"""框架无关的性能、成本、SLO 与确定性告警应用服务。"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from math import ceil, sqrt
from statistics import median
from typing import Literal, Protocol
from uuid import UUID, uuid4

from cnb_application.configuration_service import ConfigurationConflictError, ConfigurationService
from cnb_application.data_lifecycle_service import ObservabilityHistoryRepository
from cnb_application.pagination import (
    EntityCursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)
from cnb_domain import (
    ActiveAlert,
    AlertSeverity,
    ConfigEntry,
    ConfigScope,
    ConfigVersion,
    JsonValue,
    ObservabilityAlertCalibrationAnalysis,
    ObservabilityAlertCalibrationGroup,
    ObservabilityAlertCalibrationProposal,
    ObservabilityAlertCalibrationReplayAnalysis,
    ObservabilityAlertCalibrationReplayProposal,
    ObservabilityAlertCalibrationRule,
    ObservabilityAlertCalibrationStatus,
    ObservabilityAlertDisposition,
    ObservabilityAlertDispositionAction,
    ObservabilityAlertDispositionEvent,
    ObservabilityAlertDispositionStatus,
    ObservabilityAlertLifecycle,
    ObservabilityAlertLifecycleStatus,
    ObservabilityAlertRecommendationAction,
    ObservabilityAlertRecommendationActionMetrics,
    ObservabilityAlertRecommendationFeedback,
    ObservabilityAlertRecommendationFeedbackDecision,
    ObservabilityAlertRecommendationGuardrail,
    ObservabilityAlertRecommendationPriority,
    ObservabilityAlertRecommendationQualityMetrics,
    ObservabilityAlertRecommendationReason,
    ObservabilityAlertRecommendationSourceMetrics,
    ObservabilityAlertReplayActionMetrics,
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


ObservabilityAlertBaselineMetric = Literal["opened", "escalated"]
ObservabilityAlertHandoffReason = Literal[
    "critical",
    "unacknowledged",
    "escalated",
    "suppression_expiring",
]

ALERT_OPERATIONS_SUMMARY_MAX_RECORDS = 10_000
ALERT_RECOMMENDATION_SOURCE_SCAN_LIMIT = 500
ALERT_RECOMMENDATION_QUALITY_MAX_RECORDS = 10_000
ALERT_RECOMMENDATION_CALIBRATION_CONFIDENCE_Z = 1.96

_CALIBRATION_KEYS = {
    ObservabilityAlertCalibrationRule.LONG_RUNNING: (
        "alerts.recommendation.long_running_minutes",
        525_600,
    ),
    ObservabilityAlertCalibrationRule.REPEATED_WARNING: (
        "alerts.recommendation.minimum_repeated_occurrences",
        10_000,
    ),
}


@dataclass(frozen=True, slots=True)
class ObservabilityAlertBaselineSignal:
    """告警事件在当前窗口相对历史稳健基线的偏离。"""

    metric: ObservabilityAlertBaselineMetric
    source_type: str | None
    current_value: int
    baseline_median: float
    baseline_mad: float
    threshold_value: float
    anomalous: bool
    samples: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertBaseline:
    """当前 Agent 的等长历史窗口稳健基线。"""

    window_started_at: datetime
    window_ended_at: datetime
    window_minutes: int
    periods: int
    sensitivity: float
    minimum_current_count: int
    signals: tuple[ObservabilityAlertBaselineSignal, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertHandoffSource:
    """值班窗口内单一告警来源的安全聚合。"""

    source_type: str
    active: int
    critical_active: int
    unacknowledged_active: int
    opened: int
    resolved: int
    escalated: int


@dataclass(frozen=True, slots=True)
class ObservabilityAlertHandoffItem:
    """值班交接的优先关注告警，不包含自由文本处置备注。"""

    lifecycle_id: UUID
    source_type: str
    source_key: str
    code: str
    severity: AlertSeverity
    escalation_level: int
    active_minutes: int
    disposition_status: ObservabilityAlertDispositionStatus | None
    disposition_expires_at: datetime | None
    reason_codes: tuple[ObservabilityAlertHandoffReason, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertHandoff:
    """当前 Agent 的值班交接摘要。"""

    window_started_at: datetime
    window_ended_at: datetime
    active: int
    critical_active: int
    unacknowledged_active: int
    acknowledged_active: int
    suppressed_active: int
    opened: int
    resolved: int
    escalated: int
    blocked_replays: int
    sources: tuple[ObservabilityAlertHandoffSource, ...]
    priority_items: tuple[ObservabilityAlertHandoffItem, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertOperationsSummary:
    """异常基线与值班交接的统一安全读模型。"""

    generated_at: datetime
    baseline: ObservabilityAlertBaseline
    handoff: ObservabilityAlertHandoff


@dataclass(frozen=True, slots=True)
class ObservabilityAlertRecommendation:
    """不执行副作用的确定性告警处置建议。"""

    lifecycle_id: UUID
    source_type: str
    source_key: str
    code: str
    severity: AlertSeverity
    action: ObservabilityAlertRecommendationAction
    priority: ObservabilityAlertRecommendationPriority
    confidence: float
    reason_codes: tuple[ObservabilityAlertRecommendationReason, ...]
    guardrail_codes: tuple[ObservabilityAlertRecommendationGuardrail, ...]
    active_minutes: int
    occurrences: int
    escalation_level: int
    baseline_anomalous: bool
    suggested_suppression_minutes: int | None
    requires_confirmation: bool = True
    automation_allowed: bool = False


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


class ObservabilityConflictError(RuntimeError):
    """建议反馈与已有事实冲突。"""


class ObservabilityAuditRecorder(Protocol):
    """记录不含原始反馈和敏感材料的可观测审计事件。"""

    async def record_audit(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: str | None,
        detail: Mapping[str, JsonValue],
    ) -> None: ...


class ObservabilityRepository(ObservabilityHistoryRepository, Protocol):
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
        limit: int | None = None,
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
        limit: int | None = None,
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

    async def get_observability_alert_recommendation_feedback(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
    ) -> ObservabilityAlertRecommendationFeedback | None: ...

    async def save_observability_alert_recommendation_feedback(
        self, feedback: ObservabilityAlertRecommendationFeedback
    ) -> ObservabilityAlertRecommendationFeedback: ...

    async def list_observability_alert_recommendation_feedback(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...] | None = None,
        window_started_at: datetime | None = None,
        window_ended_at: datetime | None = None,
        source_type: str | None = None,
        limit: int | None = None,
    ) -> tuple[ObservabilityAlertRecommendationFeedback, ...]: ...

    async def count_observability_alert_lifecycle_statuses(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
    ) -> dict[ObservabilityAlertLifecycleStatus, int]: ...

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
        audit_recorder: ObservabilityAuditRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._configuration_service = configuration_service
        self._audit_recorder = audit_recorder

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
        # 将分析窗口固定到分钟边界，避免同一管理操作因请求间隔几秒而产生不同证据。
        ended_at = (now or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
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

    async def alert_operations_summary(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        now: datetime | None = None,
    ) -> ObservabilityAlertOperationsSummary:
        """计算当前 Agent 的稳健异常基线与值班交接摘要。"""
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        window_minutes = self._integer(
            configuration.values["alerts.baseline.window_minutes"],
            "alerts.baseline.window_minutes",
        )
        periods = self._integer(
            configuration.values["alerts.baseline.periods"],
            "alerts.baseline.periods",
        )
        sensitivity = self._number(
            configuration.values["alerts.baseline.sensitivity"],
            "alerts.baseline.sensitivity",
        )
        minimum_current_count = self._integer(
            configuration.values["alerts.baseline.minimum_current_count"],
            "alerts.baseline.minimum_current_count",
        )
        window_delta = timedelta(minutes=window_minutes)
        started_at = ended_at - window_delta
        history_started_at = started_at - window_delta * periods
        rows = await self._repository.list_observability_alert_lifecycles_in_window(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=history_started_at,
            window_ended_at=ended_at,
            limit=ALERT_OPERATIONS_SUMMARY_MAX_RECORDS + 1,
        )
        if len(rows) > ALERT_OPERATIONS_SUMMARY_MAX_RECORDS:
            raise ObservabilityValidationError(
                "告警运营摘要超过 10000 条生命周期安全上限，请缩短基线窗口或清理历史后重试"
            )
        dispositions = await self._repository.list_observability_alert_dispositions(
            tenant_id=tenant_id,
            agent_id=agent_id,
            limit=ALERT_OPERATIONS_SUMMARY_MAX_RECORDS + 1,
        )
        if len(dispositions) > ALERT_OPERATIONS_SUMMARY_MAX_RECORDS:
            raise ObservabilityValidationError(
                "告警运营摘要超过 10000 条处置记录安全上限，请清理历史后重试"
            )
        enriched = self._attach_dispositions(tuple(rows), dispositions, ended_at)
        replay_metrics = await self._repository.get_observability_alert_replay_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=started_at,
            window_ended_at=ended_at,
        )
        return ObservabilityAlertOperationsSummary(
            generated_at=ended_at,
            baseline=self._build_alert_baseline(
                rows=tuple(rows),
                started_at=started_at,
                ended_at=ended_at,
                window_delta=window_delta,
                window_minutes=window_minutes,
                periods=periods,
                sensitivity=sensitivity,
                minimum_current_count=minimum_current_count,
            ),
            handoff=self._build_alert_handoff(
                rows=enriched,
                started_at=started_at,
                ended_at=ended_at,
                blocked_replays=replay_metrics.blocked,
            ),
        )

    async def alert_recommendations(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        source_type: str | None = None,
        severity: AlertSeverity | None = None,
        action: ObservabilityAlertRecommendationAction | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> tuple[ObservabilityAlertRecommendation, ...]:
        """生成当前 Agent 的只读建议，并在任何处置副作用前停止。"""
        if not 1 <= limit <= 100:
            raise ObservabilityValidationError("告警处置建议数量必须位于 1 到 100 之间")
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        summary = await self.alert_operations_summary(
            tenant_id=tenant_id,
            agent_id=agent_id,
            now=evaluated_at,
        )
        normalized_source = source_type.strip() if source_type is not None else None
        if source_type is not None and not normalized_source:
            raise ObservabilityValidationError("通用告警来源类型不能为空")
        if normalized_source is not None and len(normalized_source) > 80:
            raise ObservabilityValidationError("通用告警来源类型不能超过 80 个字符")
        rows = await self._repository.list_observability_alert_lifecycles(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=ObservabilityAlertLifecycleStatus.ACTIVE,
            source_type=normalized_source,
            severity=severity,
            limit=ALERT_RECOMMENDATION_SOURCE_SCAN_LIMIT + 1,
            evaluated_at=evaluated_at,
        )
        if len(rows) > ALERT_RECOMMENDATION_SOURCE_SCAN_LIMIT:
            raise ObservabilityValidationError(
                "告警处置建议超过 500 条活动生命周期安全上限，请按来源或级别筛选后重试"
            )
        dispositions = await self._repository.list_observability_alert_dispositions(
            tenant_id=tenant_id, agent_id=agent_id
        )
        lifecycles = self._attach_dispositions(rows, dispositions, evaluated_at)
        feedback = await self._repository.list_observability_alert_recommendation_feedback(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_ids=tuple(item.id for item in lifecycles),
        )
        feedback_lifecycle_ids = frozenset(item.lifecycle_id for item in feedback)
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        long_running_minutes = self._integer(
            configuration.values["alerts.recommendation.long_running_minutes"],
            "alerts.recommendation.long_running_minutes",
        )
        suppression_minutes = self._integer(
            configuration.values["alerts.recommendation.suppression_minutes"],
            "alerts.recommendation.suppression_minutes",
        )
        minimum_repeated_occurrences = self._integer(
            configuration.values["alerts.recommendation.minimum_repeated_occurrences"],
            "alerts.recommendation.minimum_repeated_occurrences",
        )
        anomalous_sources = {
            signal.source_type
            for signal in summary.baseline.signals
            if signal.source_type is not None and signal.anomalous
        }
        recommendations = tuple(
            self._build_alert_recommendation(
                lifecycle=lifecycle,
                evaluated_at=evaluated_at,
                baseline_anomalous=lifecycle.source_type in anomalous_sources,
                long_running_minutes=long_running_minutes,
                suppression_minutes=suppression_minutes,
                minimum_repeated_occurrences=minimum_repeated_occurrences,
            )
            for lifecycle in lifecycles
            if lifecycle.disposition_status is None and lifecycle.id not in feedback_lifecycle_ids
        )
        matching = tuple(
            item for item in recommendations if action is None or item.action is action
        )
        priority_order = {
            ObservabilityAlertRecommendationPriority.URGENT: 0,
            ObservabilityAlertRecommendationPriority.HIGH: 1,
            ObservabilityAlertRecommendationPriority.NORMAL: 2,
        }
        return tuple(
            sorted(
                matching,
                key=lambda item: (
                    priority_order[item.priority],
                    -item.confidence,
                    -item.active_minutes,
                    str(item.lifecycle_id),
                ),
            )[:limit]
        )

    async def submit_alert_recommendation_feedback(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
        decision: ObservabilityAlertRecommendationFeedbackDecision,
        actor_id: UUID,
        confirmed: bool,
        alternative_action: ObservabilityAlertRecommendationAction | None = None,
        now: datetime | None = None,
    ) -> ObservabilityAlertRecommendationFeedback:
        """校验当前建议与人工处置事实后追加反馈。"""
        if not confirmed:
            raise ObservabilityValidationError("告警建议反馈必须显式确认")
        existing = await self._repository.get_observability_alert_recommendation_feedback(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_id=lifecycle_id,
        )
        if existing is not None:
            if existing.decision is decision:
                return existing
            raise ObservabilityConflictError("该告警生命周期已提交不同的建议反馈")
        lifecycle = await self._repository.get_observability_alert_lifecycle(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_id=lifecycle_id,
        )
        if lifecycle is None:
            raise ObservabilityNotFoundError("通用告警生命周期不存在")
        if lifecycle.status is not ObservabilityAlertLifecycleStatus.ACTIVE:
            raise ObservabilityValidationError("只能对活动告警的当前建议提交反馈")
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        summary = await self.alert_operations_summary(
            tenant_id=tenant_id,
            agent_id=agent_id,
            now=evaluated_at,
        )
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        recommendation = self._build_alert_recommendation(
            lifecycle=lifecycle,
            evaluated_at=evaluated_at,
            baseline_anomalous=any(
                signal.source_type == lifecycle.source_type and signal.anomalous
                for signal in summary.baseline.signals
            ),
            long_running_minutes=self._integer(
                configuration.values["alerts.recommendation.long_running_minutes"],
                "alerts.recommendation.long_running_minutes",
            ),
            suppression_minutes=self._integer(
                configuration.values["alerts.recommendation.suppression_minutes"],
                "alerts.recommendation.suppression_minutes",
            ),
            minimum_repeated_occurrences=self._integer(
                configuration.values["alerts.recommendation.minimum_repeated_occurrences"],
                "alerts.recommendation.minimum_repeated_occurrences",
            ),
        )
        if (
            decision is ObservabilityAlertRecommendationFeedbackDecision.ACCEPTED
            and alternative_action is not None
        ):
            raise ObservabilityValidationError("采纳建议不能同时提交替代动作")
        if alternative_action is recommendation.action:
            raise ObservabilityValidationError("替代动作必须与服务端建议不同")
        if decision is ObservabilityAlertRecommendationFeedbackDecision.ACCEPTED:
            await self._validate_accepted_recommendation(
                tenant_id=tenant_id,
                agent_id=agent_id,
                lifecycle=lifecycle,
                recommendation=recommendation,
                evaluated_at=evaluated_at,
            )
        feedback = ObservabilityAlertRecommendationFeedback(
            id=uuid4(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_id=lifecycle.id,
            source_type=lifecycle.source_type,
            source_key=lifecycle.source_key,
            code=lifecycle.code,
            recommendation_action=recommendation.action,
            priority=recommendation.priority,
            reason_codes=recommendation.reason_codes,
            decision=decision,
            actor_id=actor_id,
            feedback_at=evaluated_at,
            alternative_action=alternative_action,
        )
        try:
            return await self._repository.save_observability_alert_recommendation_feedback(feedback)
        except ValueError as error:
            raise ObservabilityConflictError(str(error)) from error

    async def alert_recommendation_quality_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_minutes: int = 10_080,
        source_type: str | None = None,
        now: datetime | None = None,
    ) -> ObservabilityAlertRecommendationQualityMetrics:
        """聚合有界建议反馈与同窗口重放复核事实。"""
        if not 5 <= window_minutes <= 525_600:
            raise ObservabilityValidationError("建议质量统计窗口必须位于 5 到 525600 分钟之间")
        normalized_source = source_type.strip() if source_type is not None else None
        if source_type is not None and not normalized_source:
            raise ObservabilityValidationError("建议质量来源类型不能为空")
        if normalized_source is not None and len(normalized_source) > 80:
            raise ObservabilityValidationError("建议质量来源类型不能超过 80 个字符")
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        started_at = ended_at - timedelta(minutes=window_minutes)
        feedback = await self._repository.list_observability_alert_recommendation_feedback(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=started_at,
            window_ended_at=ended_at,
            source_type=normalized_source,
            limit=ALERT_RECOMMENDATION_QUALITY_MAX_RECORDS + 1,
        )
        if len(feedback) > ALERT_RECOMMENDATION_QUALITY_MAX_RECORDS:
            raise ObservabilityValidationError(
                "建议质量统计超过 10000 条反馈安全上限，请缩短窗口或按来源筛选"
            )
        accepted = tuple(
            item
            for item in feedback
            if item.decision is ObservabilityAlertRecommendationFeedbackDecision.ACCEPTED
        )
        rejected = tuple(
            item
            for item in feedback
            if item.decision is ObservabilityAlertRecommendationFeedbackDecision.REJECTED
        )
        statuses = await self._repository.count_observability_alert_lifecycle_statuses(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_ids=tuple(item.lifecycle_id for item in accepted),
        )
        replay = await self._repository.get_observability_alert_replay_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=started_at,
            window_ended_at=ended_at,
            source_type=normalized_source,
        )

        def action_metrics(
            action: ObservabilityAlertRecommendationAction,
        ) -> ObservabilityAlertRecommendationActionMetrics:
            values = tuple(item for item in feedback if item.recommendation_action is action)
            return ObservabilityAlertRecommendationActionMetrics(
                action=action,
                total=len(values),
                accepted=sum(
                    item.decision is ObservabilityAlertRecommendationFeedbackDecision.ACCEPTED
                    for item in values
                ),
                rejected=sum(
                    item.decision is ObservabilityAlertRecommendationFeedbackDecision.REJECTED
                    for item in values
                ),
            )

        def source_metrics(source: str) -> ObservabilityAlertRecommendationSourceMetrics:
            values = tuple(item for item in feedback if item.source_type == source)
            return ObservabilityAlertRecommendationSourceMetrics(
                source_type=source,
                total=len(values),
                accepted=sum(
                    item.decision is ObservabilityAlertRecommendationFeedbackDecision.ACCEPTED
                    for item in values
                ),
                rejected=sum(
                    item.decision is ObservabilityAlertRecommendationFeedbackDecision.REJECTED
                    for item in values
                ),
            )

        total = len(feedback)
        return ObservabilityAlertRecommendationQualityMetrics(
            window_started_at=started_at,
            window_ended_at=ended_at,
            total=total,
            accepted=len(accepted),
            rejected=len(rejected),
            acceptance_rate_percent=round(len(accepted) * 100 / total, 2) if total else 0.0,
            accepted_resolved=statuses[ObservabilityAlertLifecycleStatus.RESOLVED],
            accepted_active=statuses[ObservabilityAlertLifecycleStatus.ACTIVE],
            replay_total=replay.total,
            replay_allowed=replay.allowed,
            replay_blocked=replay.blocked,
            actions=tuple(
                action_metrics(action) for action in ObservabilityAlertRecommendationAction
            ),
            sources=tuple(
                source_metrics(source) for source in sorted({i.source_type for i in feedback})
            ),
        )

    async def alert_recommendation_calibration_analysis(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        now: datetime | None = None,
    ) -> ObservabilityAlertCalibrationAnalysis:
        """用冻结人工反馈生成不执行调参的分组阈值评测。"""
        # 将校准窗口固定到分钟边界，保证回放证据在相邻管理请求间稳定。
        ended_at = (now or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        window_days = self._integer(
            configuration.values["alerts.recommendation.calibration.window_days"],
            "alerts.recommendation.calibration.window_days",
        )
        minimum_samples = self._integer(
            configuration.values["alerts.recommendation.calibration.minimum_samples_per_group"],
            "alerts.recommendation.calibration.minimum_samples_per_group",
        )
        target_percent = self._number(
            configuration.values[
                "alerts.recommendation.calibration.target_acceptance_rate_percent"
            ],
            "alerts.recommendation.calibration.target_acceptance_rate_percent",
        )
        started_at = ended_at - timedelta(days=window_days)
        feedback = await self._repository.list_observability_alert_recommendation_feedback(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=started_at,
            window_ended_at=ended_at,
            limit=ALERT_RECOMMENDATION_QUALITY_MAX_RECORDS + 1,
        )
        if len(feedback) > ALERT_RECOMMENDATION_QUALITY_MAX_RECORDS:
            raise ObservabilityValidationError(
                "建议校准分析超过 10000 条反馈安全上限，请缩短分析窗口"
            )

        eligible = tuple(
            (item, rule)
            for item in feedback
            for rule in (self._calibration_rule(item),)
            if rule is not None
        )
        grouped: dict[
            tuple[ObservabilityAlertCalibrationRule, str],
            list[ObservabilityAlertRecommendationFeedback],
        ] = {}
        by_rule: dict[
            ObservabilityAlertCalibrationRule,
            list[ObservabilityAlertRecommendationFeedback],
        ] = {rule: [] for rule in ObservabilityAlertCalibrationRule}
        for item, rule in eligible:
            grouped.setdefault((rule, item.source_type), []).append(item)
            by_rule[rule].append(item)

        groups = tuple(
            self._calibration_group(rule=rule, source_type=source_type, values=tuple(values))
            for (rule, source_type), values in sorted(
                grouped.items(), key=lambda entry: (entry[0][0].value, entry[0][1])
            )
        )
        proposals = tuple(
            self._calibration_proposal(
                rule=rule,
                values=tuple(by_rule[rule]),
                minimum_samples=minimum_samples,
                target_percent=target_percent,
                current_value=self._integer(
                    configuration.values[_CALIBRATION_KEYS[rule][0]],
                    _CALIBRATION_KEYS[rule][0],
                ),
            )
            for rule in ObservabilityAlertCalibrationRule
        )
        return ObservabilityAlertCalibrationAnalysis(
            window_started_at=started_at,
            window_ended_at=ended_at,
            configuration_version=configuration.version,
            minimum_samples_per_group=minimum_samples,
            target_acceptance_rate_percent=target_percent,
            confidence_level_percent=95.0,
            total_feedback=len(feedback),
            eligible_feedback=len(eligible),
            groups=groups,
            proposals=proposals,
            automatic_tuning_allowed=False,
        )

    async def create_alert_recommendation_calibration_draft(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        configuration_version: int,
        replay_fingerprint: str,
        replay_window_ended_at: datetime,
        confirmed: bool,
        now: datetime | None = None,
    ) -> ConfigVersion:
        """重算校准结论并只创建需另行发布的完整配置草稿。"""
        if not confirmed:
            raise ObservabilityValidationError("创建告警建议校准草稿必须显式确认")
        replay = await self.alert_recommendation_calibration_replay(
            tenant_id=tenant_id,
            agent_id=agent_id,
            now=replay_window_ended_at,
        )
        if replay.configuration_version != configuration_version:
            raise ObservabilityConflictError("生效配置已在分析后变更，请刷新校准结果后重试")
        if not hmac.compare_digest(replay.replay_fingerprint, replay_fingerprint):
            raise ObservabilityConflictError("回放证据已过期，请刷新回放结果后重试")
        analysis = await self.alert_recommendation_calibration_analysis(
            tenant_id=tenant_id,
            agent_id=agent_id,
            now=replay_window_ended_at,
        )
        if analysis.configuration_version != configuration_version:
            raise ObservabilityConflictError("生效配置已在分析后变更，请刷新校准结果后重试")
        changes = tuple(
            proposal
            for proposal in analysis.proposals
            if proposal.status is ObservabilityAlertCalibrationStatus.TIGHTEN
            and proposal.proposed_value != proposal.current_value
        )
        if not changes:
            raise ObservabilityValidationError("当前反馈证据没有可创建的阈值变更草稿")
        try:
            draft = await self._configuration_service.create_derived_draft(
                expected_base_version=configuration_version,
                note=f"告警建议离线校准：基于 {analysis.eligible_feedback} 条合格人工反馈",
                overrides=tuple(
                    ConfigEntry(
                        key=proposal.configuration_key,
                        scope_type=ConfigScope.AGENT,
                        scope_id=agent_id,
                        value=proposal.proposed_value,
                    )
                    for proposal in changes
                ),
                actor_id=actor_id,
            )
        except ConfigurationConflictError as error:
            raise ObservabilityConflictError(str(error)) from error
        if self._audit_recorder is not None:
            await self._audit_recorder.record_audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="observability.alert_calibration_draft_created",
                resource_type="configuration_version",
                resource_id=str(draft.id),
                detail={
                    "configuration_version": configuration_version,
                    "replay_window_started_at": replay.window_started_at.isoformat(),
                    "replay_window_ended_at": replay.window_ended_at.isoformat(),
                    "replay_fingerprint": replay.replay_fingerprint,
                    "proposal_count": len(changes),
                    "eligible_feedback": analysis.eligible_feedback,
                },
            )
        return draft

    async def alert_recommendation_calibration_replay(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        now: datetime | None = None,
    ) -> ObservabilityAlertCalibrationReplayAnalysis:
        """基于生命周期聚合事实生成不证明因果关系的候选阈值场景回放。"""
        analysis = await self.alert_recommendation_calibration_analysis(
            tenant_id=tenant_id,
            agent_id=agent_id,
            now=now,
        )
        lifecycles = await self._repository.list_observability_alert_lifecycles_in_window(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=analysis.window_started_at,
            window_ended_at=analysis.window_ended_at,
            limit=ALERT_RECOMMENDATION_QUALITY_MAX_RECORDS + 1,
        )
        if len(lifecycles) > ALERT_RECOMMENDATION_QUALITY_MAX_RECORDS:
            raise ObservabilityValidationError(
                "告警阈值场景回放超过 10000 条生命周期安全上限，请缩短分析窗口"
            )
        feedback = await self._repository.list_observability_alert_recommendation_feedback(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=analysis.window_started_at,
            window_ended_at=analysis.window_ended_at,
            limit=ALERT_RECOMMENDATION_QUALITY_MAX_RECORDS + 1,
        )
        if len(feedback) > ALERT_RECOMMENDATION_QUALITY_MAX_RECORDS:
            raise ObservabilityValidationError(
                "告警阈值场景回放超过 10000 条反馈安全上限，请缩短分析窗口"
            )
        lifecycle_by_id = {item.id: item for item in lifecycles}
        eligible = tuple(
            (item, rule)
            for item in feedback
            for rule in (self._calibration_rule(item),)
            if rule is not None
        )
        replay_proposals: list[ObservabilityAlertCalibrationReplayProposal] = []
        for proposal in analysis.proposals:
            values = tuple(item for item, rule in eligible if rule is proposal.rule)
            current_triggered = candidate_triggered = avoided = retained = 0
            retained_accepted = retained_rejected = missing = 0
            action_counts = {action: 0 for action in ObservabilityAlertRecommendationAction}
            for item in values:
                lifecycle = lifecycle_by_id.get(item.lifecycle_id)
                if lifecycle is None:
                    missing += 1
                    continue
                observed_value = self._replay_observed_value(
                    proposal.rule, lifecycle, analysis.window_ended_at
                )
                current_hit = observed_value >= proposal.current_value
                candidate_hit = observed_value >= proposal.proposed_value
                current_triggered += current_hit
                candidate_triggered += candidate_hit
                avoided += current_hit and not candidate_hit
                retained += candidate_hit
                if candidate_hit:
                    accepted = (
                        item.decision is ObservabilityAlertRecommendationFeedbackDecision.ACCEPTED
                    )
                    rejected = (
                        item.decision is ObservabilityAlertRecommendationFeedbackDecision.REJECTED
                    )
                    retained_accepted += accepted
                    retained_rejected += rejected
                    if item.alternative_action is not None:
                        action_counts[item.alternative_action] += 1
            replay_proposals.append(
                ObservabilityAlertCalibrationReplayProposal(
                    rule=proposal.rule,
                    configuration_key=proposal.configuration_key,
                    current_value=proposal.current_value,
                    candidate_value=proposal.proposed_value,
                    sample_size=len(values),
                    lifecycle_facts=len(values) - missing,
                    missing_lifecycle_facts=missing,
                    current_triggered=current_triggered,
                    candidate_triggered=candidate_triggered,
                    avoided=avoided,
                    retained=retained,
                    retained_accepted=retained_accepted,
                    retained_rejected=retained_rejected,
                    alternative_actions=tuple(
                        ObservabilityAlertReplayActionMetrics(
                            action=action, total=action_counts[action]
                        )
                        for action in ObservabilityAlertRecommendationAction
                    ),
                )
            )
        return ObservabilityAlertCalibrationReplayAnalysis(
            window_started_at=analysis.window_started_at,
            window_ended_at=analysis.window_ended_at,
            configuration_version=analysis.configuration_version,
            total_feedback=analysis.total_feedback,
            eligible_feedback=analysis.eligible_feedback,
            proposals=tuple(replay_proposals),
            replay_fingerprint=self._calibration_replay_fingerprint(
                configuration_version=analysis.configuration_version,
                window_started_at=analysis.window_started_at,
                window_ended_at=analysis.window_ended_at,
                total_feedback=analysis.total_feedback,
                eligible_feedback=analysis.eligible_feedback,
                proposals=tuple(replay_proposals),
            ),
        )

    @staticmethod
    def _calibration_replay_fingerprint(
        *,
        configuration_version: int,
        window_started_at: datetime,
        window_ended_at: datetime,
        total_feedback: int,
        eligible_feedback: int,
        proposals: tuple[ObservabilityAlertCalibrationReplayProposal, ...],
    ) -> str:
        """对回放安全聚合结果做稳定哈希，不纳入正文或运行载荷。"""
        payload = {
            "configuration_version": configuration_version,
            "window_started_at": window_started_at.isoformat(),
            "window_ended_at": window_ended_at.isoformat(),
            "total_feedback": total_feedback,
            "eligible_feedback": eligible_feedback,
            "proposals": [
                {
                    "rule": proposal.rule.value,
                    "configuration_key": proposal.configuration_key,
                    "current_value": proposal.current_value,
                    "candidate_value": proposal.candidate_value,
                    "sample_size": proposal.sample_size,
                    "lifecycle_facts": proposal.lifecycle_facts,
                    "missing_lifecycle_facts": proposal.missing_lifecycle_facts,
                    "current_triggered": proposal.current_triggered,
                    "candidate_triggered": proposal.candidate_triggered,
                    "avoided": proposal.avoided,
                    "retained": proposal.retained,
                    "retained_accepted": proposal.retained_accepted,
                    "retained_rejected": proposal.retained_rejected,
                    "alternative_actions": [
                        {"action": item.action.value, "total": item.total}
                        for item in proposal.alternative_actions
                    ],
                }
                for proposal in proposals
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _replay_observed_value(
        rule: ObservabilityAlertCalibrationRule,
        lifecycle: ObservabilityAlertLifecycle,
        ended_at: datetime,
    ) -> int:
        if rule is ObservabilityAlertCalibrationRule.REPEATED_WARNING:
            return lifecycle.occurrences
        return max(
            0,
            int(
                ((lifecycle.resolved_at or ended_at) - lifecycle.first_occurred_at).total_seconds()
                // 60
            ),
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

    @classmethod
    def _build_alert_baseline(
        cls,
        *,
        rows: tuple[ObservabilityAlertLifecycle, ...],
        started_at: datetime,
        ended_at: datetime,
        window_delta: timedelta,
        window_minutes: int,
        periods: int,
        sensitivity: float,
        minimum_current_count: int,
    ) -> ObservabilityAlertBaseline:
        history_started_at = started_at - window_delta * periods
        source_types = sorted(
            {
                row.source_type
                for row in rows
                if any(
                    event_at is not None and history_started_at <= event_at <= ended_at
                    for event_at in (row.first_occurred_at, row.escalated_at)
                )
            }
        )
        signals: list[ObservabilityAlertBaselineSignal] = []
        for source_type in (None, *source_types):
            for metric in ("opened", "escalated"):
                current = cls._count_lifecycle_events(
                    rows,
                    metric=metric,
                    source_type=source_type,
                    started_at=started_at,
                    ended_at=ended_at,
                    include_end=True,
                )
                samples = tuple(
                    cls._count_lifecycle_events(
                        rows,
                        metric=metric,
                        source_type=source_type,
                        started_at=started_at - window_delta * offset,
                        ended_at=started_at - window_delta * (offset - 1),
                        include_end=False,
                    )
                    for offset in range(periods, 0, -1)
                )
                if source_type is None or current or any(samples):
                    signals.append(
                        cls._alert_baseline_signal(
                            metric=metric,
                            source_type=source_type,
                            current=current,
                            samples=samples,
                            sensitivity=sensitivity,
                            minimum_current_count=minimum_current_count,
                        )
                    )
        return ObservabilityAlertBaseline(
            window_started_at=started_at,
            window_ended_at=ended_at,
            window_minutes=window_minutes,
            periods=periods,
            sensitivity=sensitivity,
            minimum_current_count=minimum_current_count,
            signals=tuple(signals),
        )

    @staticmethod
    def _count_lifecycle_events(
        rows: tuple[ObservabilityAlertLifecycle, ...],
        *,
        metric: ObservabilityAlertBaselineMetric,
        source_type: str | None,
        started_at: datetime,
        ended_at: datetime,
        include_end: bool,
    ) -> int:
        def in_window(event_at: datetime | None) -> bool:
            if event_at is None:
                return False
            return (
                started_at <= event_at <= ended_at
                if include_end
                else started_at <= event_at < ended_at
            )

        return sum(
            1
            for row in rows
            if source_type is None or row.source_type == source_type
            if in_window(row.first_occurred_at if metric == "opened" else row.escalated_at)
        )

    @staticmethod
    def _alert_baseline_signal(
        *,
        metric: ObservabilityAlertBaselineMetric,
        source_type: str | None,
        current: int,
        samples: tuple[int, ...],
        sensitivity: float,
        minimum_current_count: int,
    ) -> ObservabilityAlertBaselineSignal:
        baseline_median = float(median(samples))
        baseline_mad = float(median(tuple(abs(item - baseline_median) for item in samples)))
        robust_deviation = max(1.0, 1.4826 * baseline_mad)
        threshold = round(
            max(
                float(minimum_current_count),
                baseline_median + sensitivity * robust_deviation,
            ),
            4,
        )
        return ObservabilityAlertBaselineSignal(
            metric=metric,
            source_type=source_type,
            current_value=current,
            baseline_median=round(baseline_median, 4),
            baseline_mad=round(baseline_mad, 4),
            threshold_value=threshold,
            anomalous=current >= threshold,
            samples=samples,
        )

    @classmethod
    def _build_alert_handoff(
        cls,
        *,
        rows: tuple[ObservabilityAlertLifecycle, ...],
        started_at: datetime,
        ended_at: datetime,
        blocked_replays: int,
    ) -> ObservabilityAlertHandoff:
        def in_window(event_at: datetime | None) -> bool:
            return event_at is not None and started_at <= event_at <= ended_at

        active_rows = tuple(
            row for row in rows if row.status is ObservabilityAlertLifecycleStatus.ACTIVE
        )
        source_types = sorted(
            {
                row.source_type
                for row in rows
                if row.status is ObservabilityAlertLifecycleStatus.ACTIVE
                or in_window(row.first_occurred_at)
                or in_window(row.resolved_at)
                or in_window(row.escalated_at)
            }
        )
        sources = tuple(
            ObservabilityAlertHandoffSource(
                source_type=source_type,
                active=sum(row.source_type == source_type for row in active_rows),
                critical_active=sum(
                    row.source_type == source_type and row.severity is AlertSeverity.CRITICAL
                    for row in active_rows
                ),
                unacknowledged_active=sum(
                    row.source_type == source_type and row.disposition_status is None
                    for row in active_rows
                ),
                opened=sum(
                    row.source_type == source_type and in_window(row.first_occurred_at)
                    for row in rows
                ),
                resolved=sum(
                    row.source_type == source_type and in_window(row.resolved_at) for row in rows
                ),
                escalated=sum(
                    row.source_type == source_type and in_window(row.escalated_at) for row in rows
                ),
            )
            for source_type in source_types
        )
        priority_rows = sorted(
            active_rows,
            key=lambda row: (
                row.severity is not AlertSeverity.CRITICAL,
                row.disposition_status is not None,
                -row.escalation_level,
                row.first_occurred_at,
                str(row.id),
            ),
        )[:10]
        priority_items: list[ObservabilityAlertHandoffItem] = []
        suppression_expiry_limit = ended_at + (ended_at - started_at)
        for row in priority_rows:
            reasons: list[ObservabilityAlertHandoffReason] = []
            if row.severity is AlertSeverity.CRITICAL:
                reasons.append("critical")
            if row.disposition_status is None:
                reasons.append("unacknowledged")
            if row.escalation_level > 0:
                reasons.append("escalated")
            if (
                row.disposition_status is ObservabilityAlertDispositionStatus.SUPPRESSED
                and row.disposition_expires_at is not None
                and row.disposition_expires_at <= suppression_expiry_limit
            ):
                reasons.append("suppression_expiring")
            priority_items.append(
                ObservabilityAlertHandoffItem(
                    lifecycle_id=row.id,
                    source_type=row.source_type,
                    source_key=row.source_key,
                    code=row.code,
                    severity=row.severity,
                    escalation_level=row.escalation_level,
                    active_minutes=max(
                        0,
                        int((ended_at - row.first_occurred_at).total_seconds() // 60),
                    ),
                    disposition_status=row.disposition_status,
                    disposition_expires_at=row.disposition_expires_at,
                    reason_codes=tuple(reasons),
                )
            )
        return ObservabilityAlertHandoff(
            window_started_at=started_at,
            window_ended_at=ended_at,
            active=len(active_rows),
            critical_active=sum(row.severity is AlertSeverity.CRITICAL for row in active_rows),
            unacknowledged_active=sum(row.disposition_status is None for row in active_rows),
            acknowledged_active=sum(
                row.disposition_status is ObservabilityAlertDispositionStatus.ACKNOWLEDGED
                for row in active_rows
            ),
            suppressed_active=sum(
                row.disposition_status is ObservabilityAlertDispositionStatus.SUPPRESSED
                for row in active_rows
            ),
            opened=sum(in_window(row.first_occurred_at) for row in rows),
            resolved=sum(in_window(row.resolved_at) for row in rows),
            escalated=sum(in_window(row.escalated_at) for row in rows),
            blocked_replays=blocked_replays,
            sources=sources,
            priority_items=tuple(priority_items),
        )

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

    @staticmethod
    def _build_alert_recommendation(
        *,
        lifecycle: ObservabilityAlertLifecycle,
        evaluated_at: datetime,
        baseline_anomalous: bool,
        long_running_minutes: int,
        suppression_minutes: int,
        minimum_repeated_occurrences: int,
    ) -> ObservabilityAlertRecommendation:
        active_minutes = max(
            0,
            int((evaluated_at - lifecycle.first_occurred_at).total_seconds() // 60),
        )
        investigation_reasons: list[ObservabilityAlertRecommendationReason] = []
        if lifecycle.severity is AlertSeverity.CRITICAL:
            investigation_reasons.append(ObservabilityAlertRecommendationReason.CRITICAL)
        if lifecycle.escalation_level > 0:
            investigation_reasons.append(ObservabilityAlertRecommendationReason.ESCALATED)
        if baseline_anomalous:
            investigation_reasons.append(ObservabilityAlertRecommendationReason.BASELINE_ANOMALY)
        if active_minutes >= long_running_minutes:
            investigation_reasons.append(ObservabilityAlertRecommendationReason.LONG_RUNNING)

        if investigation_reasons:
            urgent = lifecycle.severity is AlertSeverity.CRITICAL and (
                lifecycle.escalation_level > 0 or baseline_anomalous
            )
            confidence = min(
                0.99,
                0.72
                + (0.1 if lifecycle.severity is AlertSeverity.CRITICAL else 0)
                + (0.08 if lifecycle.escalation_level > 0 else 0)
                + (0.06 if baseline_anomalous else 0)
                + (0.03 if active_minutes >= long_running_minutes else 0),
            )
            recommendation_action = ObservabilityAlertRecommendationAction.ACKNOWLEDGE
            priority = (
                ObservabilityAlertRecommendationPriority.URGENT
                if urgent
                else ObservabilityAlertRecommendationPriority.HIGH
            )
            reason_codes = tuple(investigation_reasons)
            suggested_minutes = None
        elif lifecycle.occurrences >= minimum_repeated_occurrences:
            recommendation_action = ObservabilityAlertRecommendationAction.SUPPRESS
            priority = ObservabilityAlertRecommendationPriority.NORMAL
            confidence = 0.72
            reason_codes = (ObservabilityAlertRecommendationReason.REPEATED_WARNING,)
            suggested_minutes = suppression_minutes
        else:
            recommendation_action = ObservabilityAlertRecommendationAction.OBSERVE
            priority = ObservabilityAlertRecommendationPriority.NORMAL
            confidence = 0.55
            reason_codes = (ObservabilityAlertRecommendationReason.INSUFFICIENT_SIGNAL,)
            suggested_minutes = None

        return ObservabilityAlertRecommendation(
            lifecycle_id=lifecycle.id,
            source_type=lifecycle.source_type,
            source_key=lifecycle.source_key,
            code=lifecycle.code,
            severity=lifecycle.severity,
            action=recommendation_action,
            priority=priority,
            confidence=round(confidence, 2),
            reason_codes=reason_codes,
            guardrail_codes=(
                ObservabilityAlertRecommendationGuardrail.MANUAL_CONFIRMATION_REQUIRED,
                ObservabilityAlertRecommendationGuardrail.AUTOMATIC_EXECUTION_FORBIDDEN,
                ObservabilityAlertRecommendationGuardrail.CURRENT_SCOPE_ONLY,
            ),
            active_minutes=active_minutes,
            occurrences=lifecycle.occurrences,
            escalation_level=lifecycle.escalation_level,
            baseline_anomalous=baseline_anomalous,
            suggested_suppression_minutes=suggested_minutes,
        )

    @staticmethod
    def _calibration_rule(
        feedback: ObservabilityAlertRecommendationFeedback,
    ) -> ObservabilityAlertCalibrationRule | None:
        """只接受可单独归因到一个配置阈值的冻结反馈。"""
        if (
            feedback.recommendation_action is ObservabilityAlertRecommendationAction.ACKNOWLEDGE
            and feedback.reason_codes == (ObservabilityAlertRecommendationReason.LONG_RUNNING,)
        ):
            return ObservabilityAlertCalibrationRule.LONG_RUNNING
        if (
            feedback.recommendation_action is ObservabilityAlertRecommendationAction.SUPPRESS
            and feedback.reason_codes == (ObservabilityAlertRecommendationReason.REPEATED_WARNING,)
        ):
            return ObservabilityAlertCalibrationRule.REPEATED_WARNING
        return None

    @classmethod
    def _calibration_group(
        cls,
        *,
        rule: ObservabilityAlertCalibrationRule,
        source_type: str,
        values: tuple[ObservabilityAlertRecommendationFeedback, ...],
    ) -> ObservabilityAlertCalibrationGroup:
        accepted = sum(
            item.decision is ObservabilityAlertRecommendationFeedbackDecision.ACCEPTED
            for item in values
        )
        lower, upper = cls._wilson_interval(accepted=accepted, total=len(values))
        return ObservabilityAlertCalibrationGroup(
            rule=rule,
            source_type=source_type,
            total=len(values),
            accepted=accepted,
            rejected=len(values) - accepted,
            acceptance_rate_percent=round(accepted * 100 / len(values), 2),
            confidence_lower_percent=lower,
            confidence_upper_percent=upper,
        )

    @classmethod
    def _calibration_proposal(
        cls,
        *,
        rule: ObservabilityAlertCalibrationRule,
        values: tuple[ObservabilityAlertRecommendationFeedback, ...],
        minimum_samples: int,
        target_percent: float,
        current_value: int,
    ) -> ObservabilityAlertCalibrationProposal:
        accepted = sum(
            item.decision is ObservabilityAlertRecommendationFeedbackDecision.ACCEPTED
            for item in values
        )
        total = len(values)
        acceptance_rate = round(accepted * 100 / total, 2) if total else 0.0
        lower, upper = cls._wilson_interval(accepted=accepted, total=total)
        status = ObservabilityAlertCalibrationStatus.INSUFFICIENT_DATA
        proposed_value = current_value
        if total >= minimum_samples:
            if upper < target_percent:
                maximum = _CALIBRATION_KEYS[rule][1]
                candidate = (
                    current_value + max(5, ceil(current_value * 0.25))
                    if rule is ObservabilityAlertCalibrationRule.LONG_RUNNING
                    else current_value + 1
                )
                proposed_value = min(maximum, candidate)
                status = (
                    ObservabilityAlertCalibrationStatus.TIGHTEN
                    if proposed_value > current_value
                    else ObservabilityAlertCalibrationStatus.LIMIT_REACHED
                )
            elif lower > target_percent:
                status = ObservabilityAlertCalibrationStatus.KEEP
            else:
                status = ObservabilityAlertCalibrationStatus.INCONCLUSIVE
        return ObservabilityAlertCalibrationProposal(
            rule=rule,
            configuration_key=_CALIBRATION_KEYS[rule][0],
            current_value=current_value,
            proposed_value=proposed_value,
            status=status,
            sample_size=total,
            acceptance_rate_percent=acceptance_rate,
            confidence_lower_percent=lower,
            confidence_upper_percent=upper,
        )

    @staticmethod
    def _wilson_interval(*, accepted: int, total: int) -> tuple[float, float]:
        """返回二项比例的双侧 95% Wilson score 区间百分比。"""
        if total == 0:
            return 0.0, 100.0
        z = ALERT_RECOMMENDATION_CALIBRATION_CONFIDENCE_Z
        proportion = accepted / total
        denominator = 1 + z**2 / total
        center = (proportion + z**2 / (2 * total)) / denominator
        margin = (
            z * sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2)) / denominator
        )
        return round(max(0.0, center - margin) * 100, 2), round(
            min(1.0, center + margin) * 100,
            2,
        )

    async def _validate_accepted_recommendation(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle: ObservabilityAlertLifecycle,
        recommendation: ObservabilityAlertRecommendation,
        evaluated_at: datetime,
    ) -> None:
        if recommendation.action is ObservabilityAlertRecommendationAction.OBSERVE:
            return
        disposition = await self._repository.get_observability_alert_disposition(
            tenant_id=tenant_id,
            agent_id=agent_id,
            source_type=lifecycle.source_type,
            source_key=lifecycle.source_key,
        )
        if disposition is None:
            raise ObservabilityValidationError("采纳处置建议前必须先完成匹配的人工处置")
        if recommendation.action is ObservabilityAlertRecommendationAction.ACKNOWLEDGE:
            if disposition.status is not ObservabilityAlertDispositionStatus.ACKNOWLEDGED:
                raise ObservabilityValidationError("采纳确认建议前必须先人工确认当前告警")
            return
        if (
            disposition.status is not ObservabilityAlertDispositionStatus.SUPPRESSED
            or disposition.expires_at is None
            or disposition.expires_at <= evaluated_at
        ):
            raise ObservabilityValidationError("采纳抑制建议前必须先完成有效的人工临时抑制")

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
