"""框架无关的性能、成本、SLO 与确定性告警应用服务。"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

from cnb_application.configuration_service import ConfigurationService
from cnb_application.pagination import (
    EntityCursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)
from cnb_domain import (
    ActiveAlert,
    AlertSeverity,
    JsonValue,
    ObservabilityAlertDisposition,
    ObservabilityAlertDispositionAction,
    ObservabilityAlertDispositionEvent,
    ObservabilityAlertDispositionStatus,
    ObservabilityAlertLifecycle,
    ObservabilityAlertLifecycleStatus,
    ObservabilityAlertReplayDecision,
    ObservabilityAlertReplayMetrics,
    ObservabilityAlertReplayReason,
    ObservabilityAlertReplayReview,
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


@dataclass(frozen=True, slots=True)
class ObservabilityAlertLifecycleReconciliation:
    """仓储在一次原子对账中确认的通用告警生命周期转移。"""

    active_lifecycles: tuple[ObservabilityAlertLifecycle, ...]
    activated_lifecycles: tuple[ObservabilityAlertLifecycle, ...]
    recovered_lifecycles: tuple[ObservabilityAlertLifecycle, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertLifecycleTrendPoint:
    """一个固定时间桶内的通用告警生命周期变化计数。"""

    bucket_started_at: datetime
    opened: int
    resolved: int
    escalated: int


@dataclass(frozen=True, slots=True)
class ObservabilityAlertSourceLifecycleMetrics:
    """单一通用告警来源的安全生命周期聚合。"""

    source_type: str
    active: int
    opened: int
    resolved: int
    escalated: int
    mean_recovery_seconds: float
    p95_recovery_seconds: int


@dataclass(frozen=True, slots=True)
class ObservabilityAlertLifecycleMetrics:
    """当前 Agent 的通用告警生命周期聚合与趋势。"""

    window_started_at: datetime
    window_ended_at: datetime
    active: int
    opened: int
    resolved: int
    escalated: int
    mean_recovery_seconds: float
    p95_recovery_seconds: int
    sources: tuple[ObservabilityAlertSourceLifecycleMetrics, ...]
    trend: tuple[ObservabilityAlertLifecycleTrendPoint, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertDispositionResult:
    """处置结果与其生命周期标识。"""

    lifecycle_id: UUID
    disposition: ObservabilityAlertDisposition


@dataclass(frozen=True, slots=True)
class ObservabilityCursorPage[T]:
    """可观测运营资源的稳定键集分页。"""

    items: tuple[T, ...]
    next_cursor: str | None


class ObservabilityValidationError(ValueError):
    """通用告警查询或处置参数不合法。"""


class ObservabilityNotFoundError(LookupError):
    """当前租户与 Agent 作用域内不存在目标通用告警。"""


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
    ) -> ObservabilityAlertLifecycleReconciliation: ...

    async def list_observability_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ObservabilityAlertLifecycleStatus | None = None,
        source_type: str | None = None,
        severity: AlertSeverity | None = None,
        minimum_duration_minutes: int | None = None,
        evaluated_at: datetime | None = None,
        cursor: EntityCursor | None = None,
        limit: int = 100,
    ) -> tuple[ObservabilityAlertLifecycle, ...]: ...

    async def list_observability_alert_lifecycles_in_window(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        source_type: str | None = None,
        severity: AlertSeverity | None = None,
    ) -> tuple[ObservabilityAlertLifecycle, ...]: ...

    async def get_observability_alert_lifecycle(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
    ) -> ObservabilityAlertLifecycle | None: ...

    async def get_observability_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        source_type: str,
        source_key: str,
    ) -> ObservabilityAlertDisposition | None: ...

    async def list_observability_alert_dispositions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> tuple[ObservabilityAlertDisposition, ...]: ...

    async def save_observability_alert_disposition(
        self, disposition: ObservabilityAlertDisposition, *, lifecycle_id: UUID | None = None
    ) -> ObservabilityAlertDisposition: ...

    async def clear_observability_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        source_type: str,
        source_key: str,
        actor_id: UUID,
        lifecycle_id: UUID | None = None,
        reason: str = "管理员解除当前处置",
    ) -> ObservabilityAlertDisposition | None: ...

    async def batch_save_observability_alert_dispositions(
        self,
        items: tuple[tuple[UUID, ObservabilityAlertDisposition], ...],
    ) -> tuple[ObservabilityAlertDisposition, ...]: ...

    async def batch_clear_observability_alert_dispositions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        actor_id: UUID,
        reason: str,
    ) -> tuple[ObservabilityAlertDisposition, ...]: ...

    async def list_observability_alert_disposition_events(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        action: ObservabilityAlertDispositionAction | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        limit: int = 100,
    ) -> tuple[ObservabilityAlertDispositionEvent, ...]: ...

    async def record_observability_alert_replay_review(
        self, review: ObservabilityAlertReplayReview
    ) -> ObservabilityAlertReplayReview: ...

    async def list_observability_alert_replay_reviews(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        decision: ObservabilityAlertReplayDecision | None = None,
        reason_code: ObservabilityAlertReplayReason | None = None,
        source_type: str | None = None,
        cursor: EntityCursor | None = None,
        limit: int = 100,
    ) -> tuple[ObservabilityAlertReplayReview, ...]: ...

    async def get_observability_alert_replay_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        source_type: str | None = None,
    ) -> ObservabilityAlertReplayMetrics: ...

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
    activated_lifecycles: tuple[ObservabilityAlertLifecycle, ...]
    recovered_lifecycles: tuple[ObservabilityAlertLifecycle, ...]
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
        lifecycles = ()
        if agent_id is not None:
            lifecycles = await self.alert_lifecycles(
                tenant_id=tenant_id,
                agent_id=agent_id,
                now=window_ended_at,
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
        reconciliation = await self._repository.reconcile_observability_alert_lifecycles(
            tenant_id=tenant_id, agent_id=agent_id, alerts=alerts, observed_at=observed_at
        )
        dispositions = await self._repository.list_observability_alert_dispositions(
            tenant_id=tenant_id, agent_id=agent_id
        )
        active = self._attach_dispositions(
            reconciliation.active_lifecycles, dispositions, observed_at
        )
        activated = self._attach_dispositions(
            reconciliation.activated_lifecycles, dispositions, observed_at
        )
        recovered = self._attach_dispositions(
            reconciliation.recovered_lifecycles, dispositions, observed_at
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
            if item.disposition_status is not ObservabilityAlertDispositionStatus.SUPPRESSED
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
            alerts=alerts,
            active_lifecycles=active,
            activated_lifecycles=activated,
            recovered_lifecycles=recovered,
            due_escalations=due,
        )

    async def alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ObservabilityAlertLifecycleStatus | None = None,
        source_type: str | None = None,
        severity: AlertSeverity | None = None,
        minimum_duration_minutes: int | None = None,
        limit: int = 100,
        now: datetime | None = None,
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        if not 1 <= limit <= 500:
            raise ObservabilityValidationError("通用告警生命周期数量必须位于 1 到 500 之间")
        normalized_source = source_type.strip() if source_type is not None else None
        if source_type is not None and not normalized_source:
            raise ObservabilityValidationError("通用告警来源类型不能为空")
        if normalized_source is not None and len(normalized_source) > 80:
            raise ObservabilityValidationError("通用告警来源类型不能超过 80 个字符")
        if minimum_duration_minutes is not None and not 0 <= minimum_duration_minutes <= 525_600:
            raise ObservabilityValidationError("最短持续时间必须位于 0 到 525600 分钟之间")
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        rows = await self._repository.list_observability_alert_lifecycles(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=status,
            source_type=normalized_source,
            severity=severity,
            minimum_duration_minutes=minimum_duration_minutes,
            evaluated_at=evaluated_at,
            limit=limit,
        )
        dispositions = await self._repository.list_observability_alert_dispositions(
            tenant_id=tenant_id, agent_id=agent_id
        )
        return self._attach_dispositions(rows, dispositions, evaluated_at)

    async def alert_lifecycle_page(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ObservabilityAlertLifecycleStatus | None = None,
        source_type: str | None = None,
        severity: AlertSeverity | None = None,
        minimum_duration_minutes: int | None = None,
        cursor: str | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> ObservabilityCursorPage[ObservabilityAlertLifecycle]:
        """按更新时间与 UUID 稳定下钻通用告警生命周期。"""
        if not 1 <= limit <= 100:
            raise ObservabilityValidationError("通用告警生命周期分页数量必须位于 1 到 100 之间")
        normalized_source = source_type.strip() if source_type is not None else None
        if source_type is not None and not normalized_source:
            raise ObservabilityValidationError("通用告警来源类型不能为空")
        if normalized_source is not None and len(normalized_source) > 80:
            raise ObservabilityValidationError("通用告警来源类型不能超过 80 个字符")
        if minimum_duration_minutes is not None and not 0 <= minimum_duration_minutes <= 525_600:
            raise ObservabilityValidationError("最短持续时间必须位于 0 到 525600 分钟之间")
        try:
            parsed_cursor = decode_cursor(cursor)
        except InvalidCursorError as error:
            raise ObservabilityValidationError("通用告警生命周期分页游标无效") from error
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        rows = await self._repository.list_observability_alert_lifecycles(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=status,
            source_type=normalized_source,
            severity=severity,
            minimum_duration_minutes=minimum_duration_minutes,
            evaluated_at=evaluated_at,
            cursor=parsed_cursor,
            limit=limit + 1,
        )
        visible = rows[:limit]
        dispositions = await self._repository.list_observability_alert_dispositions(
            tenant_id=tenant_id, agent_id=agent_id
        )
        next_cursor = (
            encode_cursor(EntityCursor(visible[-1].updated_at, visible[-1].id))
            if len(rows) > limit and visible
            else None
        )
        return ObservabilityCursorPage(
            items=self._attach_dispositions(visible, dispositions, evaluated_at),
            next_cursor=next_cursor,
        )

    async def alert_lifecycle_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_minutes: int,
        bucket_minutes: int,
        source_type: str | None = None,
        severity: AlertSeverity | None = None,
        now: datetime | None = None,
    ) -> ObservabilityAlertLifecycleMetrics:
        """独立统计通用生命周期表，避免复用渠道指标口径。"""
        if not 5 <= window_minutes <= 10_080:
            raise ObservabilityValidationError("通用告警统计窗口必须位于 5 到 10080 分钟之间")
        if not 5 <= bucket_minutes <= 1_440 or bucket_minutes > window_minutes:
            raise ObservabilityValidationError("通用告警趋势时间桶必须位于 5 分钟到统计窗口之间")
        normalized_source = source_type.strip() if source_type is not None else None
        if source_type is not None and not normalized_source:
            raise ObservabilityValidationError("通用告警来源类型不能为空")
        if normalized_source is not None and len(normalized_source) > 80:
            raise ObservabilityValidationError("通用告警来源类型不能超过 80 个字符")
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        started_at = ended_at - timedelta(minutes=window_minutes)
        rows = await self._repository.list_observability_alert_lifecycles_in_window(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=started_at,
            window_ended_at=ended_at,
            source_type=normalized_source,
            severity=severity,
        )
        bucket_seconds = bucket_minutes * 60
        bucket_count = (window_minutes + bucket_minutes - 1) // bucket_minutes
        buckets = [
            ObservabilityAlertLifecycleTrendPoint(
                bucket_started_at=started_at + timedelta(minutes=index * bucket_minutes),
                opened=0,
                resolved=0,
                escalated=0,
            )
            for index in range(bucket_count)
        ]

        def add_event(
            event_at: datetime | None,
            field: Literal["opened", "resolved", "escalated"],
        ) -> None:
            if event_at is None or not started_at <= event_at <= ended_at:
                return
            index = min(
                bucket_count - 1,
                int((event_at - started_at).total_seconds()) // bucket_seconds,
            )
            point = buckets[index]
            buckets[index] = replace(point, **{field: getattr(point, field) + 1})

        for row in rows:
            add_event(row.first_occurred_at, "opened")
            add_event(row.resolved_at, "resolved")
            add_event(row.escalated_at, "escalated")

        def recovery_values(items: tuple[ObservabilityAlertLifecycle, ...]) -> list[int]:
            return sorted(
                row.recovery_duration_seconds
                for row in items
                if row.resolved_at is not None
                and started_at <= row.resolved_at <= ended_at
                and row.recovery_duration_seconds is not None
            )

        def summary(
            source: str | None,
            items: tuple[ObservabilityAlertLifecycle, ...],
        ) -> ObservabilityAlertSourceLifecycleMetrics:
            recovery = recovery_values(items)
            p95_index = max(0, (95 * len(recovery) + 99) // 100 - 1) if recovery else 0
            return ObservabilityAlertSourceLifecycleMetrics(
                source_type=source or "全部来源",
                active=sum(
                    item.status is ObservabilityAlertLifecycleStatus.ACTIVE for item in items
                ),
                opened=sum(started_at <= item.first_occurred_at <= ended_at for item in items),
                resolved=sum(
                    item.resolved_at is not None and started_at <= item.resolved_at <= ended_at
                    for item in items
                ),
                escalated=sum(
                    item.escalated_at is not None and started_at <= item.escalated_at <= ended_at
                    for item in items
                ),
                mean_recovery_seconds=sum(recovery) / len(recovery) if recovery else 0.0,
                p95_recovery_seconds=recovery[p95_index] if recovery else 0,
            )

        all_rows = tuple(rows)
        recovery = recovery_values(all_rows)
        p95_index = max(0, (95 * len(recovery) + 99) // 100 - 1) if recovery else 0
        grouped: dict[str, list[ObservabilityAlertLifecycle]] = {}
        for row in all_rows:
            grouped.setdefault(row.source_type, []).append(row)
        by_source = tuple(summary(source, tuple(grouped[source])) for source in sorted(grouped))
        return ObservabilityAlertLifecycleMetrics(
            window_started_at=started_at,
            window_ended_at=ended_at,
            active=sum(
                item.status is ObservabilityAlertLifecycleStatus.ACTIVE for item in all_rows
            ),
            opened=sum(started_at <= item.first_occurred_at <= ended_at for item in all_rows),
            resolved=sum(
                item.resolved_at is not None and started_at <= item.resolved_at <= ended_at
                for item in all_rows
            ),
            escalated=sum(
                item.escalated_at is not None and started_at <= item.escalated_at <= ended_at
                for item in all_rows
            ),
            mean_recovery_seconds=sum(recovery) / len(recovery) if recovery else 0.0,
            p95_recovery_seconds=recovery[p95_index] if recovery else 0,
            sources=by_source,
            trend=tuple(buckets),
        )

    async def acknowledge_alert(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
        reason: str,
        actor_id: UUID,
        confirmed: bool,
        now: datetime | None = None,
    ) -> ObservabilityAlertDisposition:
        return await self._set_alert_disposition(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_id=lifecycle_id,
            status=ObservabilityAlertDispositionStatus.ACKNOWLEDGED,
            reason=reason,
            expires_at=None,
            actor_id=actor_id,
            confirmed=confirmed,
            now=now,
        )

    async def batch_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        action: Literal["acknowledge", "suppress", "clear"],
        reason: str,
        expires_at: datetime | None,
        actor_id: UUID,
        confirmed: bool,
        now: datetime | None = None,
    ) -> tuple[ObservabilityAlertDispositionResult, ...]:
        """在一个仓储事务中批量处置当前 Agent 的告警。"""
        if not confirmed:
            raise ObservabilityValidationError("批量通用告警处置必须明确确认")
        if not 1 <= len(lifecycle_ids) <= 100:
            raise ObservabilityValidationError("批量通用告警数量必须位于 1 到 100 之间")
        if len(set(lifecycle_ids)) != len(lifecycle_ids):
            raise ObservabilityValidationError("批量通用告警生命周期不能重复")
        if action not in {"acknowledge", "suppress", "clear"}:
            raise ObservabilityValidationError("批量通用告警动作无效")
        changed_at = (now or datetime.now(UTC)).astimezone(UTC)
        lifecycle_values: list[ObservabilityAlertLifecycle] = []
        for lifecycle_id in lifecycle_ids:
            lifecycle_values.append(
                await self._get_lifecycle(
                    tenant_id=tenant_id, agent_id=agent_id, lifecycle_id=lifecycle_id
                )
            )
        lifecycles = tuple(lifecycle_values)
        if action != "clear" and any(
            lifecycle.status is not ObservabilityAlertLifecycleStatus.ACTIVE
            for lifecycle in lifecycles
        ):
            raise ObservabilityValidationError("只能确认或抑制活动中的通用告警")
        source_keys = {(item.source_type, item.source_key) for item in lifecycles}
        if len(source_keys) != len(lifecycles):
            raise ObservabilityValidationError("批量处置中同一告警来源不能重复")
        if action == "clear":
            normalized_reason = reason.strip()
            if not normalized_reason:
                raise ObservabilityValidationError("通用告警处置原因不能为空")
            if len(normalized_reason) > 500:
                raise ObservabilityValidationError("通用告警处置原因不能超过 500 个字符")
            try:
                removed = await self._repository.batch_clear_observability_alert_dispositions(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    lifecycle_ids=lifecycle_ids,
                    actor_id=actor_id,
                    reason=normalized_reason,
                )
            except LookupError as error:
                raise ObservabilityNotFoundError("部分通用告警处置不存在") from error
            if len(removed) != len(lifecycle_ids):
                raise ObservabilityNotFoundError("部分通用告警处置不存在")
            by_key = {(item.source_type, item.source_key): item for item in removed}
            return tuple(
                ObservabilityAlertDispositionResult(
                    lifecycle_id=lifecycle.id,
                    disposition=by_key[(lifecycle.source_type, lifecycle.source_key)],
                )
                for lifecycle in lifecycles
            )
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ObservabilityValidationError("通用告警处置原因不能为空")
        if len(normalized_reason) > 500:
            raise ObservabilityValidationError("通用告警处置原因不能超过 500 个字符")
        if action == "suppress":
            if expires_at is None:
                raise ObservabilityValidationError("临时抑制必须设置到期时间")
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                raise ObservabilityValidationError("临时抑制到期时间必须包含时区")
            expires_at = expires_at.astimezone(UTC)
            if expires_at <= changed_at:
                raise ObservabilityValidationError("临时抑制到期时间必须晚于当前时间")
        status = (
            ObservabilityAlertDispositionStatus.ACKNOWLEDGED
            if action == "acknowledge"
            else ObservabilityAlertDispositionStatus.SUPPRESSED
        )
        existing_values: list[ObservabilityAlertDisposition | None] = []
        for lifecycle in lifecycles:
            existing_values.append(
                await self._repository.get_observability_alert_disposition(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    source_type=lifecycle.source_type,
                    source_key=lifecycle.source_key,
                )
            )
        existing = tuple(existing_values)
        items = tuple(
            (
                lifecycle.id,
                ObservabilityAlertDisposition(
                    id=prior.id if prior is not None else uuid4(),
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    source_type=lifecycle.source_type,
                    source_key=lifecycle.source_key,
                    code=lifecycle.code,
                    status=status,
                    reason=normalized_reason,
                    actor_id=actor_id,
                    expires_at=expires_at,
                    created_at=prior.created_at if prior is not None else changed_at,
                    updated_at=changed_at,
                ),
            )
            for lifecycle, prior in zip(lifecycles, existing, strict=True)
        )
        try:
            stored = await self._repository.batch_save_observability_alert_dispositions(items)
        except LookupError as error:
            raise ObservabilityNotFoundError("活动通用告警不存在") from error
        return tuple(
            ObservabilityAlertDispositionResult(lifecycle_id=lifecycle_id, disposition=item)
            for lifecycle_id, item in zip(lifecycle_ids, stored, strict=True)
        )

    async def disposition_events(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID | None = None,
        source_type: str | None = None,
        source_key: str | None = None,
        action: ObservabilityAlertDispositionAction | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        limit: int = 100,
    ) -> tuple[ObservabilityAlertDispositionEvent, ...]:
        if not 1 <= limit <= 500:
            raise ObservabilityValidationError("处置历史数量必须位于 1 到 500 之间")
        normalized_source_type = source_type.strip() if source_type is not None else None
        normalized_source_key = source_key.strip() if source_key is not None else None
        if source_type is not None and not normalized_source_type:
            raise ObservabilityValidationError("处置历史来源类型不能为空")
        if source_key is not None and not normalized_source_key:
            raise ObservabilityValidationError("处置历史来源键不能为空")
        if occurred_after is not None:
            if occurred_after.tzinfo is None or occurred_after.utcoffset() is None:
                raise ObservabilityValidationError("历史起始时间必须包含时区")
            occurred_after = occurred_after.astimezone(UTC)
        if occurred_before is not None:
            if occurred_before.tzinfo is None or occurred_before.utcoffset() is None:
                raise ObservabilityValidationError("历史结束时间必须包含时区")
            occurred_before = occurred_before.astimezone(UTC)
        if (
            occurred_after is not None
            and occurred_before is not None
            and occurred_after > occurred_before
        ):
            raise ObservabilityValidationError("历史起始时间不能晚于结束时间")
        return await self._repository.list_observability_alert_disposition_events(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_id=lifecycle_id,
            source_type=normalized_source_type,
            source_key=normalized_source_key,
            action=action,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            limit=limit,
        )

    async def alert_replay_reviews(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        decision: ObservabilityAlertReplayDecision | None = None,
        reason_code: ObservabilityAlertReplayReason | None = None,
        source_type: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ObservabilityCursorPage[ObservabilityAlertReplayReview]:
        """分页查询当前 Agent 的通用告警通知重放复核事件。"""
        if not 1 <= limit <= 100:
            raise ObservabilityValidationError("重放复核分页数量必须位于 1 到 100 之间")
        normalized_source = source_type.strip() if source_type is not None else None
        if source_type is not None and not normalized_source:
            raise ObservabilityValidationError("重放复核来源类型不能为空")
        if normalized_source is not None and len(normalized_source) > 80:
            raise ObservabilityValidationError("重放复核来源类型不能超过 80 个字符")
        try:
            parsed_cursor = decode_cursor(cursor)
        except InvalidCursorError as error:
            raise ObservabilityValidationError("重放复核分页游标无效") from error
        rows = await self._repository.list_observability_alert_replay_reviews(
            tenant_id=tenant_id,
            agent_id=agent_id,
            decision=decision,
            reason_code=reason_code,
            source_type=normalized_source,
            cursor=parsed_cursor,
            limit=limit + 1,
        )
        visible = rows[:limit]
        next_cursor = (
            encode_cursor(EntityCursor(visible[-1].reviewed_at, visible[-1].id))
            if len(rows) > limit and visible
            else None
        )
        return ObservabilityCursorPage(items=visible, next_cursor=next_cursor)

    async def alert_replay_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_minutes: int = 1_440,
        source_type: str | None = None,
        now: datetime | None = None,
    ) -> ObservabilityAlertReplayMetrics:
        """聚合当前 Agent 的通用告警通知重放复核结果。"""
        if not 5 <= window_minutes <= 10_080:
            raise ObservabilityValidationError("重放复核统计窗口必须位于 5 到 10080 分钟之间")
        normalized_source = source_type.strip() if source_type is not None else None
        if source_type is not None and not normalized_source:
            raise ObservabilityValidationError("重放复核来源类型不能为空")
        if normalized_source is not None and len(normalized_source) > 80:
            raise ObservabilityValidationError("重放复核来源类型不能超过 80 个字符")
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        return await self._repository.get_observability_alert_replay_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=ended_at - timedelta(minutes=window_minutes),
            window_ended_at=ended_at,
            source_type=normalized_source,
        )

    async def suppress_alert(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
        reason: str,
        expires_at: datetime,
        actor_id: UUID,
        confirmed: bool,
        now: datetime | None = None,
    ) -> ObservabilityAlertDisposition:
        return await self._set_alert_disposition(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_id=lifecycle_id,
            status=ObservabilityAlertDispositionStatus.SUPPRESSED,
            reason=reason,
            expires_at=expires_at,
            actor_id=actor_id,
            confirmed=confirmed,
            now=now,
        )

    async def clear_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
        actor_id: UUID,
        confirmed: bool,
    ) -> ObservabilityAlertDisposition:
        if not confirmed:
            raise ObservabilityValidationError("解除通用告警处置必须明确确认")
        lifecycle = await self._get_lifecycle(
            tenant_id=tenant_id, agent_id=agent_id, lifecycle_id=lifecycle_id
        )
        removed = await self._repository.clear_observability_alert_disposition(
            tenant_id=tenant_id,
            agent_id=agent_id,
            source_type=lifecycle.source_type,
            source_key=lifecycle.source_key,
            actor_id=actor_id,
            lifecycle_id=lifecycle.id,
            reason="管理员解除当前处置",
        )
        if removed is None:
            raise ObservabilityNotFoundError("通用告警处置不存在")
        return removed

    async def _set_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
        status: ObservabilityAlertDispositionStatus,
        reason: str,
        expires_at: datetime | None,
        actor_id: UUID,
        confirmed: bool,
        now: datetime | None,
    ) -> ObservabilityAlertDisposition:
        if not confirmed:
            raise ObservabilityValidationError("通用告警处置必须明确确认")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ObservabilityValidationError("通用告警处置原因不能为空")
        if len(normalized_reason) > 500:
            raise ObservabilityValidationError("通用告警处置原因不能超过 500 个字符")
        changed_at = (now or datetime.now(UTC)).astimezone(UTC)
        if status is ObservabilityAlertDispositionStatus.SUPPRESSED:
            if expires_at is None:
                raise ObservabilityValidationError("临时抑制必须设置到期时间")
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                raise ObservabilityValidationError("临时抑制到期时间必须包含时区")
            expires_at = expires_at.astimezone(UTC)
            if expires_at <= changed_at:
                raise ObservabilityValidationError("临时抑制到期时间必须晚于当前时间")
        lifecycle = await self._get_lifecycle(
            tenant_id=tenant_id, agent_id=agent_id, lifecycle_id=lifecycle_id
        )
        if lifecycle.status is not ObservabilityAlertLifecycleStatus.ACTIVE:
            raise ObservabilityValidationError("只能确认或抑制活动中的通用告警")
        existing = await self._repository.get_observability_alert_disposition(
            tenant_id=tenant_id,
            agent_id=agent_id,
            source_type=lifecycle.source_type,
            source_key=lifecycle.source_key,
        )
        disposition = ObservabilityAlertDisposition(
            id=existing.id if existing is not None else uuid4(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            source_type=lifecycle.source_type,
            source_key=lifecycle.source_key,
            code=lifecycle.code,
            status=status,
            reason=normalized_reason,
            actor_id=actor_id,
            expires_at=expires_at,
            created_at=existing.created_at if existing is not None else changed_at,
            updated_at=changed_at,
        )
        try:
            return await self._repository.save_observability_alert_disposition(
                disposition, lifecycle_id=lifecycle.id
            )
        except LookupError as error:
            raise ObservabilityNotFoundError("活动通用告警不存在") from error

    async def _get_lifecycle(
        self, *, tenant_id: UUID, agent_id: UUID, lifecycle_id: UUID
    ) -> ObservabilityAlertLifecycle:
        lifecycle = await self._repository.get_observability_alert_lifecycle(
            tenant_id=tenant_id, agent_id=agent_id, lifecycle_id=lifecycle_id
        )
        if lifecycle is None:
            raise ObservabilityNotFoundError("通用告警生命周期不存在")
        return lifecycle

    @staticmethod
    def _attach_dispositions(
        lifecycles: tuple[ObservabilityAlertLifecycle, ...],
        dispositions: tuple[ObservabilityAlertDisposition, ...],
        evaluated_at: datetime,
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        by_key = {
            (item.source_type, item.source_key): item
            for item in dispositions
            if item.status is ObservabilityAlertDispositionStatus.ACKNOWLEDGED
            or (
                item.status is ObservabilityAlertDispositionStatus.SUPPRESSED
                and item.expires_at is not None
                and item.expires_at > evaluated_at
            )
        }
        return tuple(
            replace(
                lifecycle,
                disposition_status=disposition.status if disposition is not None else None,
                disposition_reason=disposition.reason if disposition is not None else None,
                disposition_expires_at=disposition.expires_at if disposition is not None else None,
            )
            for lifecycle in lifecycles
            for disposition in (by_key.get((lifecycle.source_type, lifecycle.source_key)),)
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
