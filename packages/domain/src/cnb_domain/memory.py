"""长期记忆、来源、关系与索引任务的纯领域类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.configuration import JsonValue


class MemoryKind(StrEnum):
    """Agent 使用的六类记忆。"""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    RELATIONAL = "relational"
    AUTOBIOGRAPHICAL = "autobiographical"
    PROCEDURAL = "procedural"


class MemoryVisibility(StrEnum):
    """记忆可见范围；用户私有是默认且最严格的范围。"""

    USER = "user"
    AGENT = "agent"
    TENANT = "tenant"


class MemorySensitivity(StrEnum):
    """决定召回和后台展示边界的敏感级别。"""

    NORMAL = "normal"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class MemoryConfirmation(StrEnum):
    """区分推断、用户确认与明确争议。"""

    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"


class MemoryStatus(StrEnum):
    """记忆生命周期；历史替代和遗忘均不改写审计事实。"""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class MemorySourceKind(StrEnum):
    """可追溯的记忆来源类别。"""

    MESSAGE = "message"
    EPISODE = "episode"
    USER_STATEMENT = "user_statement"
    ADMIN_CORRECTION = "admin_correction"
    REFLECTION = "reflection"
    IMPORT = "import"


class MemoryLinkKind(StrEnum):
    """记忆之间的有向关系。"""

    RELATED_TO = "related_to"
    CONFLICTS_WITH = "conflicts_with"
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"


class EpisodeStatus(StrEnum):
    """一次情景记忆聚合的生命周期。"""

    OPEN = "open"
    CLOSED = "closed"
    CONSOLIDATED = "consolidated"


class RelationshipStage(StrEnum):
    """Agent 与单个用户之间缓慢演进的关系阶段。"""

    STRANGER = "stranger"
    ACQUAINTANCE = "acquaintance"
    FAMILIAR = "familiar"
    TRUSTED = "trusted"


class MemoryIndexJobStatus(StrEnum):
    """embedding 渐进重建任务的状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Episode:
    """由连续事件形成、可追溯到原消息的情景单元。"""

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


@dataclass(frozen=True, slots=True)
class Memory:
    """带隔离、时间、置信度和治理状态的一条长期记忆。"""

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


@dataclass(frozen=True, slots=True)
class MemorySource:
    """来源摘录只有在明确标记逐字内容时才能作为用户原话展示。"""

    id: UUID
    tenant_id: UUID
    memory_id: UUID
    kind: MemorySourceKind
    source_id: str
    excerpt: str | None
    is_verbatim: bool
    occurred_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryLink:
    """冲突、替代、派生和普通关联的显式记录。"""

    id: UUID
    tenant_id: UUID
    source_memory_id: UUID
    target_memory_id: UUID
    kind: MemoryLinkKind
    note: str | None
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryEmbedding:
    """不通过管理 API 返回的向量及其精确版本。"""

    id: UUID
    tenant_id: UUID
    memory_id: UUID
    embedding_version: str
    dimensions: int
    vector: tuple[float, ...]
    active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Relationship:
    """Agent 与用户独立、可解释且有界的关系状态。"""

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


@dataclass(frozen=True, slots=True)
class RelationshipEvent:
    """关系变化的只追加依据，不保存隐藏推理。"""

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


@dataclass(frozen=True, slots=True)
class MemoryIndexJob:
    """一次可观测、可审计的 embedding 版本重建任务。"""

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


@dataclass(frozen=True, slots=True)
class RawMemoryCandidate:
    """Repository 返回给纯认知重排器的安全候选和检索分量。"""

    memory: Memory
    full_text_score: float
    semantic_score: float
    relationship_score: float


@dataclass(frozen=True, slots=True)
class MemoryRecall:
    """进入上下文的记忆及可解释混合评分。"""

    memory: Memory
    score: float
    components: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class MemoryDetail:
    """后台查看的一条记忆及其来源和冲突链。"""

    memory: Memory
    sources: tuple[MemorySource, ...]
    links: tuple[MemoryLink, ...]


@dataclass(frozen=True, slots=True)
class RelationshipDetail:
    """关系当前状态与只追加事件。"""

    relationship: Relationship
    events: tuple[RelationshipEvent, ...]
