"""后台任务、尝试、Worker 与主动行为管理 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from cnb_domain import (
    BackgroundJobKind,
    BackgroundJobStatus,
    JobAttemptStatus,
    JsonValue,
    ScheduledActionKind,
    ScheduledActionStatus,
)


class BackgroundJobResponse(BaseModel):
    """不返回原始业务载荷的后台任务安全摘要。"""

    id: UUID
    tenant_id: UUID
    kind: BackgroundJobKind
    queue: str
    status: BackgroundJobStatus
    payload_keys: tuple[str, ...]
    deduplication_key: str
    correlation_id: str | None
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1, le=20)
    lease_seconds: int = Field(ge=1, le=86400)
    retry_base_seconds: int = Field(ge=1, le=86400)
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


class JobAttemptResponse(BaseModel):
    """单次执行尝试的安全元数据。"""

    id: UUID
    job_id: UUID
    attempt_number: int = Field(ge=1)
    status: JobAttemptStatus
    worker_id: str
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None
    error_summary: str | None


class BackgroundJobDetailResponse(BaseModel):
    """任务摘要和按序尝试记录。"""

    job: BackgroundJobResponse
    attempts: tuple[JobAttemptResponse, ...]


class BackgroundJobListResponse(BaseModel):
    """任务列表。"""

    items: tuple[BackgroundJobResponse, ...]


class TaskCancelCommand(BaseModel):
    """高影响取消操作必须明确确认。"""

    confirmed: bool = False


class TaskReplayCommand(BaseModel):
    """死信安全重放命令。"""

    confirmed: bool = False
    reason: str = Field(min_length=1, max_length=500)


class ScheduledActionCreate(BaseModel):
    """创建待策略门评估的定时主动行为。"""

    user_id: UUID
    conversation_id: UUID | None = None
    kind: ScheduledActionKind
    scheduled_for: datetime
    expires_at: datetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=500)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    social_cost: int = Field(default=1, ge=0, le=20)


class ScheduledActionResponse(BaseModel):
    """不直接回显业务载荷的主动行为治理摘要。"""

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
    payload_keys: tuple[str, ...]
    score: float | None
    social_cost: int
    decision_reasons: tuple[str, ...]
    job_id: UUID | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ScheduledActionListResponse(BaseModel):
    """定时行为列表。"""

    items: tuple[ScheduledActionResponse, ...]


class WorkerHeartbeatResponse(BaseModel):
    """仍在新鲜时间窗内的 Worker。"""

    worker_id: str
    queues: tuple[str, ...]
    current_job_id: UUID | None
    started_at: datetime
    last_seen_at: datetime


class TaskDashboardResponse(BaseModel):
    """任务后台的真相计数与 Worker 心跳。"""

    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    retrying: int = Field(ge=0)
    dead_letters: int = Field(ge=0)
    scheduled: int = Field(ge=0)
    workers: tuple[WorkerHeartbeatResponse, ...]
