"""外部身份、线程路由和可重放 Inbox 管理契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from cnb_domain import (
    AgentRunStatus,
    BackgroundJobStatus,
    ChannelPlatform,
    ExternalConversationKind,
    ExternalMappingStatus,
    InboxEventStatus,
    JsonValue,
)


class ExternalIdentityMappingCreate(BaseModel):
    """显式绑定平台主体与本地用户。"""

    channel_id: UUID
    external_subject_id: str = Field(min_length=1, max_length=255)
    user_id: UUID


class ExternalIdentityMappingResponse(BaseModel):
    """可管理的身份映射；外部 ID 仅在权限保护的管理接口返回。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    channel_id: UUID
    platform: ChannelPlatform
    external_subject_id: str
    user_id: UUID
    status: ExternalMappingStatus
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ExternalIdentityMappingListResponse(BaseModel):
    items: tuple[ExternalIdentityMappingResponse, ...]


class ExternalConversationMappingCreate(BaseModel):
    """显式绑定平台会话/线程与内部 Conversation。"""

    channel_id: UUID
    user_id: UUID
    kind: ExternalConversationKind
    external_conversation_id: str = Field(min_length=1, max_length=255)
    external_thread_id: str | None = Field(default=None, max_length=255)
    conversation_id: UUID


class ExternalConversationMappingResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    channel_id: UUID
    platform: ChannelPlatform
    kind: ExternalConversationKind
    external_conversation_id: str
    external_thread_id: str | None
    conversation_id: UUID
    status: ExternalMappingStatus
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ExternalConversationMappingListResponse(BaseModel):
    items: tuple[ExternalConversationMappingResponse, ...]


class ExternalMappingStatusCommand(BaseModel):
    status: ExternalMappingStatus
    confirmed: bool = False


class InboundSimulationCommand(BaseModel):
    """仅供内部 Web 与管理联调；不是外部平台 Webhook。"""

    payload: dict[str, JsonValue]
    signature_valid: bool = True
    payload_size_bytes: int = Field(ge=1, le=10 * 1024 * 1024)
    received_at: datetime


class InboundAcceptanceResponse(BaseModel):
    inbox_id: UUID
    job_id: UUID
    created: bool
    status: InboxEventStatus
    schema_version: str


class InboxEventResponse(BaseModel):
    """不展示 Envelope 正文、原始外部 ID、对象键或凭证的诊断摘要。"""

    id: UUID
    agent_id: UUID
    channel_id: UUID
    schema_version: str
    platform: ChannelPlatform
    event_type: str
    status: InboxEventStatus
    job_id: UUID
    job_status: BackgroundJobStatus | None = None
    external_event_digest: str
    external_subject_digest: str
    external_conversation_digest: str
    external_thread_digest: str | None
    external_message_digest: str
    user_id: UUID
    conversation_id: UUID
    content_kinds: tuple[str, ...]
    content_block_count: int = Field(ge=0)
    received_at: datetime
    processed_at: datetime | None
    last_error_code: str | None
    message_id: UUID | None = None
    run_id: UUID | None = None
    run_status: AgentRunStatus | None = None
    idempotent_replay: bool | None = None
    execution_status: str | None = None


class InboxEventListResponse(BaseModel):
    items: tuple[InboxEventResponse, ...]


class InboxReplayCommand(BaseModel):
    confirmed: bool = False
    reason: str = Field(min_length=1, max_length=500)


class InboxReplayResponse(BaseModel):
    job_id: UUID
    status: BackgroundJobStatus
    replayed_from_id: UUID
