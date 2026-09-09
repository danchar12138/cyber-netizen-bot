"""会话附件上传、校验和预览 API 契约。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from cnb_domain import AttachmentStatus


class AttachmentReserve(BaseModel):
    """申请一个受约束的附件直传授权。"""

    conversation_id: UUID
    client_message_id: UUID
    original_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=160)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)


class AttachmentResponse(BaseModel):
    """不暴露对象存储凭证和内部定位信息的附件元数据。"""

    id: UUID
    conversation_id: UUID
    client_message_id: UUID
    message_id: UUID | None
    original_name: str
    content_type: str
    size_bytes: int = Field(gt=0)
    sha256: str
    status: AttachmentStatus
    validation_error: str | None
    created_at: datetime
    expires_at: datetime
    uploaded_at: datetime | None
    attached_at: datetime | None


class AttachmentUploadGrant(BaseModel):
    """浏览器执行直传所需的一次性短期信息。"""

    url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_at: datetime


class AttachmentReservationResponse(BaseModel):
    """附件预留记录及其上传授权。"""

    attachment: AttachmentResponse
    upload: AttachmentUploadGrant


class AttachmentListResponse(BaseModel):
    """会话中当前用户可见的附件。"""

    items: tuple[AttachmentResponse, ...]


class AttachmentPreviewResponse(BaseModel):
    """短期附件预览地址。"""

    attachment: AttachmentResponse
    url: str
    expires_in_seconds: int = 300
