"""MinIO 官方客户端适配器测试。"""

from datetime import timedelta
from typing import cast

import pytest
from minio import Minio
from minio.datatypes import Object as MinioObject

from cnb_infrastructure import MinioObjectStorage, Settings


class FakeMinioClient:
    """记录适配器传入参数，避免单元测试依赖真实 MinIO。"""

    def __init__(self) -> None:
        self.removed: tuple[str, str] | None = None

    def presigned_put_object(self, bucket_name: str, object_name: str, expires: timedelta) -> str:
        assert bucket_name == "cyber-netizen"
        assert object_name == "tenants/tenant/attachments/file.txt"
        assert expires == timedelta(minutes=10)
        return "http://minio.local/upload-token"

    def stat_object(self, bucket_name: str, object_name: str) -> MinioObject:
        assert bucket_name == "cyber-netizen"
        return MinioObject(
            bucket_name,
            object_name,
            size=7,
            content_type="text/plain",
            metadata={"x-amz-meta-sha256": "a" * 64},
        )

    def presigned_get_object(
        self,
        bucket_name: str,
        object_name: str,
        expires: timedelta,
        response_headers: dict[str, str],
    ) -> str:
        assert bucket_name == "cyber-netizen"
        assert object_name == "tenants/tenant/attachments/file.txt"
        assert expires == timedelta(minutes=5)
        assert (
            "filename*=UTF-8''%E8%B5%84%E6%96%99.txt"
            in response_headers["response-content-disposition"]
        )
        return "http://minio.local/download-token"

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self.removed = (bucket_name, object_name)


async def test_minio_adapter_presigns_verifies_previews_and_deletes() -> None:
    client = FakeMinioClient()
    storage = MinioObjectStorage(
        Settings(
            environment="test",
            minio_endpoint_url="http://minio.local:9000",
            minio_bucket="cyber-netizen",
        ),
        client=cast(Minio, client),
    )
    object_key = "tenants/tenant/attachments/file.txt"

    upload = await storage.presign_upload(
        object_key=object_key,
        content_type="text/plain",
        sha256="a" * 64,
        expires_seconds=600,
    )
    observed = await storage.stat_object(object_key)
    preview = await storage.presign_download(
        object_key=object_key,
        download_name="资料.txt",
        expires_seconds=300,
    )
    await storage.delete_object(object_key)

    assert upload.url == "http://minio.local/upload-token"
    assert upload.headers == {
        "Content-Type": "text/plain",
        "x-amz-meta-sha256": "a" * 64,
    }
    assert observed.size_bytes == 7
    assert observed.content_type == "text/plain"
    assert observed.sha256 == "a" * 64
    assert preview == "http://minio.local/download-token"
    assert client.removed == ("cyber-netizen", object_key)


def test_minio_adapter_rejects_endpoint_with_path() -> None:
    with pytest.raises(ValueError, match="不能包含路径"):
        MinioObjectStorage(
            Settings(environment="test", minio_endpoint_url="http://minio.local:9000/storage")
        )
