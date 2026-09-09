"""性能、成本、SLO 与活动告警 API 契约。"""

from datetime import datetime

from pydantic import BaseModel, Field

from cnb_domain import AlertSeverity


class LatencyPercentilesResponse(BaseModel):
    """毫秒延迟分位数。"""

    p50_ms: int = Field(ge=0)
    p95_ms: int = Field(ge=0)
    p99_ms: int = Field(ge=0)


class ApiSloResponse(BaseModel):
    """API 请求量、5xx 错误率与延迟。"""

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


class ActiveAlertResponse(BaseModel):
    """不含正文或凭证的确定性阈值告警。"""

    code: str
    severity: AlertSeverity
    title: str
    summary: str
    current_value: float
    threshold_value: float
    unit: str


class ObservabilityDashboardResponse(BaseModel):
    """管理后台可观测性完整聚合视图。"""

    window_started_at: datetime
    window_ended_at: datetime
    api: ApiSloResponse
    agent_runs: AgentRunSloResponse
    models: tuple[ModelUsageResponse, ...]
    queue: QueueMetricsResponse
    total_estimated_cost_microusd: int = Field(ge=0)
    alerts: tuple[ActiveAlertResponse, ...]
