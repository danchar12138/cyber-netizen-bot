"""可恢复异步任务、事务事件与主动行为调度的纯领域类型。"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.configuration import JsonValue


class BackgroundJobKind(StrEnum):
    """Worker 可执行的稳定任务种类。"""

    REFLECTION = "reflection"
    EPISODE_CONSOLIDATION = "episode_consolidation"
    MEMORY_EXTRACTION = "memory_extraction"
    EMBEDDING_REBUILD = "embedding_rebuild"
    RELATIONSHIP_UPDATE = "relationship_update"
    SCHEDULED_ACTION = "scheduled_action"
    INBOUND_MESSAGE = "inbound_message"


class BackgroundJobStatus(StrEnum):
    """后台任务从待执行到终态的生命周期。"""

    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELED = "canceled"


class InboxEventStatus(StrEnum):
    """入站事件的幂等消费状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"
    CANCELED = "canceled"


class OutboxEventStatus(StrEnum):
    """事务 Outbox 从待发布到终态的生命周期。"""

    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"
    CANCELED = "canceled"


class JobAttemptStatus(StrEnum):
    """单次 Worker 尝试的结果。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"


class ScheduledActionKind(StrEnum):
    """可由后台创建和治理的主动行为。"""

    FOLLOW_UP = "follow_up"
    PROACTIVE_MESSAGE = "proactive_message"
    REFLECTION = "reflection"


class ScheduledActionStatus(StrEnum):
    """定时行为经过策略评估后的状态。"""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    SUPPRESSED = "suppressed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class InboxEvent:
    """来自 API 或 Adapter 的去重事件；载荷不得包含密钥或隐藏思维。"""

    id: UUID
    tenant_id: UUID
    event_key: str
    event_type: str
    payload: dict[str, JsonValue]
    status: InboxEventStatus
    job_id: UUID
    received_at: datetime
    processed_at: datetime | None
    last_error_code: str | None
    agent_id: UUID | None = None
    channel_id: UUID | None = None
    schema_version: str | None = None
    platform: str | None = None
    external_event_digest: str | None = None
    external_subject_digest: str | None = None
    external_conversation_digest: str | None = None
    external_thread_digest: str | None = None
    external_message_digest: str | None = None
    user_id: UUID | None = None
    conversation_id: UUID | None = None
    content_kinds: tuple[str, ...] = ()
    content_block_count: int = 0


@dataclass(frozen=True, slots=True)
class BackgroundJob:
    """PostgreSQL 中可恢复、可取消和可安全重放的任务真相。"""

    id: UUID
    tenant_id: UUID
    kind: BackgroundJobKind
    queue: str
    status: BackgroundJobStatus
    payload: dict[str, JsonValue]
    deduplication_key: str
    source_inbox_id: UUID | None
    correlation_id: str | None
    attempt_count: int
    max_attempts: int
    lease_seconds: int
    retry_base_seconds: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    cancel_requested_at: datetime | None
    last_error_code: str | None
    last_error_summary: str | None
    result_summary: dict[str, JsonValue]
    replayed_from_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """与任务同事务创建、由发布器至少一次投递的事件。"""

    id: UUID
    tenant_id: UUID
    job_id: UUID
    event_type: str
    payload: dict[str, JsonValue]
    status: OutboxEventStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    published_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class JobAttempt:
    """不保存业务正文的单次执行审计。"""

    id: UUID
    tenant_id: UUID
    job_id: UUID
    attempt_number: int
    status: JobAttemptStatus
    worker_id: str
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None
    error_summary: str | None


@dataclass(frozen=True, slots=True)
class JobClaim:
    """Worker 成功取得任务租约后的执行快照。"""

    job: BackgroundJob
    attempt: JobAttempt


@dataclass(frozen=True, slots=True)
class ScheduledAction:
    """尚未产生外部副作用的定时主动行为候选。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    user_id: UUID
    conversation_id: UUID | None
    kind: ScheduledActionKind
    status: ScheduledActionStatus
    scheduled_for: datetime
    expires_at: datetime | None
    idempotency_key: str
    reason: str
    payload: dict[str, JsonValue]
    score: float | None
    social_cost: int
    decision_reasons: tuple[str, ...]
    job_id: UUID | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class SocialBudgetUsage:
    """一次主动行为对单个 Agent/用户每日预算的幂等占用。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    user_id: UUID
    budget_date: date
    scheduled_action_id: UUID
    cost: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkerHeartbeat:
    """管理后台用于判断 Worker 新鲜度的进程心跳。"""

    worker_id: str
    queues: tuple[str, ...]
    current_job_id: UUID | None
    started_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class TaskCounts:
    """租户任务与死信的安全聚合。"""

    pending: int
    running: int
    retrying: int
    dead_letters: int
    scheduled: int
