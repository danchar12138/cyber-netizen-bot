"""框架无关的性能、成本、SLO 与确定性告警应用服务。"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from cnb_application.configuration_service import ConfigurationService
from cnb_domain import (
    ActiveAlert,
    AlertSeverity,
    JsonValue,
    ObservabilityAlertLifecycle,
    ObservabilityAlertLifecycleStatus,
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
    agent_id: UUID | None = None


class ObservabilityRepository(Protocol):
    """请求指标写入及租户隔离聚合边界。"""

    async def record_api_request(self, observation: ApiRequestObservation) -> None: ...

    async def get_metrics(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        agent_id: UUID | None = None,
    ) -> ObservabilityMetrics: ...

    async def reconcile_observability_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alerts: tuple[ActiveAlert, ...],
        observed_at: datetime,
    ) -> tuple[ObservabilityAlertLifecycle, ...]: ...

    async def list_observability_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ObservabilityAlertLifecycleStatus | None = None,
        limit: int = 100,
    ) -> tuple[ObservabilityAlertLifecycle, ...]: ...

    async def mark_observability_alert_lifecycles_escalated(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        escalation_level: int,
        escalated_at: datetime,
    ) -> tuple[ObservabilityAlertLifecycle, ...]: ...

    async def list_observability_agent_ids(self) -> tuple[tuple[UUID, UUID], ...]: ...


@dataclass(frozen=True, slots=True)
class ObservabilityAlertEscalationCandidate:
    lifecycle: ObservabilityAlertLifecycle
    target_level: int
    adapter: str | None


@dataclass(frozen=True, slots=True)
class ObservabilityAlertLifecycleEvaluation:
    """一次通用告警对账的安全结果。"""

    alerts: tuple[ActiveAlert, ...]
    active_lifecycles: tuple[ObservabilityAlertLifecycle, ...]
    due_escalations: tuple[ObservabilityAlertEscalationCandidate, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertSourceAdapter:
    """把确定性指标告警映射为稳定的通用生命周期来源。"""

    source_type: str
    codes: tuple[str, ...]

    def adapt(self, alert: ActiveAlert) -> ActiveAlert | None:
        if alert.code not in self.codes:
            return None
        return replace(alert, source_type=self.source_type, source_key=alert.code)


_GENERIC_ALERT_SOURCE_ADAPTERS = (
    ObservabilityAlertSourceAdapter("api", ("api_error_rate", "api_p95_latency")),
    ObservabilityAlertSourceAdapter("agent_runtime", ("agent_success_rate", "agent_p95_latency")),
    ObservabilityAlertSourceAdapter("model_runtime", ("model_failure_rate", "model_cost_budget")),
    ObservabilityAlertSourceAdapter("task_queue", ("queue_backlog", "queue_oldest_wait")),
    ObservabilityAlertSourceAdapter("notification", ("notification_delivery_dead_letters",)),
)


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
        agent_id: UUID | None = None,
        now: datetime | None = None,
    ) -> ObservabilityDashboard:
        window_ended_at = now or datetime.now(UTC)
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        window_minutes = self._integer(
            configuration.values["observability.window_minutes"],
            "observability.window_minutes",
        )
        metrics = await self._repository.get_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=window_ended_at - timedelta(minutes=window_minutes),
            window_ended_at=window_ended_at,
        )
        total_cost = sum(item.estimated_cost_microusd for item in metrics.models)
        alerts = self._alerts(metrics, total_cost, configuration.values)
        lifecycles = (
            await self._repository.list_observability_alert_lifecycles(
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            if agent_id is not None
            else ()
        )
        return ObservabilityDashboard(
            metrics=metrics,
            total_estimated_cost_microusd=total_cost,
            alerts=alerts,
            alert_lifecycles=lifecycles,
        )

    async def reconcile_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        now: datetime | None = None,
    ) -> ObservabilityAlertLifecycleEvaluation:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id, agent_id=agent_id
        )
        window_minutes = self._integer(
            configuration.values["observability.window_minutes"], "observability.window_minutes"
        )
        metrics = await self._repository.get_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=observed_at - timedelta(minutes=window_minutes),
            window_ended_at=observed_at,
        )
        total_cost = sum(item.estimated_cost_microusd for item in metrics.models)
        alerts = self._lifecycle_alerts(self._alerts(metrics, total_cost, configuration.values))
        active = await self._repository.reconcile_observability_alert_lifecycles(
            tenant_id=tenant_id, agent_id=agent_id, alerts=alerts, observed_at=observed_at
        )
        from cnb_application.alert_policy import AlertEscalationPolicy

        policy = AlertEscalationPolicy.from_values(configuration.values)
        due = tuple(
            ObservabilityAlertEscalationCandidate(
                lifecycle=item,
                target_level=decision.target_level,
                adapter=decision.adapter,
            )
            for item in active
            if (
                decision := policy.evaluate(
                    severity=item.severity,
                    duration_minutes=max(
                        0, int((observed_at - item.first_occurred_at).total_seconds() // 60)
                    ),
                    current_level=item.escalation_level,
                    evaluated_at=observed_at,
                )
            ).target_level
            is not None
        )
        return ObservabilityAlertLifecycleEvaluation(
            alerts=alerts, active_lifecycles=active, due_escalations=due
        )

    async def alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ObservabilityAlertLifecycleStatus | None = None,
        limit: int = 100,
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("通用告警生命周期数量必须位于 1 到 500 之间")
        return await self._repository.list_observability_alert_lifecycles(
            tenant_id=tenant_id, agent_id=agent_id, status=status, limit=limit
        )

    async def mark_alert_lifecycles_escalated(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        escalation_level: int,
        escalated_at: datetime,
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        if not lifecycle_ids:
            return ()
        if not 1 <= escalation_level <= 3:
            raise ValueError("通用告警升级等级必须位于 1 到 3 之间")
        return await self._repository.mark_observability_alert_lifecycles_escalated(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_ids=lifecycle_ids,
            escalation_level=escalation_level,
            escalated_at=escalated_at.astimezone(UTC),
        )

    @staticmethod
    def _lifecycle_alerts(alerts: tuple[ActiveAlert, ...]) -> tuple[ActiveAlert, ...]:
        """按来源适配器生成生命周期输入，渠道来源由专用生命周期处理。"""
        adapted: list[ActiveAlert] = []
        for alert in alerts:
            for adapter in _GENERIC_ALERT_SOURCE_ADAPTERS:
                item = adapter.adapt(alert)
                if item is not None:
                    adapted.append(item)
                    break
        return tuple(adapted)

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
        channel_failure_limit = cls._number(
            values["alerts.channel.failure_rate_percent"],
            "alerts.channel.failure_rate_percent",
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
                    "应用接口错误率超出服务等级目标",
                    "当前窗口的服务端错误比例高于已发布阈值。",
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
                    "应用接口 P95 延迟超出服务等级目标",
                    "当前窗口的应用接口 P95 响应时间高于已发布阈值。",
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
                    "智能体运行成功率低于服务等级目标",
                    "当前窗口的智能体运行完成比例低于已发布阈值。",
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
                    "智能体运行 P95 延迟超出服务等级目标",
                    "当前窗口的智能体运行 P95 完成时间高于已发布阈值。",
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
        if (
            metrics.channel_delivery.attempts
            and metrics.channel_delivery.failure_rate_percent > channel_failure_limit
        ):
            alerts.append(
                cls._alert(
                    "channel_delivery_failure_rate",
                    AlertSeverity.CRITICAL,
                    "渠道出站失败率过高",
                    "当前窗口的渠道出站失败和限流比例高于已发布阈值。",
                    metrics.channel_delivery.failure_rate_percent,
                    channel_failure_limit,
                    "%",
                )
            )
        if metrics.notification_delivery.dead_letters:
            alerts.append(
                cls._alert(
                    "notification_delivery_dead_letters",
                    AlertSeverity.CRITICAL,
                    "通知投递出现死信",
                    "告警通知后台任务存在尚未安全重放的死信。",
                    metrics.notification_delivery.dead_letters,
                    0,
                    "jobs",
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
