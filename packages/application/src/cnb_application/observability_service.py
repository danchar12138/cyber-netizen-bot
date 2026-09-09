"""框架无关的性能、成本、SLO 与确定性告警应用服务。"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from cnb_application.configuration_service import ConfigurationService
from cnb_domain import (
    ActiveAlert,
    AlertSeverity,
    JsonValue,
    ObservabilityDashboard,
    ObservabilityMetrics,
)


@dataclass(frozen=True, slots=True)
class ApiRequestObservation:
    """允许落盘的 HTTP 请求安全元数据白名单。"""

    tenant_id: UUID | None
    method: str
    route: str
    status_code: int
    duration_ms: int
    occurred_at: datetime


class ObservabilityRepository(Protocol):
    """请求指标写入及租户隔离聚合边界。"""

    async def record_api_request(self, observation: ApiRequestObservation) -> None: ...

    async def get_metrics(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> ObservabilityMetrics: ...


class ObservabilityService:
    """读取已发布阈值并形成可解释的活动告警。"""

    def __init__(
        self,
        repository: ObservabilityRepository,
        configuration_service: ConfigurationService,
    ) -> None:
        self._repository = repository
        self._configuration_service = configuration_service

    async def dashboard(
        self,
        *,
        tenant_id: UUID,
        now: datetime | None = None,
    ) -> ObservabilityDashboard:
        window_ended_at = now or datetime.now(UTC)
        configuration = await self._configuration_service.resolve_effective(tenant_id=tenant_id)
        window_minutes = self._integer(
            configuration.values["observability.window_minutes"],
            "observability.window_minutes",
        )
        metrics = await self._repository.get_metrics(
            tenant_id=tenant_id,
            window_started_at=window_ended_at - timedelta(minutes=window_minutes),
            window_ended_at=window_ended_at,
        )
        total_cost = sum(item.estimated_cost_microusd for item in metrics.models)
        alerts = self._alerts(metrics, total_cost, configuration.values)
        return ObservabilityDashboard(
            metrics=metrics,
            total_estimated_cost_microusd=total_cost,
            alerts=alerts,
        )

    @classmethod
    def _alerts(
        cls,
        metrics: ObservabilityMetrics,
        total_cost_microusd: int,
        values: Mapping[str, JsonValue],
    ) -> tuple[ActiveAlert, ...]:
        alerts: list[ActiveAlert] = []
        api_error_limit = cls._number(
            values["slo.api.maximum_error_rate_percent"],
            "slo.api.maximum_error_rate_percent",
        )
        api_p95_limit = cls._number(
            values["slo.api.maximum_p95_ms"],
            "slo.api.maximum_p95_ms",
        )
        agent_success_limit = cls._number(
            values["slo.agent.minimum_success_rate_percent"],
            "slo.agent.minimum_success_rate_percent",
        )
        agent_p95_limit = cls._number(
            values["slo.agent.maximum_p95_ms"],
            "slo.agent.maximum_p95_ms",
        )
        backlog_limit = cls._number(
            values["alerts.queue.maximum_backlog"],
            "alerts.queue.maximum_backlog",
        )
        model_failure_limit = cls._number(
            values["alerts.model.maximum_failure_rate_percent"],
            "alerts.model.maximum_failure_rate_percent",
        )
        wait_limit = cls._number(
            values["alerts.queue.maximum_oldest_wait_seconds"],
            "alerts.queue.maximum_oldest_wait_seconds",
        )
        cost_limit_usd = cls._number(
            values["cost.window_budget_usd"],
            "cost.window_budget_usd",
        )

        if metrics.api.requests and metrics.api.error_rate_percent > api_error_limit:
            alerts.append(
                cls._alert(
                    "api_error_rate",
                    AlertSeverity.CRITICAL,
                    "API 错误率超出 SLO",
                    "当前窗口的 HTTP 5xx 比例高于已发布阈值。",
                    metrics.api.error_rate_percent,
                    api_error_limit,
                    "%",
                )
            )
        if metrics.api.requests and metrics.api.latency.p95_ms > api_p95_limit:
            alerts.append(
                cls._alert(
                    "api_p95_latency",
                    AlertSeverity.WARNING,
                    "API P95 延迟超出 SLO",
                    "当前窗口的 API P95 响应时间高于已发布阈值。",
                    metrics.api.latency.p95_ms,
                    api_p95_limit,
                    "ms",
                )
            )
        if (
            metrics.agent_runs.terminal_runs
            and metrics.agent_runs.success_rate_percent < agent_success_limit
        ):
            alerts.append(
                cls._alert(
                    "agent_success_rate",
                    AlertSeverity.CRITICAL,
                    "Agent Run 成功率低于 SLO",
                    "当前窗口的 Agent Run 完成比例低于已发布阈值。",
                    metrics.agent_runs.success_rate_percent,
                    agent_success_limit,
                    "%",
                )
            )
        if metrics.agent_runs.terminal_runs and metrics.agent_runs.latency.p95_ms > agent_p95_limit:
            alerts.append(
                cls._alert(
                    "agent_p95_latency",
                    AlertSeverity.WARNING,
                    "Agent Run P95 延迟超出 SLO",
                    "当前窗口的 Agent Run P95 完成时间高于已发布阈值。",
                    metrics.agent_runs.latency.p95_ms,
                    agent_p95_limit,
                    "ms",
                )
            )
        if metrics.queue.backlog > backlog_limit:
            alerts.append(
                cls._alert(
                    "queue_backlog",
                    AlertSeverity.WARNING,
                    "后台队列积压",
                    "待处理与重试任务数量高于已发布阈值。",
                    metrics.queue.backlog,
                    backlog_limit,
                    "jobs",
                )
            )
        model_invocations = sum(item.invocations for item in metrics.models)
        model_failures = sum(item.failed_invocations for item in metrics.models)
        model_failure_rate = model_failures * 100 / model_invocations if model_invocations else 0.0
        if model_invocations and model_failure_rate > model_failure_limit:
            alerts.append(
                cls._alert(
                    "model_failure_rate",
                    AlertSeverity.CRITICAL,
                    "模型调用失败率过高",
                    "当前窗口的模型失败与超时比例高于已发布阈值。",
                    model_failure_rate,
                    model_failure_limit,
                    "%",
                )
            )
        if metrics.queue.oldest_wait_seconds > wait_limit:
            alerts.append(
                cls._alert(
                    "queue_oldest_wait",
                    AlertSeverity.WARNING,
                    "后台任务等待过久",
                    "最早可执行任务的等待时间高于已发布阈值。",
                    metrics.queue.oldest_wait_seconds,
                    wait_limit,
                    "s",
                )
            )
        total_cost_usd = total_cost_microusd / 1_000_000
        if total_cost_usd > cost_limit_usd:
            alerts.append(
                cls._alert(
                    "model_cost_budget",
                    AlertSeverity.WARNING,
                    "模型成本超出窗口预算",
                    "冻结估算的模型调用成本高于已发布预算。",
                    total_cost_usd,
                    cost_limit_usd,
                    "USD",
                )
            )
        return tuple(alerts)

    @staticmethod
    def _alert(
        code: str,
        severity: AlertSeverity,
        title: str,
        summary: str,
        current_value: float,
        threshold_value: float,
        unit: str,
    ) -> ActiveAlert:
        return ActiveAlert(
            code=code,
            severity=severity,
            title=title,
            summary=summary,
            current_value=float(current_value),
            threshold_value=float(threshold_value),
            unit=unit,
        )

    @staticmethod
    def _integer(value: object, key: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"生效配置 {key} 必须是整数")
        return value

    @staticmethod
    def _number(value: object, key: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"生效配置 {key} 必须是数值")
        return float(value)
