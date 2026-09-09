"""长期记忆、来源、关系与索引治理 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from cnb_domain import (
    EpisodeStatus,
    JsonValue,
    MemoryConfirmation,
    MemoryIndexJobStatus,
    MemoryKind,
    MemoryLinkKind,
    MemorySensitivity,
    MemorySourceKind,
    MemoryStatus,
    MemoryVisibility,
    RelationshipStage,
)


class MemorySourceCreate(BaseModel):
    """创建长期记忆时提交的可追溯来源。"""

    kind: MemorySourceKind
    source_id: str = Field(min_length=1, max_length=255)
    excerpt: str | None = Field(default=None, max_length=2000)
    is_verbatim: bool = False
    occurred_at: datetime


class MemorySourceResponse(BaseModel):
    """不把推断伪装成用户原话的来源视图。"""

    id: UUID
    tenant_id: UUID
    memory_id: UUID
    kind: MemorySourceKind
    source_id: str
    excerpt: str | None
    is_verbatim: bool
    occurred_at: datetime
    created_at: datetime


class MemoryResponse(BaseModel):
    """长期记忆的管理摘要；遗忘后 content 必须为空。"""

    id: UUID
    lineage_id: UUID
    tenant_id: UUID
    agent_id: UUID
    user_id: UUID | None
    conversation_id: UUID | None
    episode_id: UUID | None
    kind: MemoryKind
    visibility: MemoryVisibility
    content: str | None
    event_at: datetime
    confidence: float
    importance: float
    emotional_weight: float
    sensitivity: MemorySensitivity
    confirmation: MemoryConfirmation
    status: MemoryStatus
    version: int
    embedding_version: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class MemoryLinkResponse(BaseModel):
    """冲突、替代、派生和相关关系。"""

    id: UUID
    tenant_id: UUID
    source_memory_id: UUID
    target_memory_id: UUID
    kind: MemoryLinkKind
    note: str | None
    created_by: UUID
    created_at: datetime


class MemoryDetailResponse(BaseModel):
    """一条记忆及其证据和版本关系。"""

    memory: MemoryResponse
    sources: tuple[MemorySourceResponse, ...]
    links: tuple[MemoryLinkResponse, ...]


class MemoryListResponse(BaseModel):
    """记忆管理列表。"""

    items: tuple[MemoryResponse, ...]


class MemoryCreate(BaseModel):
    """显式携带治理属性和至少一个来源的记忆创建命令。"""

    user_id: UUID | None = None
    conversation_id: UUID | None = None
    episode_id: UUID | None = None
    kind: MemoryKind
    visibility: MemoryVisibility = MemoryVisibility.USER
    content: str = Field(min_length=1, max_length=8000)
    event_at: datetime
    confidence: float = Field(default=0.7, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    emotional_weight: float = Field(default=0, ge=-1, le=1)
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    confirmation: MemoryConfirmation = MemoryConfirmation.UNCONFIRMED
    sources: tuple[MemorySourceCreate, ...] = Field(min_length=1)


class MemoryConfirmationCommand(BaseModel):
    """确认或争议一条生效记忆。"""

    confirmation: MemoryConfirmation


class MemoryCorrectionCommand(BaseModel):
    """创建新版本并保留旧版本的纠正命令。"""

    content: str = Field(min_length=1, max_length=8000)
    event_at: datetime
    note: str | None = Field(default=None, max_length=1000)


class MemoryConflictCommand(BaseModel):
    """建立两条记忆冲突关系的命令。"""

    target_memory_id: UUID
    note: str | None = Field(default=None, max_length=1000)


class MemoryForgetCommand(BaseModel):
    """要求显式确认的不可逆正文遗忘命令。"""

    confirmed: bool


class MemoryRecallCommand(BaseModel):
    """使用当前发布配置执行混合召回。"""

    user_id: UUID
    query: str = Field(min_length=1, max_length=8000)
    limit: int | None = Field(default=None, ge=1, le=100)


class MemoryRecallItemResponse(BaseModel):
    """带可解释评分分量的召回结果。"""

    memory: MemoryResponse
    score: float
    components: dict[str, JsonValue]


class MemoryRecallResponse(BaseModel):
    """混合召回结果列表。"""

    items: tuple[MemoryRecallItemResponse, ...]
    embedding_version: str


class EpisodeCreate(BaseModel):
    """把连续消息登记为可追溯 Episode。"""

    user_id: UUID
    conversation_id: UUID
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    started_at: datetime
    ended_at: datetime | None = None
    source_message_ids: tuple[UUID, ...] = Field(min_length=1)


class EpisodeResponse(BaseModel):
    """Episode 管理视图。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    user_id: UUID
    conversation_id: UUID
    title: str
    summary: str
    status: EpisodeStatus
    started_at: datetime
    ended_at: datetime | None
    source_message_ids: tuple[UUID, ...]
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class EpisodeListResponse(BaseModel):
    """Episode 管理列表。"""

    items: tuple[EpisodeResponse, ...]


class EpisodeCloseCommand(BaseModel):
    """关闭或标记已巩固 Episode。"""

    consolidate: bool = False


class RelationshipResponse(BaseModel):
    """Agent 与单个用户的当前关系快照。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    user_id: UUID
    stage: RelationshipStage
    affinity: float
    trust: float
    familiarity: float
    interaction_count: int
    summary: str
    boundaries: tuple[str, ...]
    version: int
    created_at: datetime
    updated_at: datetime


class RelationshipEventResponse(BaseModel):
    """不包含隐藏推理的关系变化证据。"""

    id: UUID
    tenant_id: UUID
    relationship_id: UUID
    event_type: str
    affinity_delta: float
    trust_delta: float
    familiarity_delta: float
    evidence_memory_id: UUID | None
    summary: str
    created_by: UUID
    created_at: datetime


class RelationshipDetailResponse(BaseModel):
    """关系快照和只追加事件。"""

    relationship: RelationshipResponse
    events: tuple[RelationshipEventResponse, ...]


class RelationshipEventCreate(BaseModel):
    """以显式摘要和数值变化推进关系状态。"""

    user_id: UUID
    event_type: str = Field(min_length=1, max_length=80)
    affinity_delta: float = Field(default=0, ge=-1, le=1)
    trust_delta: float = Field(default=0, ge=-1, le=1)
    familiarity_delta: float = Field(default=0, ge=-1, le=1)
    summary: str = Field(min_length=1, max_length=1000)
    boundaries: tuple[str, ...] | None = None
    evidence_memory_id: UUID | None = None


class MemoryIndexRebuildCommand(BaseModel):
    """对当前 Agent 或单个用户执行渐进索引重建。"""

    user_id: UUID | None = None
    confirmed: bool


class MemoryIndexJobResponse(BaseModel):
    """可观测的 embedding 重建任务进度。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    user_id: UUID | None
    target_embedding_version: str
    status: MemoryIndexJobStatus
    total_items: int
    processed_items: int
    error_code: str | None
    created_by: UUID
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @model_validator(mode="after")
    def validate_progress(self) -> "MemoryIndexJobResponse":
        if self.processed_items > self.total_items:
            raise ValueError("索引任务已处理数量不能超过总数")
        return self


class MemoryIndexJobListResponse(BaseModel):
    """embedding 重建任务列表。"""

    items: tuple[MemoryIndexJobResponse, ...]
