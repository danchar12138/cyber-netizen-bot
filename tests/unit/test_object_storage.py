"""MinIO 官方客户端适配器测试。"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from typing import cast
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from minio import Minio
from minio.datatypes import Object as MinioObject

from cnb_application import ObjectInspectionError
from cnb_infrastructure import (
    MemoryObjectStorage,
    MinioObjectStorage,
    Settings,
)


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

    def get_object(self, bucket_name: str, object_name: str) -> "FakeObjectResponse":
        assert bucket_name == "cyber-netizen"
        assert object_name == "tenants/tenant/attachments/file.txt"
        return FakeObjectResponse(b"payload")

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

    def list_objects(
        self,
        bucket_name: str,
        *,
        prefix: str,
        recursive: bool,
    ) -> Iterator[MinioObject]:
        assert bucket_name == "cyber-netizen"
        assert prefix == "tenants/tenant/"
        assert recursive is True
        for index in range(2):
            yield MinioObject(
                bucket_name,
                f"{prefix}attachments/{index}.txt",
                last_modified=datetime(2026, 9, 10, tzinfo=UTC),
                size=index + 1,
            )


class FakeObjectResponse:
    """模拟 urllib3 流并记录资源释放。"""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self.closed = False

    def stream(self, amt: int) -> Iterator[bytes]:
        yield from (
            self._content[index : index + amt] for index in range(0, len(self._content), amt)
        )

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.closed = True


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
    inspected = await storage.inspect_object(object_key, max_bytes=7)
    content = await storage.read_object(object_key, max_bytes=7)
    preview = await storage.presign_download(
        object_key=object_key,
        download_name="资料.txt",
        expires_seconds=300,
    )
    listed = await storage.list_objects(prefix="tenants/tenant/", limit=1)
    await storage.delete_object(object_key)

    assert upload.url == "http://minio.local/upload-token"
    assert upload.headers == {
        "Content-Type": "text/plain",
        "x-amz-meta-sha256": "a" * 64,
    }
    assert observed.size_bytes == 7
    assert observed.content_type == "text/plain"
    assert observed.sha256 == "a" * 64
    assert inspected.sha256 == sha256(b"payload").hexdigest()
    assert inspected.metadata_sha256 == "a" * 64
    assert inspected.detected_content_type == "text/plain"
    assert content.data == b"payload"
    assert content.content_type == "text/plain"
    assert content.sha256 == sha256(b"payload").hexdigest()
    assert preview == "http://minio.local/download-token"
    assert len(listed) == 1
    assert listed[0].object_key == "tenants/tenant/attachments/0.txt"
    assert client.removed == ("cyber-netizen", object_key)


def test_minio_adapter_rejects_endpoint_with_path() -> None:
    with pytest.raises(ValueError, match="不能包含路径"):
        MinioObjectStorage(
            Settings(environment="test", minio_endpoint_url="http://minio.local:9000/storage")
        )


@pytest.mark.parametrize(
    ("content_type", "content"),
    (
        ("application/json", b'{"unfinished":'),
        ("text/plain", b"visible\x00binary"),
        ("image/png", b"\x89PNG\r\n\x1a\nnot-an-image"),
        ("application/pdf", b"%PDF-1.7\n1 0 obj << /Java#53cript 2 0 R >>"),
    ),
)
async def test_object_inspection_rejects_spoofed_or_active_content(
    content_type: str,
    content: bytes,
) -> None:
    storage = MemoryObjectStorage()
    storage.put_for_test(
        object_key="unsafe-object",
        content=content,
        content_type=content_type,
    )

    with pytest.raises(ObjectInspectionError):
        await storage.inspect_object("unsafe-object", max_bytes=len(content))


async def test_docx_inspection_rejects_external_relationships() -> None:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
        archive.writestr(
            "word/_rels/document.xml.rels",
            "<Relationships><Relationship TargetMode = 'External' "
            "Target='https://evil.example.test/payload'/></Relationships>",
        )
    content = output.getvalue()
    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    storage = MemoryObjectStorage()
    storage.put_for_test(
        object_key="unsafe.docx",
        content=content,
        content_type=content_type,
    )

    with pytest.raises(ObjectInspectionError, match="外部关系"):
        await storage.inspect_object("unsafe.docx", max_bytes=len(content))


async def test_private_object_read_enforces_explicit_byte_limit() -> None:
    storage = MemoryObjectStorage()
    storage.put_for_test(
        object_key="bounded.txt",
        content=b"bounded content",
        content_type="text/plain",
    )

    with pytest.raises(ObjectInspectionError, match="模型输入大小上限"):
        await storage.read_object("bounded.txt", max_bytes=4)
