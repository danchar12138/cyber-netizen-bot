"""内部 Web 对话、Agent Run 与流式事件 API 契约。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from cnb_domain import (
    AgentRunStatus,
    ConversationStatus,
    JsonValue,
    MessageFeedbackRating,
    MessageSenderType,
    MessageStatus,
)


class DevelopmentIdentityResponse(BaseModel):
    """本地开发模式向前端公开的非敏感身份。"""

    tenant_id: UUID
    user_id: UUID
    agent_id: UUID
    user_name: str
    agent_name: str


class ConversationCreate(BaseModel):
    """创建内部 Web 会话的命令。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)


class ConversationResponse(BaseModel):
    """管理后台使用的会话摘要。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    title: str
    status: ConversationStatus
    event_sequence: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    pinned_at: datetime | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    branched_from_conversation_id: UUID | None = None
    branched_from_message_id: UUID | None = None


class ConversationUpdate(BaseModel):
    """重命名、归档或置顶会话的部分更新命令。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: ConversationStatus | None = None
    pinned: bool | None = None


class ConversationListResponse(BaseModel):
    """会话的游标分页结果。"""

    items: tuple[ConversationResponse, ...]
    next_cursor: str | None = None


class MessageResponse(BaseModel):
    """一条已持久化的会话文本消息。"""

    id: UUID
    conversation_id: UUID
    sender_type: MessageSenderType
    sender_id: UUID | None
    content: str
    status: MessageStatus
    client_message_id: UUID | None
    created_at: datetime
    updated_at: datetime
    edited_from_id: UUID | None = None


class MessageListResponse(BaseModel):
    """消息的游标分页结果。"""

    items: tuple[MessageResponse, ...]
    next_cursor: str | None = None


class MessageCreate(BaseModel):
    """带客户端幂等 ID 的用户消息命令。"""

    client_message_id: UUID
    content: str = Field(min_length=1, max_length=20_000)
    attachment_ids: tuple[UUID, ...] = Field(default=(), max_length=10)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        """拒绝空白消息，并在进入应用层前规范首尾空白。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息内容不能为空")
        return normalized


class MessageRegenerate(BaseModel):
    """带幂等 ID 的重新生成命令。"""

    client_request_id: UUID


class MessageEdit(BaseModel):
    """编辑用户消息并创建会话分支的命令。"""

    client_message_id: UUID
    content: str = Field(min_length=1, max_length=20_000)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息内容不能为空")
        return normalized


class MessageFeedbackSet(BaseModel):
    """新增或覆盖当前用户对 Agent 回复的反馈。"""

    rating: MessageFeedbackRating
    comment: str | None = Field(default=None, max_length=2_000)


class MessageFeedbackResponse(BaseModel):
    """一条不包含内部推理的消息反馈。"""

    id: UUID
    conversation_id: UUID
    message_id: UUID
    user_id: UUID
    rating: MessageFeedbackRating
    comment: str | None
    created_at: datetime
    updated_at: datetime


class MessageFeedbackListResponse(BaseModel):
    """当前用户在会话中的全部反馈。"""

    items: tuple[MessageFeedbackResponse, ...]


class MessageSearchItemResponse(BaseModel):
    """带会话摘要的消息搜索命中。"""

    conversation: ConversationResponse
    message: MessageResponse


class MessageSearchResponse(BaseModel):
    """跨会话或会话内消息搜索结果。"""

    items: tuple[MessageSearchItemResponse, ...]


class AgentRunResponse(BaseModel):
    """一次 Agent Run 的安全状态摘要。"""

    id: UUID
    conversation_id: UUID
    response_message_id: UUID
    status: AgentRunStatus
    configuration_version: int = Field(ge=0)
    persona_version: int = Field(ge=1)
    prompt_version: int = Field(ge=1)
    model_profile: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class MessageAcceptedResponse(BaseModel):
    """消息接收事务创建或复用的持久化资源。"""

    user_message: MessageResponse
    response_message: MessageResponse
    run: AgentRunResponse
    idempotent_replay: bool


class ConversationEventEnvelope(BaseModel):
    """可排序、去重和断线重放的版本化事件 Envelope。"""

    schema_version: Literal["1"] = "1"
    event_id: UUID
    conversation_id: UUID
    sequence: int = Field(ge=1)
    event_type: str
    occurred_at: datetime
    run_id: UUID | None = None
    message_id: UUID | None = None
    payload: dict[str, JsonValue]


class HeartbeatEnvelope(BaseModel):
    """不参与持久化序号推进的 WebSocket 心跳。"""

    schema_version: Literal["1"] = "1"
    event_type: Literal["system.heartbeat"] = "system.heartbeat"
    occurred_at: datetime
    last_sequence: int = Field(ge=0)
