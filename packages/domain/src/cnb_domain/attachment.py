"""会话附件的领域状态与不可变元数据。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AttachmentStatus(StrEnum):
    """附件从预留到消息绑定及清理的生命周期。"""

    PENDING = "pending"
    READY = "ready"
    ATTACHED = "attached"
    REJECTED = "rejected"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class Attachment:
    """对象存储内容对应的租户隔离附件元数据。"""

    id: UUID
    tenant_id: UUID
    owner_id: UUID
    conversation_id: UUID
    client_message_id: UUID
    message_id: UUID | None
    original_name: str
    content_type: str
    size_bytes: int
    sha256: str
    object_key: str
    status: AttachmentStatus
    validation_error: str | None
    created_at: datetime
    expires_at: datetime
    uploaded_at: datetime | None
    attached_at: datetime | None
    deleted_at: datetime | None
