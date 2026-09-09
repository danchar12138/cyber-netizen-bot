"""MinIO 对象存储与测试内存实现。"""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256 as calculate_sha256
from urllib.parse import quote, urlsplit

from minio import Minio
from minio.datatypes import Object as MinioObject
from minio.error import S3Error as MinioProtocolError

from cnb_application import ObjectNotFoundError, StoredObjectInfo, UploadGrant
from cnb_infrastructure.settings import Settings


class MemoryObjectStorage:
    """记录对象字节并产生虚拟授权，便于无基础设施测试。"""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str, str]] = {}

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
            content, content_type, digest = self._objects[object_key]
        except KeyError as error:
            raise ObjectNotFoundError(object_key) from error
        return StoredObjectInfo(size_bytes=len(content), content_type=content_type, sha256=digest)

    async def presign_download(
        self, *, object_key: str, download_name: str, expires_seconds: int
    ) -> str:
        del expires_seconds
        if object_key not in self._objects:
            raise ObjectNotFoundError(object_key)
        return f"memory://download/{quote(object_key)}?name={quote(download_name)}"

    async def delete_object(self, object_key: str) -> None:
        self._objects.pop(object_key, None)

    def put_for_test(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
        sha256: str | None = None,
    ) -> None:
        """模拟客户端完成一次携带摘要元数据的 PUT。"""
        digest = sha256 or calculate_sha256(content).hexdigest()
        self._objects[object_key] = (content, content_type, digest)


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

    async def delete_object(self, object_key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, self._bucket, object_key)

    @staticmethod
    def _content_disposition(name: str) -> str:
        ascii_name = "".join(character if character.isascii() else "_" for character in name)
        ascii_name = ascii_name.replace('"', "_") or "attachment"
        return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
