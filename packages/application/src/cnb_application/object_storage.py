"""MinIO 对象存储的应用层端口与不含凭证的数据对象。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class ObjectNotFoundError(LookupError):
    """预留对象尚未上传到对象存储时抛出。"""


class ObjectInspectionError(ValueError):
    """对象内容无法在安全限制内完成类型、摘要或结构检查。"""


@dataclass(frozen=True, slots=True)
class StoredObjectInfo:
    """完成上传校验所需的可信对象元数据。"""

    size_bytes: int
    content_type: str
    sha256: str | None
    metadata_sha256: str | None = None
    detected_content_type: str | None = None


@dataclass(frozen=True, slots=True)
class StoredObjectContent:
    """在显式字节上限内从私有桶读取并重新计算摘要的对象内容。"""

    data: bytes
    content_type: str
    sha256: str


@dataclass(frozen=True, slots=True)
class StoredObjectEntry:
    """对象清单中的最小安全元数据；不包含内容或下载凭证。"""

    object_key: str
    size_bytes: int
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class UploadGrant:
    """短期、单对象、约束请求头的直传授权。"""

    url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime


class ObjectStorage(Protocol):
    """MinIO 对象存储所需的最小能力。"""

    async def presign_upload(
        self,
        *,
        object_key: str,
        content_type: str,
        sha256: str,
        expires_seconds: int,
    ) -> UploadGrant: ...

    async def stat_object(self, object_key: str) -> StoredObjectInfo: ...

    async def inspect_object(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> StoredObjectInfo: ...

    async def read_object(self, object_key: str, *, max_bytes: int) -> StoredObjectContent: ...

    async def presign_download(
        self, *, object_key: str, download_name: str, expires_seconds: int
    ) -> str: ...

    async def delete_object(self, object_key: str) -> None: ...

    async def list_objects(self, *, prefix: str, limit: int) -> tuple[StoredObjectEntry, ...]: ...
