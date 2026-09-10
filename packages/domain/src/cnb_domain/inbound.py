"""外部身份、会话线程映射与版本化入站 Envelope 的纯领域类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.channels import ChannelPlatform, MultimodalContentBlock

INBOUND_ENVELOPE_SCHEMA_VERSION = "1"


class ExternalMappingStatus(StrEnum):
    """外部路由映射是否允许参与新入站事件解析。"""

    ENABLED = "enabled"
    DISABLED = "disabled"


class ExternalConversationKind(StrEnum):
    """平台会话的稳定语义，不使用显示名称推断。"""

    DIRECT = "direct"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class ExternalIdentityMapping:
    """平台主体到本地用户的显式、Agent 级隔离映射。"""

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


@dataclass(frozen=True, slots=True)
class ExternalConversationMapping:
    """平台会话及可选线程到内部 Conversation 的稳定路由。"""

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


@dataclass(frozen=True, slots=True)
class InboundEnvelope:
    """签名验证和净化后才允许持久化及重放的入站契约。"""

    schema_version: str
    tenant_id: UUID
    agent_id: UUID
    channel_id: UUID
    platform: ChannelPlatform
    external_event_id: str
    external_subject_id: str
    user_id: UUID
    conversation_kind: ExternalConversationKind
    external_conversation_id: str
    external_thread_id: str | None
    conversation_id: UUID
    external_message_id: str
    blocks: tuple[MultimodalContentBlock, ...]
    occurred_at: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class InboundVerification:
    """由平台签名边界提供、供通用 Inbox 再校验的结果。"""

    signature_valid: bool
    payload_size_bytes: int
    received_at: datetime
