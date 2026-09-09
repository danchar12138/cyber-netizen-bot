"""性能、成本、SLO 与告警使用的纯领域类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AlertSeverity(StrEnum):
    """确定性阈值告警的严重级别。"""

    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class LatencyPercentiles:
    """一段时间窗口内的毫秒延迟分位数。"""

    p50_ms: int
    p95_ms: int
    p99_ms: int


@dataclass(frozen=True, slots=True)
class ApiSloMetrics:
    """管理 API 的安全聚合 SLO 指标。"""

    requests: int
    server_errors: int
    error_rate_percent: float
    latency: LatencyPercentiles


@dataclass(frozen=True, slots=True)
class AgentRunSloMetrics:
    """Agent Run 的终态成功率与执行延迟。"""

    terminal_runs: int
    completed_runs: int
    unsuccessful_runs: int
    success_rate_percent: float
    latency: LatencyPercentiles


@dataclass(frozen=True, slots=True)
class ModelUsageMetrics:
    """按 Provider 与模型冻结聚合的 Token、成本和可靠性指标。"""

    provider: str
    model: str
    invocations: int
    failed_invocations: int
    input_tokens: int
    output_tokens: int
    estimated_cost_microusd: int
    latency: LatencyPercentiles


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    """数据库任务真相源中的队列积压。"""

    backlog: int
    oldest_wait_seconds: int


@dataclass(frozen=True, slots=True)
class ObservabilityMetrics:
    """仓储返回的一段租户隔离原始聚合窗口。"""

    window_started_at: datetime
    window_ended_at: datetime
    api: ApiSloMetrics
    agent_runs: AgentRunSloMetrics
    models: tuple[ModelUsageMetrics, ...]
    queue: QueueMetrics


@dataclass(frozen=True, slots=True)
class ActiveAlert:
    """由已发布阈值确定性计算且不含业务正文的活动告警。"""

    code: str
    severity: AlertSeverity
    title: str
    summary: str
    current_value: float
    threshold_value: float
    unit: str


@dataclass(frozen=True, slots=True)
class ObservabilityDashboard:
    """管理后台使用的可观测性与成本总览。"""

    metrics: ObservabilityMetrics
    total_estimated_cost_microusd: int
    alerts: tuple[ActiveAlert, ...]
