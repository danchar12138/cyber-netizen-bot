"""S3/MinIO 对象存储与测试内存实现。"""

import asyncio
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256 as calculate_sha256
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from mypy_boto3_s3 import S3Client
from mypy_boto3_s3.type_defs import HeadObjectOutputTypeDef

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


class S3ObjectStorage:
    """通过 boto3 为兼容 S3 的私有桶签发短期 URL。"""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client: S3Client = boto3.client(  # pyright: ignore[reportUnknownMemberType]
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def presign_upload(
        self,
        *,
        object_key: str,
        content_type: str,
        sha256: str,
        expires_seconds: int,
    ) -> UploadGrant:
        checksum = b64encode(bytes.fromhex(sha256)).decode("ascii")
        parameters = {
            "Bucket": self._bucket,
            "Key": object_key,
            "ContentType": content_type,
            "Metadata": {"sha256": sha256},
            "ChecksumSHA256": checksum,
        }
        url = await asyncio.to_thread(
            self._client.generate_presigned_url,
            "put_object",
            Params=parameters,
            ExpiresIn=expires_seconds,
            HttpMethod="PUT",
        )
        return UploadGrant(
            url=url,
            method="PUT",
            headers={
                "Content-Type": content_type,
                "x-amz-meta-sha256": sha256,
                "x-amz-checksum-sha256": checksum,
            },
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_seconds),
        )

    async def stat_object(self, object_key: str) -> StoredObjectInfo:
        try:
            raw: HeadObjectOutputTypeDef = await asyncio.to_thread(
                self._client.head_object, Bucket=self._bucket, Key=object_key
            )
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFoundError(object_key) from error
            raise
        metadata = raw.get("Metadata", {})
        digest = metadata.get("sha256")
        return StoredObjectInfo(
            size_bytes=int(raw["ContentLength"]),
            content_type=str(raw.get("ContentType", "application/octet-stream")),
            sha256=str(digest) if digest else None,
        )

    async def presign_download(
        self, *, object_key: str, download_name: str, expires_seconds: int
    ) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": object_key,
                "ResponseContentDisposition": self._content_disposition(download_name),
            },
            ExpiresIn=expires_seconds,
            HttpMethod="GET",
        )

    async def delete_object(self, object_key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=object_key)

    @staticmethod
    def _content_disposition(name: str) -> str:
        ascii_name = "".join(character if character.isascii() else "_" for character in name)
        ascii_name = ascii_name.replace('"', "_") or "attachment"
        return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
