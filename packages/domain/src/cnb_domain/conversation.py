"""最小对话闭环所需的领域实体与生命周期状态。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.configuration import JsonValue


class EntityStatus(StrEnum):
    """租户、用户与 Agent 的通用启停状态。"""

    ACTIVE = "active"
    DISABLED = "disabled"


class ConversationStatus(StrEnum):
    """会话生命周期状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageSenderType(StrEnum):
    """消息发送主体类型。"""

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class MessageStatus(StrEnum):
    """消息从接收到最终落盘的处理状态。"""

    RECEIVED = "received"
    PROCESSING = "processing"
    STREAMING = "streaming"
    COMPLETED = "completed"
    SUPPRESSED = "suppressed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AgentRunStatus(StrEnum):
    """单次 Agent Run 的执行状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class MessageFeedbackRating(StrEnum):
    """用户对单条 Agent 回复的轻量反馈。"""

    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclass(frozen=True, slots=True)
class DevelopmentIdentity:
    """本地开发环境使用的稳定身份集合。"""

    tenant_id: UUID
    user_id: UUID
    agent_id: UUID
    user_name: str
    agent_name: str


@dataclass(frozen=True, slots=True)
class Conversation:
    """一个用户与指定 Agent 之间的持久化会话。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    title: str
    status: ConversationStatus
    created_by: UUID
    event_sequence: int
    created_at: datetime
    updated_at: datetime
    pinned_at: datetime | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    branched_from_conversation_id: UUID | None = None
    branched_from_message_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Message:
    """会话中的一条文本消息。"""

    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    sender_type: MessageSenderType
    sender_id: UUID | None
    content: str
    status: MessageStatus
    client_message_id: UUID | None
    created_at: datetime
    updated_at: datetime
    edited_from_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AgentRun:
    """由用户消息触发且可回放的单次认知运行。"""

    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    agent_id: UUID
    trigger_message_id: UUID
    response_message_id: UUID
    status: AgentRunStatus
    configuration_version: int
    persona_version: int
    prompt_version: int
    policy_version: int
    model_route_version: int
    model_profile: str
    input_tokens: int | None
    output_tokens: int | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ConversationEvent:
    """支持断线恢复的持久化有序会话事件。"""

    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, JsonValue]
    run_id: UUID | None
    message_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PendingAgentRun:
    """消息接收事务创建的用户消息、回复占位与运行快照。"""

    conversation: Conversation
    trigger_message: Message
    response_message: Message
    run: AgentRun
    created: bool


@dataclass(frozen=True, slots=True)
class MessageFeedback:
    """当前用户对一条 Agent 消息的可更新反馈。"""

    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    message_id: UUID
    user_id: UUID
    rating: MessageFeedbackRating
    comment: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MessageSearchResult:
    """跨会话全文搜索结果。"""

    conversation: Conversation
    message: Message
