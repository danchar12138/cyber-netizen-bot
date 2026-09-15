"""性能、成本、服务等级与活动告警接口契约。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from cnb_domain import (
    AlertSeverity,
    ObservabilityAlertDispositionAction,
    ObservabilityAlertDispositionStatus,
    ObservabilityAlertLifecycleStatus,
)


class LatencyPercentilesResponse(BaseModel):
    """毫秒延迟分位数。"""

    p50_ms: int = Field(ge=0)
    p95_ms: int = Field(ge=0)
    p99_ms: int = Field(ge=0)


class ApiSloResponse(BaseModel):
    """应用接口请求量、服务端错误率与延迟。"""

    requests: int = Field(ge=0)
    server_errors: int = Field(ge=0)
    error_rate_percent: float = Field(ge=0, le=100)
    latency: LatencyPercentilesResponse


class AgentRunSloResponse(BaseModel):
    """Agent Run 成功率与延迟。"""

    terminal_runs: int = Field(ge=0)
    completed_runs: int = Field(ge=0)
    unsuccessful_runs: int = Field(ge=0)
    success_rate_percent: float = Field(ge=0, le=100)
    latency: LatencyPercentilesResponse


class ModelUsageResponse(BaseModel):
    """按模型聚合且已冻结价格的用量。"""

    provider: str
    model: str
    invocations: int = Field(ge=0)
    failed_invocations: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_microusd: int = Field(ge=0)
    latency: LatencyPercentilesResponse


class QueueMetricsResponse(BaseModel):
    """数据库任务队列当前积压。"""

    backlog: int = Field(ge=0)
    oldest_wait_seconds: int = Field(ge=0)


class ChannelDeliveryMetricsResponse(BaseModel):
    """渠道出站投递的安全聚合。"""

    attempts: int = Field(ge=0)
    delivered: int = Field(ge=0)
    degraded: int = Field(ge=0)
    failed: int = Field(ge=0)
    rate_limited: int = Field(ge=0)
    failure_rate_percent: float = Field(ge=0, le=100)


class NotificationDeliveryMetricsResponse(BaseModel):
    """告警通知任务窗口计数与尚未安全重放的死信数量。"""

    total: int = Field(ge=0)
    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    retrying: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    dead_letters: int = Field(ge=0)


class ActiveAlertResponse(BaseModel):
    """不含正文或凭证的确定性阈值告警。"""

    code: str
    severity: AlertSeverity
    title: str
    summary: str
    current_value: float
    threshold_value: float
    unit: str
    source_type: str = "observability"
    source_key: str | None = None


class ObservabilityAlertLifecycleResponse(BaseModel):
    """通用可观测告警生命周期，不含通知目标或 Secret。"""

    id: UUID
    agent_id: UUID
    source_type: str
    source_key: str
    code: str
    status: ObservabilityAlertLifecycleStatus
    severity: AlertSeverity
    occurrences: int = Field(ge=1)
    current_value: float
    threshold_value: float
    unit: str
    first_occurred_at: datetime
    last_occurred_at: datetime
    last_evaluated_at: datetime
    escalated_at: datetime | None
    escalation_level: int = Field(ge=0, le=3)
    last_escalated_at: datetime | None
    resolved_at: datetime | None
    recovery_duration_seconds: int | None = Field(default=None, ge=0)
    disposition_status: ObservabilityAlertDispositionStatus | None = None
    disposition_reason: str | None = Field(default=None, max_length=500)
    disposition_expires_at: datetime | None = None


class ObservabilityAlertLifecycleTrendPointResponse(BaseModel):
    """一个固定时间桶内的通用告警生命周期变化。"""

    bucket_started_at: datetime
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)


class ObservabilityAlertSourceLifecycleMetricsResponse(BaseModel):
    """单一通用告警来源的安全聚合。"""

    source_type: str = Field(min_length=1, max_length=80)
    active: int = Field(ge=0)
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)
    mean_recovery_seconds: float = Field(ge=0)
    p95_recovery_seconds: int = Field(ge=0)


class ObservabilityAlertLifecycleMetricsResponse(BaseModel):
    """当前 Agent 的通用告警生命周期聚合与趋势。"""

    window_started_at: datetime
    window_ended_at: datetime
    active: int = Field(ge=0)
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)
    mean_recovery_seconds: float = Field(ge=0)
    p95_recovery_seconds: int = Field(ge=0)
    sources: tuple[ObservabilityAlertSourceLifecycleMetricsResponse, ...]
    trend: tuple[ObservabilityAlertLifecycleTrendPointResponse, ...]


class ObservabilityAlertDispositionCommand(BaseModel):
    """通用告警确认命令，调用方必须显式确认。"""

    reason: str = Field(min_length=1, max_length=500)
    confirmed: bool = False


class ObservabilityAlertSuppressionCommand(ObservabilityAlertDispositionCommand):
    """带明确到期时间的通用告警临时抑制命令。"""

    expires_at: datetime


class ObservabilityAlertDispositionClearCommand(BaseModel):
    """解除当前通用告警处置的显式确认命令。"""

    confirmed: bool = False


class ObservabilityAlertDispositionResponse(BaseModel):
    """通用告警处置结果，不包含通知目标、Secret 或业务正文。"""

    lifecycle_id: UUID
    source_type: str
    source_key: str
    code: str
    status: Literal["acknowledged", "suppressed", "cleared"]
    reason: str
    expires_at: datetime | None
    updated_at: datetime


class ObservabilityAlertBatchDispositionCommand(BaseModel):
    """最多 100 项的原子批量处置命令。"""

    lifecycle_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    action: Literal["acknowledge", "suppress", "clear"]
    reason: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None
    confirmed: bool = False


class ObservabilityAlertBatchDispositionResponse(BaseModel):
    """批量处置的逐项结果。"""

    items: tuple[ObservabilityAlertDispositionResponse, ...]


class ObservabilityAlertDispositionEventResponse(BaseModel):
    """不含正文、通知目标或 Secret 的处置历史事件。"""

    id: UUID
    lifecycle_id: UUID
    source_type: str
    source_key: str
    code: str
    action: ObservabilityAlertDispositionAction
    reason: str = Field(min_length=1, max_length=500)
    actor_id: UUID
    expires_at: datetime | None
    occurred_at: datetime


class ObservabilityDashboardResponse(BaseModel):
    """管理后台可观测性完整聚合视图。"""

    window_started_at: datetime
    window_ended_at: datetime
    api: ApiSloResponse
    agent_runs: AgentRunSloResponse
    models: tuple[ModelUsageResponse, ...]
    queue: QueueMetricsResponse
    channel_delivery: ChannelDeliveryMetricsResponse
    notification_delivery: NotificationDeliveryMetricsResponse
    total_estimated_cost_microusd: int = Field(ge=0)
    alerts: tuple[ActiveAlertResponse, ...]
    alert_lifecycles: tuple[ObservabilityAlertLifecycleResponse, ...] = ()
