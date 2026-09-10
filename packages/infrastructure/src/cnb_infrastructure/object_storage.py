"""MinIO 对象存储与测试内存实现。"""

import asyncio
import codecs
import json
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256 as calculate_sha256
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, cast
from urllib.parse import quote, urlsplit
from zipfile import BadZipFile, ZipFile

from minio import Minio
from minio.datatypes import Object as MinioObject
from minio.error import S3Error as MinioProtocolError
from PIL import Image, UnidentifiedImageError

from cnb_application import (
    ObjectInspectionError,
    ObjectNotFoundError,
    StoredObjectContent,
    StoredObjectEntry,
    StoredObjectInfo,
    UploadGrant,
)
from cnb_infrastructure.settings import Settings


class MemoryObjectStorage:
    """记录对象字节并产生虚拟授权，便于无基础设施测试。"""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str, str, datetime]] = {}

    async def presign_upload(
        self,
        *,
        object_key: str,
        content_type: str,
        sha256: str,
        expires_seconds: int,
    ) -> UploadGrant:
        return UploadGrant(
            url=f"memory://upload/{quote(object_key)}",
            method="PUT",
            headers={"Content-Type": content_type, "x-amz-meta-sha256": sha256},
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_seconds),
        )

    async def stat_object(self, object_key: str) -> StoredObjectInfo:
        try:
            content, content_type, digest, _ = self._objects[object_key]
        except KeyError as error:
            raise ObjectNotFoundError(object_key) from error
        return StoredObjectInfo(size_bytes=len(content), content_type=content_type, sha256=digest)

    async def inspect_object(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> StoredObjectInfo:
        try:
            content, content_type, metadata_digest, _ = self._objects[object_key]
        except KeyError as error:
            raise ObjectNotFoundError(object_key) from error
        if len(content) > max_bytes:
            raise ObjectInspectionError("对象超过预留大小")
        with SpooledTemporaryFile(max_size=8 * 1024 * 1024) as stream:
            stream.write(content)
            detected = _inspect_content(cast(BinaryIO, stream), content_type, len(content))
        return StoredObjectInfo(
            size_bytes=len(content),
            content_type=content_type,
            sha256=calculate_sha256(content).hexdigest(),
            metadata_sha256=metadata_digest,
            detected_content_type=detected,
        )

    async def presign_download(
        self, *, object_key: str, download_name: str, expires_seconds: int
    ) -> str:
        del expires_seconds
        if object_key not in self._objects:
            raise ObjectNotFoundError(object_key)
        return f"memory://download/{quote(object_key)}?name={quote(download_name)}"

    async def read_object(self, object_key: str, *, max_bytes: int) -> StoredObjectContent:
        try:
            content, content_type, _, _ = self._objects[object_key]
        except KeyError as error:
            raise ObjectNotFoundError(object_key) from error
        if len(content) > max_bytes:
            raise ObjectInspectionError("对象超过模型输入大小上限")
        return StoredObjectContent(
            data=content,
            content_type=content_type,
            sha256=calculate_sha256(content).hexdigest(),
        )

    async def delete_object(self, object_key: str) -> None:
        self._objects.pop(object_key, None)

    async def list_objects(self, *, prefix: str, limit: int) -> tuple[StoredObjectEntry, ...]:
        rows = (
            StoredObjectEntry(
                object_key=object_key,
                size_bytes=len(content),
                last_modified=created_at,
            )
            for object_key, (content, _, _, created_at) in self._objects.items()
            if object_key.startswith(prefix)
        )
        return tuple(sorted(rows, key=lambda item: item.object_key)[:limit])

    def put_for_test(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
        sha256: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        """模拟客户端完成一次携带摘要元数据的 PUT。"""
        digest = sha256 or calculate_sha256(content).hexdigest()
        self._objects[object_key] = (
            content,
            content_type,
            digest,
            created_at or datetime.now(UTC),
        )


class MinioObjectStorage:
    """通过 MinIO 官方客户端管理私有桶并签发短期上传、预览地址。"""

    def __init__(self, settings: Settings, *, client: Minio | None = None) -> None:
        endpoint = urlsplit(settings.minio_endpoint_url)
        if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
            raise ValueError("MinIO 地址必须是完整的 http 或 https URL")
        if endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment:
            raise ValueError("MinIO 地址不能包含路径、查询参数或片段")
        self._bucket = settings.minio_bucket
        self._client = client or Minio(
            endpoint.netloc,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=endpoint.scheme == "https",
        )

    async def presign_upload(
        self,
        *,
        object_key: str,
        content_type: str,
        sha256: str,
        expires_seconds: int,
    ) -> UploadGrant:
        url = await asyncio.to_thread(
            self._client.presigned_put_object,
            self._bucket,
            object_key,
            expires=timedelta(seconds=expires_seconds),
        )
        return UploadGrant(
            url=url,
            method="PUT",
            headers={
                "Content-Type": content_type,
                "x-amz-meta-sha256": sha256,
            },
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_seconds),
        )

    async def stat_object(self, object_key: str) -> StoredObjectInfo:
        try:
            raw: MinioObject = await asyncio.to_thread(
                self._client.stat_object, self._bucket, object_key
            )
        except MinioProtocolError as error:
            code = error.code
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFoundError(object_key) from error
            raise
        metadata = raw.metadata or {}
        digest = (
            metadata.get("x-amz-meta-sha256")
            or metadata.get("X-Amz-Meta-Sha256")
            or metadata.get("sha256")
        )
        if raw.size is None:
            raise RuntimeError("MinIO 未返回对象大小")
        return StoredObjectInfo(
            size_bytes=raw.size,
            content_type=raw.content_type or "application/octet-stream",
            sha256=str(digest) if digest else None,
        )

    async def inspect_object(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> StoredObjectInfo:
        """流式读取私有对象，计算服务端摘要并验证实际文件结构。"""
        return await asyncio.to_thread(self._inspect_object_sync, object_key, max_bytes)

    def _inspect_object_sync(self, object_key: str, max_bytes: int) -> StoredObjectInfo:
        try:
            raw: MinioObject = self._client.stat_object(self._bucket, object_key)
            response = cast(_ObjectResponse, self._client.get_object(self._bucket, object_key))
        except MinioProtocolError as error:
            if error.code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFoundError(object_key) from error
            raise
        metadata = raw.metadata or {}
        metadata_digest = (
            metadata.get("x-amz-meta-sha256")
            or metadata.get("X-Amz-Meta-Sha256")
            or metadata.get("sha256")
        )
        digest = calculate_sha256()
        size = 0
        try:
            with SpooledTemporaryFile(max_size=8 * 1024 * 1024) as stream:
                for chunk in response.stream(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ObjectInspectionError("对象超过预留大小")
                    digest.update(chunk)
                    stream.write(chunk)
                declared_type = raw.content_type or "application/octet-stream"
                detected_type = _inspect_content(cast(BinaryIO, stream), declared_type, size)
        finally:
            response.close()
            response.release_conn()
        return StoredObjectInfo(
            size_bytes=size,
            content_type=raw.content_type or "application/octet-stream",
            sha256=digest.hexdigest(),
            metadata_sha256=str(metadata_digest) if metadata_digest else None,
            detected_content_type=detected_type,
        )

    async def presign_download(
        self, *, object_key: str, download_name: str, expires_seconds: int
    ) -> str:
        return await asyncio.to_thread(
            self._client.presigned_get_object,
            self._bucket,
            object_key,
            expires=timedelta(seconds=expires_seconds),
            response_headers={
                "response-content-disposition": self._content_disposition(download_name),
            },
        )

    async def read_object(self, object_key: str, *, max_bytes: int) -> StoredObjectContent:
        """在显式上限内流式读取私有对象，不签发可外传的对象地址。"""
        return await asyncio.to_thread(self._read_object_sync, object_key, max_bytes)

    def _read_object_sync(self, object_key: str, max_bytes: int) -> StoredObjectContent:
        try:
            raw: MinioObject = self._client.stat_object(self._bucket, object_key)
            response = cast(_ObjectResponse, self._client.get_object(self._bucket, object_key))
        except MinioProtocolError as error:
            if error.code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFoundError(object_key) from error
            raise
        content = bytearray()
        digest = calculate_sha256()
        try:
            for chunk in response.stream(64 * 1024):
                if len(content) + len(chunk) > max_bytes:
                    raise ObjectInspectionError("对象超过模型输入大小上限")
                content.extend(chunk)
                digest.update(chunk)
        finally:
            response.close()
            response.release_conn()
        return StoredObjectContent(
            data=bytes(content),
            content_type=raw.content_type or "application/octet-stream",
            sha256=digest.hexdigest(),
        )

    async def delete_object(self, object_key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, self._bucket, object_key)

    async def list_objects(self, *, prefix: str, limit: int) -> tuple[StoredObjectEntry, ...]:
        return await asyncio.to_thread(self._list_objects_sync, prefix, limit)

    def _list_objects_sync(self, prefix: str, limit: int) -> tuple[StoredObjectEntry, ...]:
        rows: list[StoredObjectEntry] = []
        for item in self._client.list_objects(self._bucket, prefix=prefix, recursive=True):
            if item.object_name is None or item.last_modified is None or item.size is None:
                continue
            rows.append(
                StoredObjectEntry(
                    object_key=item.object_name,
                    size_bytes=item.size,
                    last_modified=item.last_modified,
                )
            )
            if len(rows) >= limit:
                break
        return tuple(rows)

    @staticmethod
    def _content_disposition(name: str) -> str:
        ascii_name = "".join(character if character.isascii() else "_" for character in name)
        ascii_name = ascii_name.replace('"', "_") or "attachment"
        suffix = name.casefold().rsplit(".", maxsplit=1)[-1] if "." in name else ""
        disposition = "inline" if suffix in {"png", "jpg", "jpeg", "webp", "gif"} else "attachment"
        return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


class _ObjectResponse(Protocol):
    """MinIO get_object 返回值中安全检查所需的最小同步接口。"""

    def stream(self, amt: int) -> Iterator[bytes]: ...

    def close(self) -> None: ...

    def release_conn(self) -> None: ...


def _inspect_content(stream: BinaryIO, declared_type: str, size: int) -> str:
    """验证魔数、可解析性和压缩包边界后返回可信媒体类型。"""
    if size <= 0:
        raise ObjectInspectionError("对象内容为空")
    normalized = declared_type.casefold().split(";", maxsplit=1)[0]
    stream.seek(0)
    signature = stream.read(16)
    stream.seek(0)
    image_signatures = {
        "image/png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": signature.startswith(b"\xff\xd8\xff"),
        "image/gif": signature.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": signature.startswith(b"RIFF") and signature[8:12] == b"WEBP",
    }
    if normalized in image_signatures:
        if not image_signatures[normalized]:
            raise ObjectInspectionError("图片魔数与声明类型不一致")
        _verify_image(stream, normalized)
        return normalized
    if normalized == "application/pdf":
        if not signature.startswith(b"%PDF-"):
            raise ObjectInspectionError("PDF 魔数无效")
        _reject_active_pdf(stream)
        return normalized
    if normalized == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        _verify_docx(stream, size)
        return normalized
    if normalized in {"text/plain", "text/markdown", "text/csv", "application/json"}:
        _verify_utf8_text(stream)
        if normalized == "application/json":
            stream.seek(0)
            try:
                json.load(codecs.getreader("utf-8")(stream))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ObjectInspectionError("JSON 文档结构无效") from error
        return normalized
    raise ObjectInspectionError("对象媒体类型不在安全检查范围内")


def _verify_image(stream: BinaryIO, content_type: str) -> None:
    expected_formats = {
        "image/png": "PNG",
        "image/jpeg": "JPEG",
        "image/gif": "GIF",
        "image/webp": "WEBP",
    }
    stream.seek(0)
    try:
        with Image.open(stream) as image:
            if image.format != expected_formats[content_type]:
                raise ObjectInspectionError("图片解码格式与声明类型不一致")
            if image.width * image.height > 40_000_000:
                raise ObjectInspectionError("图片像素数量超过安全上限")
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ObjectInspectionError("图片内容无法安全解码") from error


def _reject_active_pdf(stream: BinaryIO) -> None:
    stream.seek(0)
    carry = b""
    while chunk := stream.read(64 * 1024):
        sample = carry + chunk
        normalized = re.sub(
            rb"#([0-9a-fA-F]{2})",
            lambda match: bytes((int(match.group(1), 16),)),
            sample,
        )
        if re.search(
            rb"/(?:JavaScript|JS|Launch|EmbeddedFile|RichMedia)\b",
            normalized,
            flags=re.IGNORECASE,
        ):
            raise ObjectInspectionError("PDF 包含不允许的主动内容")
        carry = sample[-32:]


def _verify_docx(stream: BinaryIO, compressed_size: int) -> None:
    stream.seek(0)
    try:
        with ZipFile(stream) as archive:
            members = archive.infolist()
            names = {item.filename for item in members}
            if len(members) > 2_048:
                raise ObjectInspectionError("DOCX 文件项数量超过安全上限")
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ObjectInspectionError("对象不是有效 DOCX 文档")
            total_uncompressed = 0
            for member in members:
                path_parts = member.filename.replace("\\", "/").split("/")
                if member.flag_bits & 0x1 or ".." in path_parts or member.filename.startswith("/"):
                    raise ObjectInspectionError("DOCX 包含不安全文件项")
                total_uncompressed += member.file_size
                if member.filename.casefold().endswith("vbaproject.bin"):
                    raise ObjectInspectionError("DOCX 不允许包含宏")
                if member.filename.endswith(".rels"):
                    if member.file_size > 1024 * 1024:
                        raise ObjectInspectionError("DOCX 关系文件超过安全上限")
                    relationship = archive.read(member)
                    try:
                        root = ElementTree.fromstring(relationship)
                    except ElementTree.ParseError as error:
                        raise ObjectInspectionError("DOCX 关系文件结构无效") from error
                    for element in root.iter():
                        target_mode = next(
                            (
                                value
                                for key, value in element.attrib.items()
                                if key.rsplit("}", maxsplit=1)[-1].casefold() == "targetmode"
                            ),
                            None,
                        )
                        if target_mode is not None and target_mode.casefold() == "external":
                            raise ObjectInspectionError("DOCX 不允许包含外部关系")
            if total_uncompressed > 50 * 1024 * 1024 or total_uncompressed > compressed_size * 100:
                raise ObjectInspectionError("DOCX 解压规模超过安全上限")
    except BadZipFile as error:
        raise ObjectInspectionError("DOCX 压缩结构无效") from error


def _verify_utf8_text(stream: BinaryIO) -> None:
    stream.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        while chunk := stream.read(64 * 1024):
            if b"\x00" in chunk:
                raise ObjectInspectionError("文本文件包含二进制空字节")
            decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise ObjectInspectionError("文本文件不是有效 UTF-8") from error
