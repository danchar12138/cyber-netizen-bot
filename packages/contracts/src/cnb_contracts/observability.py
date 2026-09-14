"""性能、成本、服务等级与活动告警接口契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from cnb_domain import AlertSeverity, ObservabilityAlertLifecycleStatus


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
