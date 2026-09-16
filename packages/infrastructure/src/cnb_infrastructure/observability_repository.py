"""可观测请求指标写入与 PostgreSQL 租户、Agent 隔离聚合。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from cnb_application import (
    ApiRequestObservation,
    EntityCursor,
    ObservabilityAlertHistorySnapshot,
    ObservabilityAlertLifecycleReconciliation,
    ObservabilityHistoryRetentionResult,
)
from cnb_domain import (
    ActiveAlert,
    AgentRunSloMetrics,
    AlertSeverity,
    ApiSloMetrics,
    ChannelDeliveryMetrics,
    LatencyPercentiles,
    ModelUsageMetrics,
    NotificationDeliveryMetrics,
    ObservabilityAlertDisposition,
    ObservabilityAlertDispositionAction,
    ObservabilityAlertDispositionEvent,
    ObservabilityAlertDispositionStatus,
    ObservabilityAlertLifecycle,
    ObservabilityAlertLifecycleStatus,
    ObservabilityAlertRecommendationAction,
    ObservabilityAlertRecommendationFeedback,
    ObservabilityAlertRecommendationFeedbackDecision,
    ObservabilityAlertRecommendationPriority,
    ObservabilityAlertRecommendationReason,
    ObservabilityAlertReplayDecision,
    ObservabilityAlertReplayMetrics,
    ObservabilityAlertReplayReason,
    ObservabilityAlertReplayReasonMetrics,
    ObservabilityAlertReplayReview,
    ObservabilityAlertReplaySourceMetrics,
    ObservabilityMetrics,
    QueueMetrics,
)
from cnb_infrastructure.models import (
    Agent,
    AgentRunModel,
    ApiRequestMetricModel,
    AuditLog,
    BackgroundJobModel,
    ChannelDiagnosticEventModel,
    ChannelInstanceModel,
    ModelInvocationModel,
    ObservabilityAlertDispositionEventModel,
    ObservabilityAlertDispositionModel,
    ObservabilityAlertLifecycleModel,
    ObservabilityAlertRecommendationFeedbackModel,
    ObservabilityAlertReplayReviewModel,
)


class MemoryObservabilityRepository:
    """供测试与无数据库联调使用的请求指标仓储。"""

    def __init__(self) -> None:
        self._requests: list[ApiRequestObservation] = []
        self._lifecycles: dict[UUID, ObservabilityAlertLifecycle] = {}
        self._dispositions: dict[tuple[UUID, UUID, str, str], ObservabilityAlertDisposition] = {}
        self._disposition_events: list[ObservabilityAlertDispositionEvent] = []
        self._replay_reviews: list[ObservabilityAlertReplayReview] = []
        self._recommendation_feedback: dict[
            tuple[UUID, UUID, UUID], ObservabilityAlertRecommendationFeedback
        ] = {}
        self._lock = asyncio.Lock()

    async def record_api_request(self, observation: ApiRequestObservation) -> None:
        async with self._lock:
            self._requests.append(observation)

    async def get_metrics(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        agent_id: UUID | None = None,
    ) -> ObservabilityMetrics:
        async with self._lock:
            requests = tuple(
                item
                for item in self._requests
                if item.tenant_id == tenant_id
                and (agent_id is None or item.agent_id == agent_id)
                and window_started_at <= item.occurred_at <= window_ended_at
            )
        server_errors = sum(item.status_code >= 500 for item in requests)
        return ObservabilityMetrics(
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
            api=ApiSloMetrics(
                requests=len(requests),
                server_errors=server_errors,
                error_rate_percent=_rate(server_errors, len(requests)),
                latency=_memory_percentiles(tuple(item.duration_ms for item in requests)),
            ),
            agent_runs=AgentRunSloMetrics(
                terminal_runs=0,
                completed_runs=0,
                unsuccessful_runs=0,
                success_rate_percent=100.0,
                latency=LatencyPercentiles(0, 0, 0),
            ),
            models=(),
            queue=QueueMetrics(backlog=0, oldest_wait_seconds=0),
        )

    async def reconcile_observability_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alerts: tuple[ActiveAlert, ...],
        observed_at: datetime,
    ) -> ObservabilityAlertLifecycleReconciliation:
        async with self._lock:
            activated: list[ObservabilityAlertLifecycle] = []
            recovered: list[ObservabilityAlertLifecycle] = []
            keys = {(item.source_type, item.alert_key) for item in alerts}
            current = {
                (item.source_type, item.source_key): item
                for item in self._lifecycles.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and item.status is ObservabilityAlertLifecycleStatus.ACTIVE
            }
            for alert in alerts:
                key = (alert.source_type, alert.alert_key)
                row = current.get(key)
                if row is None:
                    row = ObservabilityAlertLifecycle(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        source_type=alert.source_type,
                        source_key=alert.alert_key,
                        code=alert.code,
                        status=ObservabilityAlertLifecycleStatus.ACTIVE,
                        severity=alert.severity,
                        occurrences=1,
                        current_value=alert.current_value,
                        threshold_value=alert.threshold_value,
                        unit=alert.unit,
                        first_occurred_at=alert.first_occurred_at or observed_at,
                        last_occurred_at=alert.last_occurred_at or observed_at,
                        last_evaluated_at=observed_at,
                        escalated_at=None,
                        resolved_at=None,
                        recovery_duration_seconds=None,
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                    activated.append(row)
                else:
                    row = replace(
                        row,
                        severity=alert.severity,
                        occurrences=row.occurrences + 1,
                        current_value=alert.current_value,
                        threshold_value=alert.threshold_value,
                        last_occurred_at=alert.last_occurred_at or observed_at,
                        last_evaluated_at=observed_at,
                        updated_at=observed_at,
                    )
                self._lifecycles[row.id] = row
            for lifecycle_id, row in tuple(self._lifecycles.items()):
                key = (row.source_type, row.source_key)
                if (
                    row.tenant_id != tenant_id
                    or row.agent_id != agent_id
                    or key in keys
                    or row.status is not ObservabilityAlertLifecycleStatus.ACTIVE
                ):
                    continue
                resolved = replace(
                    row,
                    status=ObservabilityAlertLifecycleStatus.RESOLVED,
                    last_evaluated_at=observed_at,
                    resolved_at=observed_at,
                    recovery_duration_seconds=max(
                        0, int((observed_at - row.first_occurred_at).total_seconds())
                    ),
                    updated_at=observed_at,
                )
                self._lifecycles[lifecycle_id] = resolved
                recovered.append(resolved)
            active = tuple(
                sorted(
                    (
                        item
                        for item in self._lifecycles.values()
                        if item.tenant_id == tenant_id
                        and item.agent_id == agent_id
                        and item.status is ObservabilityAlertLifecycleStatus.ACTIVE
                    ),
                    key=lambda item: item.first_occurred_at,
                    reverse=True,
                )
            )
            return ObservabilityAlertLifecycleReconciliation(
                active_lifecycles=active,
                activated_lifecycles=tuple(activated),
                recovered_lifecycles=tuple(recovered),
            )

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
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        checked_at = evaluated_at or datetime.now(UTC)
        async with self._lock:
            values = [
                item
                for item in self._lifecycles.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (status is None or item.status is status)
                and (source_type is None or item.source_type == source_type)
                and (severity is None or item.severity is severity)
                and (
                    minimum_duration_minutes is None
                    or int(
                        ((item.resolved_at or checked_at) - item.first_occurred_at).total_seconds()
                    )
                    >= minimum_duration_minutes * 60
                )
                and (
                    cursor is None
                    or item.updated_at < cursor.occurred_at
                    or (
                        item.updated_at == cursor.occurred_at
                        and str(item.id) < str(cursor.entity_id)
                    )
                )
            ]
            return tuple(
                sorted(values, key=lambda item: (item.updated_at, str(item.id)), reverse=True)[
                    :limit
                ]
            )

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
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        async with self._lock:
            values = [
                item
                for item in self._lifecycles.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (source_type is None or item.source_type == source_type)
                and (severity is None or item.severity is severity)
                and (
                    item.status is ObservabilityAlertLifecycleStatus.ACTIVE
                    or window_started_at <= item.first_occurred_at <= window_ended_at
                    or (
                        item.resolved_at is not None
                        and window_started_at <= item.resolved_at <= window_ended_at
                    )
                    or (
                        item.escalated_at is not None
                        and window_started_at <= item.escalated_at <= window_ended_at
                    )
                )
            ]
            values.sort(key=lambda item: (item.first_occurred_at, str(item.id)), reverse=True)
            return tuple(values if limit is None else values[:limit])

    async def get_observability_alert_lifecycle(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
    ) -> ObservabilityAlertLifecycle | None:
        async with self._lock:
            item = self._lifecycles.get(lifecycle_id)
            if item is None or item.tenant_id != tenant_id or item.agent_id != agent_id:
                return None
            return item

    async def get_observability_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        source_type: str,
        source_key: str,
    ) -> ObservabilityAlertDisposition | None:
        async with self._lock:
            return self._dispositions.get((tenant_id, agent_id, source_type, source_key))

    async def list_observability_alert_dispositions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        limit: int | None = None,
    ) -> tuple[ObservabilityAlertDisposition, ...]:
        async with self._lock:
            values = sorted(
                (
                    item
                    for item in self._dispositions.values()
                    if item.tenant_id == tenant_id and item.agent_id == agent_id
                ),
                key=lambda item: item.updated_at,
                reverse=True,
            )
            return tuple(values if limit is None else values[:limit])

    async def save_observability_alert_disposition(
        self, disposition: ObservabilityAlertDisposition, *, lifecycle_id: UUID | None = None
    ) -> ObservabilityAlertDisposition:
        async with self._lock:
            lifecycle = self._find_active_lifecycle(
                tenant_id=disposition.tenant_id,
                agent_id=disposition.agent_id,
                source_type=disposition.source_type,
                source_key=disposition.source_key,
                lifecycle_id=lifecycle_id,
            )
            if lifecycle is None:
                raise LookupError("活动通用告警不存在")
            key = (
                disposition.tenant_id,
                disposition.agent_id,
                disposition.source_type,
                disposition.source_key,
            )
            self._dispositions[key] = disposition
            self._append_disposition_event(
                lifecycle=lifecycle,
                action=ObservabilityAlertDispositionAction(disposition.status.value),
                reason=disposition.reason,
                actor_id=disposition.actor_id,
                expires_at=disposition.expires_at,
                occurred_at=disposition.updated_at,
            )
            return disposition

    async def batch_save_observability_alert_dispositions(
        self,
        items: tuple[tuple[UUID, ObservabilityAlertDisposition], ...],
    ) -> tuple[ObservabilityAlertDisposition, ...]:
        async with self._lock:
            lifecycles = tuple(
                self._find_active_lifecycle(
                    tenant_id=item.tenant_id,
                    agent_id=item.agent_id,
                    source_type=item.source_type,
                    source_key=item.source_key,
                    lifecycle_id=lifecycle_id,
                )
                for lifecycle_id, item in items
            )
            if any(item is None for item in lifecycles):
                raise LookupError("活动通用告警不存在")
            for (_lifecycle_id, item), lifecycle in zip(items, lifecycles, strict=True):
                assert lifecycle is not None
                self._dispositions[
                    (item.tenant_id, item.agent_id, item.source_type, item.source_key)
                ] = item
                self._append_disposition_event(
                    lifecycle=lifecycle,
                    action=ObservabilityAlertDispositionAction(item.status.value),
                    reason=item.reason,
                    actor_id=item.actor_id,
                    expires_at=item.expires_at,
                    occurred_at=item.updated_at,
                )
            return tuple(item for _, item in items)

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
    ) -> ObservabilityAlertDisposition | None:
        async with self._lock:
            removed = self._dispositions.pop((tenant_id, agent_id, source_type, source_key), None)
            if removed is None:
                return None
            lifecycle = self._lifecycles.get(lifecycle_id) if lifecycle_id is not None else None
            if lifecycle is None:
                lifecycle = self._find_latest_lifecycle(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    source_type=source_type,
                    source_key=source_key,
                )
            if lifecycle is not None:
                self._append_disposition_event(
                    lifecycle=lifecycle,
                    action=ObservabilityAlertDispositionAction.CLEARED,
                    reason=reason,
                    actor_id=actor_id,
                    expires_at=removed.expires_at,
                    occurred_at=datetime.now(UTC),
                )
            return removed

    async def batch_clear_observability_alert_dispositions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        actor_id: UUID,
        reason: str,
    ) -> tuple[ObservabilityAlertDisposition, ...]:
        async with self._lock:
            lifecycles = tuple(self._lifecycles.get(lifecycle_id) for lifecycle_id in lifecycle_ids)
            if any(
                lifecycle is None
                or lifecycle.tenant_id != tenant_id
                or lifecycle.agent_id != agent_id
                for lifecycle in lifecycles
            ):
                raise LookupError("通用告警生命周期不存在")
            keys = tuple(
                (tenant_id, agent_id, lifecycle.source_type, lifecycle.source_key)
                for lifecycle in lifecycles
                if lifecycle is not None
            )
            removed = tuple(self._dispositions.get(key) for key in keys)
            if any(item is None for item in removed):
                raise LookupError("通用告警处置不存在")
            result = tuple(item for item in removed if item is not None)
            occurred_at = datetime.now(UTC)
            for lifecycle, item in zip(lifecycles, result, strict=True):
                assert lifecycle is not None
                self._dispositions.pop(
                    (tenant_id, agent_id, lifecycle.source_type, lifecycle.source_key), None
                )
                self._append_disposition_event(
                    lifecycle=lifecycle,
                    action=ObservabilityAlertDispositionAction.CLEARED,
                    reason=reason,
                    actor_id=actor_id,
                    expires_at=item.expires_at,
                    occurred_at=occurred_at,
                )
            return result

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
    ) -> tuple[ObservabilityAlertDispositionEvent, ...]:
        async with self._lock:
            values = [
                event
                for event in self._disposition_events
                if event.tenant_id == tenant_id
                and event.agent_id == agent_id
                and (lifecycle_id is None or event.lifecycle_id == lifecycle_id)
                and (source_type is None or event.source_type == source_type)
                and (source_key is None or event.source_key == source_key)
                and (action is None or event.action is action)
                and (occurred_after is None or event.occurred_at >= occurred_after)
                and (occurred_before is None or event.occurred_at <= occurred_before)
            ]
            values.sort(key=lambda item: (item.occurred_at, str(item.id)), reverse=True)
            return tuple(values[:limit])

    async def record_observability_alert_replay_review(
        self, review: ObservabilityAlertReplayReview
    ) -> ObservabilityAlertReplayReview:
        async with self._lock:
            self._replay_reviews.append(review)
            return review

    async def get_observability_alert_recommendation_feedback(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
    ) -> ObservabilityAlertRecommendationFeedback | None:
        async with self._lock:
            return self._recommendation_feedback.get((tenant_id, agent_id, lifecycle_id))

    async def save_observability_alert_recommendation_feedback(
        self, feedback: ObservabilityAlertRecommendationFeedback
    ) -> ObservabilityAlertRecommendationFeedback:
        key = (feedback.tenant_id, feedback.agent_id, feedback.lifecycle_id)
        async with self._lock:
            existing = self._recommendation_feedback.get(key)
            if existing is not None:
                if existing.decision is feedback.decision:
                    return existing
                raise ValueError("该告警生命周期已提交不同的建议反馈")
            self._recommendation_feedback[key] = feedback
            return feedback

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
    ) -> tuple[ObservabilityAlertRecommendationFeedback, ...]:
        lifecycle_filter = None if lifecycle_ids is None else frozenset(lifecycle_ids)
        async with self._lock:
            values = sorted(
                (
                    item
                    for item in self._recommendation_feedback.values()
                    if item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and (lifecycle_filter is None or item.lifecycle_id in lifecycle_filter)
                    and (window_started_at is None or item.feedback_at >= window_started_at)
                    and (window_ended_at is None or item.feedback_at <= window_ended_at)
                    and (source_type is None or item.source_type == source_type)
                ),
                key=lambda item: (item.feedback_at, str(item.id)),
                reverse=True,
            )
            return tuple(values if limit is None else values[:limit])

    async def count_observability_alert_lifecycle_statuses(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
    ) -> dict[ObservabilityAlertLifecycleStatus, int]:
        requested = frozenset(lifecycle_ids)
        async with self._lock:
            counts = {status: 0 for status in ObservabilityAlertLifecycleStatus}
            for item in self._lifecycles.values():
                if (
                    item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.id in requested
                ):
                    counts[item.status] += 1
            return counts

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
    ) -> tuple[ObservabilityAlertReplayReview, ...]:
        async with self._lock:
            values = [
                item
                for item in self._replay_reviews
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (decision is None or item.decision is decision)
                and (reason_code is None or item.reason_code is reason_code)
                and (source_type is None or item.source_type == source_type)
                and (
                    cursor is None
                    or item.reviewed_at < cursor.occurred_at
                    or (
                        item.reviewed_at == cursor.occurred_at
                        and str(item.id) < str(cursor.entity_id)
                    )
                )
            ]
            values.sort(key=lambda item: (item.reviewed_at, str(item.id)), reverse=True)
            return tuple(values[:limit])

    async def get_observability_alert_replay_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        source_type: str | None = None,
    ) -> ObservabilityAlertReplayMetrics:
        async with self._lock:
            values = tuple(
                item
                for item in self._replay_reviews
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and window_started_at <= item.reviewed_at <= window_ended_at
                and (source_type is None or item.source_type == source_type)
            )
        reason_counts = {
            reason: sum(item.reason_code is reason for item in values)
            for reason in ObservabilityAlertReplayReason
        }
        sources = sorted({item.source_type for item in values}, key=lambda item: item or "")
        return ObservabilityAlertReplayMetrics(
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
            total=len(values),
            allowed=sum(
                item.decision is ObservabilityAlertReplayDecision.ALLOWED for item in values
            ),
            blocked=sum(
                item.decision is ObservabilityAlertReplayDecision.BLOCKED for item in values
            ),
            reasons=tuple(
                ObservabilityAlertReplayReasonMetrics(reason_code=reason, count=count)
                for reason, count in reason_counts.items()
                if count
            ),
            sources=tuple(
                ObservabilityAlertReplaySourceMetrics(
                    source_type=source,
                    total=len(source_values),
                    allowed=sum(
                        item.decision is ObservabilityAlertReplayDecision.ALLOWED
                        for item in source_values
                    ),
                    blocked=sum(
                        item.decision is ObservabilityAlertReplayDecision.BLOCKED
                        for item in source_values
                    ),
                )
                for source in sources
                if (source_values := tuple(item for item in values if item.source_type == source))
            ),
        )

    async def collect_observability_alert_history(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        max_records: int,
    ) -> ObservabilityAlertHistorySnapshot:
        async with self._lock:
            lifecycles = tuple(
                sorted(
                    (
                        item
                        for item in self._lifecycles.values()
                        if item.tenant_id == tenant_id
                        and item.agent_id == agent_id
                        and window_started_at <= item.updated_at <= window_ended_at
                    ),
                    key=lambda item: (item.updated_at, str(item.id)),
                )
            )
            disposition_events = tuple(
                sorted(
                    (
                        item
                        for item in self._disposition_events
                        if item.tenant_id == tenant_id
                        and item.agent_id == agent_id
                        and window_started_at <= item.occurred_at <= window_ended_at
                    ),
                    key=lambda item: (item.occurred_at, str(item.id)),
                )
            )
            replay_reviews = tuple(
                sorted(
                    (
                        item
                        for item in self._replay_reviews
                        if item.tenant_id == tenant_id
                        and item.agent_id == agent_id
                        and window_started_at <= item.reviewed_at <= window_ended_at
                    ),
                    key=lambda item: (item.reviewed_at, str(item.id)),
                )
            )
            recommendation_feedback = tuple(
                sorted(
                    (
                        item
                        for item in self._recommendation_feedback.values()
                        if item.tenant_id == tenant_id
                        and item.agent_id == agent_id
                        and window_started_at <= item.feedback_at <= window_ended_at
                    ),
                    key=lambda item: (item.feedback_at, str(item.id)),
                )
            )
            truncated = (
                len(lifecycles)
                + len(disposition_events)
                + len(replay_reviews)
                + len(recommendation_feedback)
                > max_records
            )
            return ObservabilityAlertHistorySnapshot(
                lifecycles=() if truncated else lifecycles,
                disposition_events=() if truncated else disposition_events,
                replay_reviews=() if truncated else replay_reviews,
                recommendation_feedback=() if truncated else recommendation_feedback,
                truncated=truncated,
            )

    async def purge_observability_alert_history(
        self,
        *,
        tenant_id: UUID,
        disposition_events_before: datetime,
        replay_reviews_before: datetime,
        recommendation_feedback_before: datetime,
        limit: int,
    ) -> ObservabilityHistoryRetentionResult:
        async with self._lock:
            event_ids = {
                item.id
                for item in sorted(
                    (
                        item
                        for item in self._disposition_events
                        if item.tenant_id == tenant_id
                        and item.occurred_at <= disposition_events_before
                    ),
                    key=lambda item: (item.occurred_at, str(item.id)),
                )[:limit]
            }
            review_ids = {
                item.id
                for item in sorted(
                    (
                        item
                        for item in self._replay_reviews
                        if item.tenant_id == tenant_id and item.reviewed_at <= replay_reviews_before
                    ),
                    key=lambda item: (item.reviewed_at, str(item.id)),
                )[:limit]
            }
            feedback_ids = {
                item.id
                for item in sorted(
                    (
                        item
                        for item in self._recommendation_feedback.values()
                        if item.tenant_id == tenant_id
                        and item.feedback_at <= recommendation_feedback_before
                    ),
                    key=lambda item: (item.feedback_at, str(item.id)),
                )[:limit]
            }
            self._disposition_events = [
                item for item in self._disposition_events if item.id not in event_ids
            ]
            self._replay_reviews = [
                item for item in self._replay_reviews if item.id not in review_ids
            ]
            self._recommendation_feedback = {
                key: item
                for key, item in self._recommendation_feedback.items()
                if item.id not in feedback_ids
            }
            return ObservabilityHistoryRetentionResult(
                disposition_events_purged=len(event_ids),
                replay_reviews_purged=len(review_ids),
                recommendation_feedback_purged=len(feedback_ids),
            )

    def _find_active_lifecycle(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        source_type: str,
        source_key: str,
        lifecycle_id: UUID | None,
    ) -> ObservabilityAlertLifecycle | None:
        lifecycle = self._lifecycles.get(lifecycle_id) if lifecycle_id is not None else None
        if lifecycle is not None and (
            lifecycle.tenant_id != tenant_id
            or lifecycle.agent_id != agent_id
            or lifecycle.source_type != source_type
            or lifecycle.source_key != source_key
            or lifecycle.status is not ObservabilityAlertLifecycleStatus.ACTIVE
        ):
            return None
        if lifecycle is not None:
            return lifecycle
        return next(
            (
                item
                for item in self._lifecycles.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and item.source_type == source_type
                and item.source_key == source_key
                and item.status is ObservabilityAlertLifecycleStatus.ACTIVE
            ),
            None,
        )

    def _find_latest_lifecycle(
        self, *, tenant_id: UUID, agent_id: UUID, source_type: str, source_key: str
    ) -> ObservabilityAlertLifecycle | None:
        values = [
            item
            for item in self._lifecycles.values()
            if item.tenant_id == tenant_id
            and item.agent_id == agent_id
            and item.source_type == source_type
            and item.source_key == source_key
        ]
        return max(values, key=lambda item: item.updated_at, default=None)

    def _append_disposition_event(
        self,
        *,
        lifecycle: ObservabilityAlertLifecycle,
        action: ObservabilityAlertDispositionAction,
        reason: str,
        actor_id: UUID,
        expires_at: datetime | None,
        occurred_at: datetime,
    ) -> None:
        self._disposition_events.append(
            ObservabilityAlertDispositionEvent(
                id=uuid4(),
                tenant_id=lifecycle.tenant_id,
                agent_id=lifecycle.agent_id,
                lifecycle_id=lifecycle.id,
                source_type=lifecycle.source_type,
                source_key=lifecycle.source_key,
                code=lifecycle.code,
                action=action,
                reason=reason,
                actor_id=actor_id,
                expires_at=expires_at,
                occurred_at=occurred_at,
            )
        )

    async def mark_observability_alert_lifecycles_escalated(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        escalation_level: int,
        escalated_at: datetime,
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        selected: list[ObservabilityAlertLifecycle] = []
        identifiers = set(lifecycle_ids)
        async with self._lock:
            for key, row in tuple(self._lifecycles.items()):
                if (
                    row.id not in identifiers
                    or row.tenant_id != tenant_id
                    or row.agent_id != agent_id
                    or row.status is not ObservabilityAlertLifecycleStatus.ACTIVE
                    or row.escalation_level >= escalation_level
                ):
                    continue
                updated = replace(
                    row,
                    escalated_at=row.escalated_at or escalated_at,
                    escalation_level=escalation_level,
                    last_escalated_at=escalated_at,
                    updated_at=escalated_at,
                )
                self._lifecycles[key] = updated
                selected.append(updated)
        return tuple(selected)

    async def list_observability_agent_ids(self) -> tuple[tuple[UUID, UUID], ...]:
        async with self._lock:
            return tuple(
                sorted(
                    {
                        (item.tenant_id, item.agent_id)
                        for item in self._requests
                        if item.tenant_id is not None and item.agent_id is not None
                    },
                    key=lambda value: (str(value[0]), str(value[1])),
                )
            )


class SqlAlchemyObservabilityRepository:
    """使用数据库侧分位数与分组聚合控制读取规模和作用域。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record_api_request(self, observation: ApiRequestObservation) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                ApiRequestMetricModel(
                    tenant_id=observation.tenant_id,
                    agent_id=observation.agent_id,
                    method=observation.method,
                    route=observation.route,
                    status_code=observation.status_code,
                    duration_ms=observation.duration_ms,
                    occurred_at=observation.occurred_at,
                )
            )

    async def get_metrics(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        agent_id: UUID | None = None,
    ) -> ObservabilityMetrics:
        async with self._session_factory() as session:
            api = await self._api_metrics(
                session, tenant_id, window_started_at, window_ended_at, agent_id
            )
            agent_runs = await self._agent_run_metrics(
                session, tenant_id, window_started_at, window_ended_at, agent_id
            )
            models = await self._model_metrics(
                session, tenant_id, window_started_at, window_ended_at, agent_id
            )
            queue = await self._queue_metrics(session, tenant_id, window_ended_at, agent_id)
            channel_delivery = await self._channel_delivery_metrics(
                session, tenant_id, window_started_at, window_ended_at, agent_id
            )
            notification_delivery = await self._notification_delivery_metrics(
                session, tenant_id, window_started_at, window_ended_at, agent_id
            )
        return ObservabilityMetrics(
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
            api=api,
            agent_runs=agent_runs,
            models=models,
            queue=queue,
            channel_delivery=channel_delivery,
            notification_delivery=notification_delivery,
        )

    async def reconcile_observability_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alerts: tuple[ActiveAlert, ...],
        observed_at: datetime,
    ) -> ObservabilityAlertLifecycleReconciliation:
        """在 Agent 行锁下对账通用告警，保证重复维护循环不会创建重复活动事件。"""
        async with self._session_factory.begin() as session:
            # 多个 Worker 可能同时维护同一 Agent；事务级 advisory lock 让
            # 首次创建与恢复对账在唯一部分索引之外也保持确定性。
            lock_key = f"observability-alerts:{tenant_id}:{agent_id}"
            await session.execute(
                select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
            )
            rows = (
                await session.scalars(
                    select(ObservabilityAlertLifecycleModel)
                    .where(
                        ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                        ObservabilityAlertLifecycleModel.agent_id == agent_id,
                        ObservabilityAlertLifecycleModel.status
                        == ObservabilityAlertLifecycleStatus.ACTIVE.value,
                    )
                    .with_for_update()
                )
            ).all()
            activated_rows: list[ObservabilityAlertLifecycleModel] = []
            recovered_rows: list[ObservabilityAlertLifecycleModel] = []
            current = {(row.source_type, row.source_key): row for row in rows}
            active_keys = {(item.source_type, item.alert_key) for item in alerts}
            for alert in alerts:
                key = (alert.source_type, alert.alert_key)
                row = current.get(key)
                first = alert.first_occurred_at or observed_at
                last = alert.last_occurred_at or observed_at
                if row is None:
                    row = ObservabilityAlertLifecycleModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        source_type=alert.source_type,
                        source_key=alert.alert_key,
                        code=alert.code,
                        status=ObservabilityAlertLifecycleStatus.ACTIVE.value,
                        severity=alert.severity.value,
                        occurrences=1,
                        current_value=alert.current_value,
                        threshold_value=alert.threshold_value,
                        unit=alert.unit,
                        first_occurred_at=first,
                        last_occurred_at=last,
                        last_evaluated_at=observed_at,
                        escalated_at=None,
                        escalation_level=0,
                        last_escalated_at=None,
                        resolved_at=None,
                        recovery_duration_seconds=None,
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                    session.add(row)
                    activated_rows.append(row)
                else:
                    row.severity = alert.severity.value
                    row.occurrences += 1
                    row.current_value = alert.current_value
                    row.threshold_value = alert.threshold_value
                    row.unit = alert.unit
                    row.last_occurred_at = last
                    row.last_evaluated_at = observed_at
                    row.updated_at = observed_at
            for row in rows:
                if (row.source_type, row.source_key) in active_keys:
                    continue
                row.status = ObservabilityAlertLifecycleStatus.RESOLVED.value
                row.last_evaluated_at = observed_at
                row.resolved_at = observed_at
                row.recovery_duration_seconds = max(
                    0, int((observed_at - row.first_occurred_at).total_seconds())
                )
                row.updated_at = observed_at
                recovered_rows.append(row)
            await session.flush()
            active_rows = (
                await session.scalars(
                    select(ObservabilityAlertLifecycleModel)
                    .where(
                        ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                        ObservabilityAlertLifecycleModel.agent_id == agent_id,
                        ObservabilityAlertLifecycleModel.status
                        == ObservabilityAlertLifecycleStatus.ACTIVE.value,
                    )
                    .order_by(ObservabilityAlertLifecycleModel.first_occurred_at.desc())
                )
            ).all()
            return ObservabilityAlertLifecycleReconciliation(
                active_lifecycles=tuple(self._observability_lifecycle(row) for row in active_rows),
                activated_lifecycles=tuple(
                    self._observability_lifecycle(row) for row in activated_rows
                ),
                recovered_lifecycles=tuple(
                    self._observability_lifecycle(row) for row in recovered_rows
                ),
            )

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
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        statement = (
            select(ObservabilityAlertLifecycleModel)
            .where(
                ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                ObservabilityAlertLifecycleModel.agent_id == agent_id,
            )
            .order_by(
                ObservabilityAlertLifecycleModel.updated_at.desc(),
                ObservabilityAlertLifecycleModel.id.desc(),
            )
            .limit(limit)
        )
        if status is not None:
            statement = statement.where(ObservabilityAlertLifecycleModel.status == status.value)
        if source_type is not None:
            statement = statement.where(ObservabilityAlertLifecycleModel.source_type == source_type)
        if severity is not None:
            statement = statement.where(ObservabilityAlertLifecycleModel.severity == severity.value)
        if minimum_duration_minutes is not None:
            statement = statement.where(
                func.coalesce(
                    ObservabilityAlertLifecycleModel.resolved_at,
                    evaluated_at or datetime.now(UTC),
                )
                - ObservabilityAlertLifecycleModel.first_occurred_at
                >= timedelta(minutes=minimum_duration_minutes)
            )
        if cursor is not None:
            statement = statement.where(
                or_(
                    ObservabilityAlertLifecycleModel.updated_at < cursor.occurred_at,
                    and_(
                        ObservabilityAlertLifecycleModel.updated_at == cursor.occurred_at,
                        ObservabilityAlertLifecycleModel.id < cursor.entity_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._observability_lifecycle(row) for row in rows)

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
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        statement = (
            select(ObservabilityAlertLifecycleModel)
            .where(
                ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                ObservabilityAlertLifecycleModel.agent_id == agent_id,
                or_(
                    ObservabilityAlertLifecycleModel.status
                    == ObservabilityAlertLifecycleStatus.ACTIVE.value,
                    ObservabilityAlertLifecycleModel.first_occurred_at.between(
                        window_started_at, window_ended_at
                    ),
                    ObservabilityAlertLifecycleModel.resolved_at.between(
                        window_started_at, window_ended_at
                    ),
                    ObservabilityAlertLifecycleModel.escalated_at.between(
                        window_started_at, window_ended_at
                    ),
                ),
            )
            .order_by(
                ObservabilityAlertLifecycleModel.first_occurred_at.desc(),
                ObservabilityAlertLifecycleModel.id.desc(),
            )
        )
        if source_type is not None:
            statement = statement.where(ObservabilityAlertLifecycleModel.source_type == source_type)
        if severity is not None:
            statement = statement.where(ObservabilityAlertLifecycleModel.severity == severity.value)
        if limit is not None:
            statement = statement.limit(limit)
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._observability_lifecycle(row) for row in rows)

    async def get_observability_alert_lifecycle(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
    ) -> ObservabilityAlertLifecycle | None:
        statement = select(ObservabilityAlertLifecycleModel).where(
            ObservabilityAlertLifecycleModel.id == lifecycle_id,
            ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
            ObservabilityAlertLifecycleModel.agent_id == agent_id,
        )
        async with self._session_factory() as session:
            row = await session.scalar(statement)
        return None if row is None else self._observability_lifecycle(row)

    async def get_observability_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        source_type: str,
        source_key: str,
    ) -> ObservabilityAlertDisposition | None:
        statement = select(ObservabilityAlertDispositionModel).where(
            ObservabilityAlertDispositionModel.tenant_id == tenant_id,
            ObservabilityAlertDispositionModel.agent_id == agent_id,
            ObservabilityAlertDispositionModel.source_type == source_type,
            ObservabilityAlertDispositionModel.source_key == source_key,
        )
        async with self._session_factory() as session:
            row = await session.scalar(statement)
        return None if row is None else self._observability_disposition(row)

    async def list_observability_alert_dispositions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        limit: int | None = None,
    ) -> tuple[ObservabilityAlertDisposition, ...]:
        statement = (
            select(ObservabilityAlertDispositionModel)
            .where(
                ObservabilityAlertDispositionModel.tenant_id == tenant_id,
                ObservabilityAlertDispositionModel.agent_id == agent_id,
            )
            .order_by(ObservabilityAlertDispositionModel.updated_at.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._observability_disposition(row) for row in rows)

    async def save_observability_alert_disposition(
        self, disposition: ObservabilityAlertDisposition, *, lifecycle_id: UUID | None = None
    ) -> ObservabilityAlertDisposition:
        async with self._session_factory.begin() as session:
            lock_key = (
                f"observability-disposition:{disposition.tenant_id}:"
                f"{disposition.agent_id}:{disposition.source_type}:{disposition.source_key}"
            )
            await session.execute(
                select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
            )
            lifecycle = await session.scalar(
                select(ObservabilityAlertLifecycleModel)
                .where(
                    ObservabilityAlertLifecycleModel.tenant_id == disposition.tenant_id,
                    ObservabilityAlertLifecycleModel.agent_id == disposition.agent_id,
                    ObservabilityAlertLifecycleModel.source_type == disposition.source_type,
                    ObservabilityAlertLifecycleModel.source_key == disposition.source_key,
                    ObservabilityAlertLifecycleModel.status
                    == ObservabilityAlertLifecycleStatus.ACTIVE.value,
                    *(
                        ()
                        if lifecycle_id is None
                        else (ObservabilityAlertLifecycleModel.id == lifecycle_id,)
                    ),
                )
                .with_for_update()
            )
            if lifecycle is None:
                raise LookupError("活动通用告警不存在")
            row = await session.scalar(
                select(ObservabilityAlertDispositionModel)
                .where(
                    ObservabilityAlertDispositionModel.tenant_id == disposition.tenant_id,
                    ObservabilityAlertDispositionModel.agent_id == disposition.agent_id,
                    ObservabilityAlertDispositionModel.source_type == disposition.source_type,
                    ObservabilityAlertDispositionModel.source_key == disposition.source_key,
                )
                .with_for_update()
            )
            if row is None:
                row = ObservabilityAlertDispositionModel(
                    id=disposition.id,
                    tenant_id=disposition.tenant_id,
                    agent_id=disposition.agent_id,
                    source_type=disposition.source_type,
                    source_key=disposition.source_key,
                    code=disposition.code,
                    status=disposition.status.value,
                    reason=disposition.reason,
                    actor_id=disposition.actor_id,
                    expires_at=disposition.expires_at,
                    created_at=disposition.created_at,
                    updated_at=disposition.updated_at,
                )
                session.add(row)
            else:
                row.code = disposition.code
                row.status = disposition.status.value
                row.reason = disposition.reason
                row.actor_id = disposition.actor_id
                row.expires_at = disposition.expires_at
                row.updated_at = disposition.updated_at
            session.add(
                AuditLog(
                    tenant_id=disposition.tenant_id,
                    actor_id=disposition.actor_id,
                    action=f"observability_alert.{disposition.status.value}",
                    resource_type="observability_alert_disposition",
                    resource_id=disposition.alert_key,
                    detail={
                        "agent_id": str(disposition.agent_id),
                        "code": disposition.code,
                        "status": disposition.status.value,
                        "expires_at": (
                            disposition.expires_at.isoformat()
                            if disposition.expires_at is not None
                            else None
                        ),
                    },
                )
            )
            session.add(
                ObservabilityAlertDispositionEventModel(
                    id=uuid4(),
                    tenant_id=disposition.tenant_id,
                    agent_id=disposition.agent_id,
                    lifecycle_id=lifecycle.id,
                    source_type=disposition.source_type,
                    source_key=disposition.source_key,
                    code=disposition.code,
                    action=disposition.status.value,
                    reason=disposition.reason,
                    actor_id=disposition.actor_id,
                    expires_at=disposition.expires_at,
                    occurred_at=disposition.updated_at,
                )
            )
            await session.flush()
            return self._observability_disposition(row)

    async def batch_save_observability_alert_dispositions(
        self,
        items: tuple[tuple[UUID, ObservabilityAlertDisposition], ...],
    ) -> tuple[ObservabilityAlertDisposition, ...]:
        """锁定全部活动生命周期后原子写入处置、历史和审计。"""
        async with self._session_factory.begin() as session:
            if not items:
                return ()
            tenant_id = items[0][1].tenant_id
            agent_id = items[0][1].agent_id
            if any(item.tenant_id != tenant_id or item.agent_id != agent_id for _, item in items):
                raise LookupError("批量通用告警必须属于同一租户与 Agent")
            lifecycle_ids = tuple(lifecycle_id for lifecycle_id, _ in items)
            rows = (
                await session.scalars(
                    select(ObservabilityAlertLifecycleModel)
                    .where(
                        ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                        ObservabilityAlertLifecycleModel.agent_id == agent_id,
                        ObservabilityAlertLifecycleModel.id.in_(lifecycle_ids),
                        ObservabilityAlertLifecycleModel.status
                        == ObservabilityAlertLifecycleStatus.ACTIVE.value,
                    )
                    .order_by(ObservabilityAlertLifecycleModel.id)
                    .with_for_update()
                )
            ).all()
            by_id = {row.id: row for row in rows}
            if len(by_id) != len(items):
                raise LookupError("活动通用告警不存在")
            stored_by_lifecycle: dict[UUID, ObservabilityAlertDisposition] = {}
            for lifecycle_id, disposition in sorted(items, key=lambda item: str(item[0])):
                lifecycle = by_id.get(lifecycle_id)
                if lifecycle is None or (
                    lifecycle.source_type != disposition.source_type
                    or lifecycle.source_key != disposition.source_key
                ):
                    raise LookupError("活动通用告警不存在")
                row = await session.scalar(
                    select(ObservabilityAlertDispositionModel)
                    .where(
                        ObservabilityAlertDispositionModel.tenant_id == tenant_id,
                        ObservabilityAlertDispositionModel.agent_id == agent_id,
                        ObservabilityAlertDispositionModel.source_type == disposition.source_type,
                        ObservabilityAlertDispositionModel.source_key == disposition.source_key,
                    )
                    .with_for_update()
                )
                if row is None:
                    row = ObservabilityAlertDispositionModel(
                        id=disposition.id,
                        tenant_id=disposition.tenant_id,
                        agent_id=disposition.agent_id,
                        source_type=disposition.source_type,
                        source_key=disposition.source_key,
                        code=disposition.code,
                        status=disposition.status.value,
                        reason=disposition.reason,
                        actor_id=disposition.actor_id,
                        expires_at=disposition.expires_at,
                        created_at=disposition.created_at,
                        updated_at=disposition.updated_at,
                    )
                    session.add(row)
                else:
                    row.code = disposition.code
                    row.status = disposition.status.value
                    row.reason = disposition.reason
                    row.actor_id = disposition.actor_id
                    row.expires_at = disposition.expires_at
                    row.updated_at = disposition.updated_at
                session.add(
                    ObservabilityAlertDispositionEventModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        lifecycle_id=lifecycle.id,
                        source_type=disposition.source_type,
                        source_key=disposition.source_key,
                        code=disposition.code,
                        action=disposition.status.value,
                        reason=disposition.reason,
                        actor_id=disposition.actor_id,
                        expires_at=disposition.expires_at,
                        occurred_at=disposition.updated_at,
                    )
                )
                session.add(
                    AuditLog(
                        tenant_id=tenant_id,
                        actor_id=disposition.actor_id,
                        action=f"observability_alert.{disposition.status.value}",
                        resource_type="observability_alert_disposition",
                        resource_id=disposition.alert_key,
                        detail={
                            "agent_id": str(agent_id),
                            "code": disposition.code,
                            "status": disposition.status.value,
                            "expires_at": (
                                disposition.expires_at.isoformat()
                                if disposition.expires_at is not None
                                else None
                            ),
                            "batch": True,
                        },
                    )
                )
                stored_by_lifecycle[lifecycle_id] = self._observability_disposition(row)
            return tuple(stored_by_lifecycle[lifecycle_id] for lifecycle_id in lifecycle_ids)

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
    ) -> ObservabilityAlertDisposition | None:
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(ObservabilityAlertDispositionModel)
                .where(
                    ObservabilityAlertDispositionModel.tenant_id == tenant_id,
                    ObservabilityAlertDispositionModel.agent_id == agent_id,
                    ObservabilityAlertDispositionModel.source_type == source_type,
                    ObservabilityAlertDispositionModel.source_key == source_key,
                )
                .with_for_update()
            )
            if row is None:
                return None
            removed = self._observability_disposition(row)
            lifecycle_statement = select(ObservabilityAlertLifecycleModel).where(
                ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                ObservabilityAlertLifecycleModel.agent_id == agent_id,
                ObservabilityAlertLifecycleModel.source_type == source_type,
                ObservabilityAlertLifecycleModel.source_key == source_key,
            )
            if lifecycle_id is not None:
                lifecycle_statement = lifecycle_statement.where(
                    ObservabilityAlertLifecycleModel.id == lifecycle_id
                )
            lifecycle = await session.scalar(
                lifecycle_statement.order_by(
                    ObservabilityAlertLifecycleModel.updated_at.desc()
                ).limit(1)
            )
            if lifecycle is None:
                return None
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="observability_alert.disposition_cleared",
                    resource_type="observability_alert_disposition",
                    resource_id=removed.alert_key,
                    detail={
                        "agent_id": str(agent_id),
                        "code": removed.code,
                        "status": removed.status.value,
                        "expires_at": (
                            removed.expires_at.isoformat()
                            if removed.expires_at is not None
                            else None
                        ),
                    },
                )
            )
            session.add(
                ObservabilityAlertDispositionEventModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    lifecycle_id=lifecycle.id,
                    source_type=source_type,
                    source_key=source_key,
                    code=removed.code,
                    action=ObservabilityAlertDispositionAction.CLEARED.value,
                    reason=reason,
                    actor_id=actor_id,
                    expires_at=removed.expires_at,
                    occurred_at=datetime.now(UTC),
                )
            )
            await session.delete(row)
            return removed

    async def batch_clear_observability_alert_dispositions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        actor_id: UUID,
        reason: str,
    ) -> tuple[ObservabilityAlertDisposition, ...]:
        async with self._session_factory.begin() as session:
            lifecycles = (
                await session.scalars(
                    select(ObservabilityAlertLifecycleModel)
                    .where(
                        ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                        ObservabilityAlertLifecycleModel.agent_id == agent_id,
                        ObservabilityAlertLifecycleModel.id.in_(lifecycle_ids),
                    )
                    .order_by(ObservabilityAlertLifecycleModel.id)
                    .with_for_update()
                )
            ).all()
            by_id = {row.id: row for row in lifecycles}
            if len(by_id) != len(lifecycle_ids):
                raise LookupError("通用告警生命周期不存在")
            dispositions = (
                await session.scalars(
                    select(ObservabilityAlertDispositionModel)
                    .where(
                        ObservabilityAlertDispositionModel.tenant_id == tenant_id,
                        ObservabilityAlertDispositionModel.agent_id == agent_id,
                        ObservabilityAlertDispositionModel.source_type.in_(
                            tuple(by_id[item].source_type for item in lifecycle_ids)
                        ),
                        ObservabilityAlertDispositionModel.source_key.in_(
                            tuple(by_id[item].source_key for item in lifecycle_ids)
                        ),
                    )
                    .order_by(
                        ObservabilityAlertDispositionModel.source_type,
                        ObservabilityAlertDispositionModel.source_key,
                    )
                    .with_for_update()
                )
            ).all()
            disposition_by_key = {(row.source_type, row.source_key): row for row in dispositions}
            if any(
                (by_id[item].source_type, by_id[item].source_key) not in disposition_by_key
                for item in lifecycle_ids
            ):
                raise LookupError("通用告警处置不存在")
            result: list[ObservabilityAlertDisposition] = []
            occurred_at = datetime.now(UTC)
            for lifecycle_id in lifecycle_ids:
                lifecycle = by_id[lifecycle_id]
                row = disposition_by_key[(lifecycle.source_type, lifecycle.source_key)]
                removed = self._observability_disposition(row)
                result.append(removed)
                session.add(
                    AuditLog(
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        action="observability_alert.disposition_cleared",
                        resource_type="observability_alert_disposition",
                        resource_id=removed.alert_key,
                        detail={
                            "agent_id": str(agent_id),
                            "code": removed.code,
                            "status": removed.status.value,
                            "expires_at": (
                                removed.expires_at.isoformat()
                                if removed.expires_at is not None
                                else None
                            ),
                            "batch": True,
                        },
                    )
                )
                session.add(
                    ObservabilityAlertDispositionEventModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        lifecycle_id=lifecycle.id,
                        source_type=lifecycle.source_type,
                        source_key=lifecycle.source_key,
                        code=removed.code,
                        action=ObservabilityAlertDispositionAction.CLEARED.value,
                        reason=reason,
                        actor_id=actor_id,
                        expires_at=removed.expires_at,
                        occurred_at=occurred_at,
                    )
                )
                await session.delete(row)
            return tuple(result)

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
    ) -> tuple[ObservabilityAlertDispositionEvent, ...]:
        statement = select(ObservabilityAlertDispositionEventModel).where(
            ObservabilityAlertDispositionEventModel.tenant_id == tenant_id,
            ObservabilityAlertDispositionEventModel.agent_id == agent_id,
        )
        if lifecycle_id is not None:
            statement = statement.where(
                ObservabilityAlertDispositionEventModel.lifecycle_id == lifecycle_id
            )
        if source_type is not None:
            statement = statement.where(
                ObservabilityAlertDispositionEventModel.source_type == source_type
            )
        if source_key is not None:
            statement = statement.where(
                ObservabilityAlertDispositionEventModel.source_key == source_key
            )
        if action is not None:
            statement = statement.where(
                ObservabilityAlertDispositionEventModel.action == action.value
            )
        if occurred_after is not None:
            statement = statement.where(
                ObservabilityAlertDispositionEventModel.occurred_at >= occurred_after
            )
        if occurred_before is not None:
            statement = statement.where(
                ObservabilityAlertDispositionEventModel.occurred_at <= occurred_before
            )
        statement = statement.order_by(
            ObservabilityAlertDispositionEventModel.occurred_at.desc()
        ).limit(limit)
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._observability_disposition_event(row) for row in rows)

    async def record_observability_alert_replay_review(
        self, review: ObservabilityAlertReplayReview
    ) -> ObservabilityAlertReplayReview:
        async with self._session_factory.begin() as session:
            session.add(
                ObservabilityAlertReplayReviewModel(
                    id=review.id,
                    tenant_id=review.tenant_id,
                    agent_id=review.agent_id,
                    source_job_id=review.source_job_id,
                    source_type=review.source_type,
                    source_key=review.source_key,
                    decision=review.decision.value,
                    reason_code=review.reason_code.value,
                    actor_id=review.actor_id,
                    suppression_expires_at=review.suppression_expires_at,
                    reviewed_at=review.reviewed_at,
                )
            )
            session.add(
                AuditLog(
                    tenant_id=review.tenant_id,
                    actor_id=review.actor_id,
                    action=f"observability_alert.replay_review_{review.decision.value}",
                    resource_type="observability_alert_replay_review",
                    resource_id=str(review.source_job_id or review.id),
                    detail={
                        "agent_id": str(review.agent_id) if review.agent_id is not None else None,
                        "source_type": review.source_type,
                        "source_key": review.source_key,
                        "decision": review.decision.value,
                        "reason_code": review.reason_code.value,
                        "suppression_expires_at": (
                            review.suppression_expires_at.isoformat()
                            if review.suppression_expires_at is not None
                            else None
                        ),
                    },
                )
            )
        return review

    async def get_observability_alert_recommendation_feedback(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_id: UUID,
    ) -> ObservabilityAlertRecommendationFeedback | None:
        statement = select(ObservabilityAlertRecommendationFeedbackModel).where(
            ObservabilityAlertRecommendationFeedbackModel.tenant_id == tenant_id,
            ObservabilityAlertRecommendationFeedbackModel.agent_id == agent_id,
            ObservabilityAlertRecommendationFeedbackModel.lifecycle_id == lifecycle_id,
        )
        async with self._session_factory() as session:
            row = await session.scalar(statement)
        return None if row is None else self._observability_recommendation_feedback(row)

    async def save_observability_alert_recommendation_feedback(
        self, feedback: ObservabilityAlertRecommendationFeedback
    ) -> ObservabilityAlertRecommendationFeedback:
        values = {
            "id": feedback.id,
            "tenant_id": feedback.tenant_id,
            "agent_id": feedback.agent_id,
            "lifecycle_id": feedback.lifecycle_id,
            "source_type": feedback.source_type,
            "source_key": feedback.source_key,
            "code": feedback.code,
            "recommendation_action": feedback.recommendation_action.value,
            "priority": feedback.priority.value,
            "reason_codes": [item.value for item in feedback.reason_codes],
            "decision": feedback.decision.value,
            "alternative_action": (
                feedback.alternative_action.value
                if feedback.alternative_action is not None
                else None
            ),
            "actor_id": feedback.actor_id,
            "feedback_at": feedback.feedback_at,
        }
        async with self._session_factory.begin() as session:
            inserted_id = await session.scalar(
                pg_insert(ObservabilityAlertRecommendationFeedbackModel)
                .values(**values)
                .on_conflict_do_nothing(
                    constraint="uq_observability_alert_recommendation_feedback_lifecycle"
                )
                .returning(ObservabilityAlertRecommendationFeedbackModel.id)
            )
            row = await session.scalar(
                select(ObservabilityAlertRecommendationFeedbackModel).where(
                    ObservabilityAlertRecommendationFeedbackModel.tenant_id == feedback.tenant_id,
                    ObservabilityAlertRecommendationFeedbackModel.agent_id == feedback.agent_id,
                    ObservabilityAlertRecommendationFeedbackModel.lifecycle_id
                    == feedback.lifecycle_id,
                )
            )
            if row is None:
                raise RuntimeError("建议反馈写入后未找到记录")
            stored = self._observability_recommendation_feedback(row)
            if inserted_id is None:
                if stored.decision is feedback.decision:
                    return stored
                raise ValueError("该告警生命周期已提交不同的建议反馈")
            session.add(
                AuditLog(
                    tenant_id=feedback.tenant_id,
                    actor_id=feedback.actor_id,
                    action=f"observability_alert.recommendation_feedback_{feedback.decision.value}",
                    resource_type="observability_alert_recommendation_feedback",
                    resource_id=str(feedback.lifecycle_id),
                    detail={
                        "agent_id": str(feedback.agent_id),
                        "code": feedback.code,
                        "recommendation_action": feedback.recommendation_action.value,
                        "priority": feedback.priority.value,
                        "reason_codes": [item.value for item in feedback.reason_codes],
                        "decision": feedback.decision.value,
                        "alternative_action": (
                            feedback.alternative_action.value
                            if feedback.alternative_action is not None
                            else None
                        ),
                    },
                )
            )
            return stored

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
    ) -> tuple[ObservabilityAlertRecommendationFeedback, ...]:
        statement = select(ObservabilityAlertRecommendationFeedbackModel).where(
            ObservabilityAlertRecommendationFeedbackModel.tenant_id == tenant_id,
            ObservabilityAlertRecommendationFeedbackModel.agent_id == agent_id,
        )
        if lifecycle_ids is not None:
            if not lifecycle_ids:
                return ()
            statement = statement.where(
                ObservabilityAlertRecommendationFeedbackModel.lifecycle_id.in_(lifecycle_ids)
            )
        if window_started_at is not None:
            statement = statement.where(
                ObservabilityAlertRecommendationFeedbackModel.feedback_at >= window_started_at
            )
        if window_ended_at is not None:
            statement = statement.where(
                ObservabilityAlertRecommendationFeedbackModel.feedback_at <= window_ended_at
            )
        if source_type is not None:
            statement = statement.where(
                ObservabilityAlertRecommendationFeedbackModel.source_type == source_type
            )
        statement = statement.order_by(
            ObservabilityAlertRecommendationFeedbackModel.feedback_at.desc(),
            ObservabilityAlertRecommendationFeedbackModel.id.desc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._observability_recommendation_feedback(row) for row in rows)

    async def count_observability_alert_lifecycle_statuses(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
    ) -> dict[ObservabilityAlertLifecycleStatus, int]:
        if not lifecycle_ids:
            return {status: 0 for status in ObservabilityAlertLifecycleStatus}
        statement = (
            select(ObservabilityAlertLifecycleModel.status, func.count())
            .where(
                ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                ObservabilityAlertLifecycleModel.agent_id == agent_id,
                ObservabilityAlertLifecycleModel.id.in_(lifecycle_ids),
            )
            .group_by(ObservabilityAlertLifecycleModel.status)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        counts = {status: 0 for status in ObservabilityAlertLifecycleStatus}
        counts.update({ObservabilityAlertLifecycleStatus(row[0]): int(row[1]) for row in rows})
        return counts

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
    ) -> tuple[ObservabilityAlertReplayReview, ...]:
        statement = select(ObservabilityAlertReplayReviewModel).where(
            ObservabilityAlertReplayReviewModel.tenant_id == tenant_id,
            ObservabilityAlertReplayReviewModel.agent_id == agent_id,
        )
        if decision is not None:
            statement = statement.where(
                ObservabilityAlertReplayReviewModel.decision == decision.value
            )
        if reason_code is not None:
            statement = statement.where(
                ObservabilityAlertReplayReviewModel.reason_code == reason_code.value
            )
        if source_type is not None:
            statement = statement.where(
                ObservabilityAlertReplayReviewModel.source_type == source_type
            )
        if cursor is not None:
            statement = statement.where(
                or_(
                    ObservabilityAlertReplayReviewModel.reviewed_at < cursor.occurred_at,
                    and_(
                        ObservabilityAlertReplayReviewModel.reviewed_at == cursor.occurred_at,
                        ObservabilityAlertReplayReviewModel.id < cursor.entity_id,
                    ),
                )
            )
        statement = statement.order_by(
            ObservabilityAlertReplayReviewModel.reviewed_at.desc(),
            ObservabilityAlertReplayReviewModel.id.desc(),
        ).limit(limit)
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._observability_replay_review(row) for row in rows)

    async def get_observability_alert_replay_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        source_type: str | None = None,
    ) -> ObservabilityAlertReplayMetrics:
        filters = (
            ObservabilityAlertReplayReviewModel.tenant_id == tenant_id,
            ObservabilityAlertReplayReviewModel.agent_id == agent_id,
            ObservabilityAlertReplayReviewModel.reviewed_at >= window_started_at,
            ObservabilityAlertReplayReviewModel.reviewed_at <= window_ended_at,
            *(
                ()
                if source_type is None
                else (ObservabilityAlertReplayReviewModel.source_type == source_type,)
            ),
        )
        async with self._session_factory() as session:
            total_row = (
                await session.execute(
                    select(
                        func.count(ObservabilityAlertReplayReviewModel.id),
                        func.count(ObservabilityAlertReplayReviewModel.id).filter(
                            ObservabilityAlertReplayReviewModel.decision
                            == ObservabilityAlertReplayDecision.ALLOWED.value
                        ),
                        func.count(ObservabilityAlertReplayReviewModel.id).filter(
                            ObservabilityAlertReplayReviewModel.decision
                            == ObservabilityAlertReplayDecision.BLOCKED.value
                        ),
                    ).where(*filters)
                )
            ).one()
            reason_rows = (
                await session.execute(
                    select(
                        ObservabilityAlertReplayReviewModel.reason_code,
                        func.count(ObservabilityAlertReplayReviewModel.id),
                    )
                    .where(*filters)
                    .group_by(ObservabilityAlertReplayReviewModel.reason_code)
                    .order_by(ObservabilityAlertReplayReviewModel.reason_code)
                )
            ).all()
            source_rows = (
                await session.execute(
                    select(
                        ObservabilityAlertReplayReviewModel.source_type,
                        func.count(ObservabilityAlertReplayReviewModel.id),
                        func.count(ObservabilityAlertReplayReviewModel.id).filter(
                            ObservabilityAlertReplayReviewModel.decision
                            == ObservabilityAlertReplayDecision.ALLOWED.value
                        ),
                        func.count(ObservabilityAlertReplayReviewModel.id).filter(
                            ObservabilityAlertReplayReviewModel.decision
                            == ObservabilityAlertReplayDecision.BLOCKED.value
                        ),
                    )
                    .where(*filters)
                    .group_by(ObservabilityAlertReplayReviewModel.source_type)
                    .order_by(ObservabilityAlertReplayReviewModel.source_type)
                )
            ).all()
        return ObservabilityAlertReplayMetrics(
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
            total=int(total_row[0]),
            allowed=int(total_row[1]),
            blocked=int(total_row[2]),
            reasons=tuple(
                ObservabilityAlertReplayReasonMetrics(
                    reason_code=ObservabilityAlertReplayReason(row[0]), count=int(row[1])
                )
                for row in reason_rows
            ),
            sources=tuple(
                ObservabilityAlertReplaySourceMetrics(
                    source_type=row[0],
                    total=int(row[1]),
                    allowed=int(row[2]),
                    blocked=int(row[3]),
                )
                for row in source_rows
            ),
        )

    async def collect_observability_alert_history(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        max_records: int,
    ) -> ObservabilityAlertHistorySnapshot:
        lifecycle_filters = (
            ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
            ObservabilityAlertLifecycleModel.agent_id == agent_id,
            ObservabilityAlertLifecycleModel.updated_at.between(window_started_at, window_ended_at),
        )
        event_filters = (
            ObservabilityAlertDispositionEventModel.tenant_id == tenant_id,
            ObservabilityAlertDispositionEventModel.agent_id == agent_id,
            ObservabilityAlertDispositionEventModel.occurred_at.between(
                window_started_at, window_ended_at
            ),
        )
        review_filters = (
            ObservabilityAlertReplayReviewModel.tenant_id == tenant_id,
            ObservabilityAlertReplayReviewModel.agent_id == agent_id,
            ObservabilityAlertReplayReviewModel.reviewed_at.between(
                window_started_at, window_ended_at
            ),
        )
        feedback_filters = (
            ObservabilityAlertRecommendationFeedbackModel.tenant_id == tenant_id,
            ObservabilityAlertRecommendationFeedbackModel.agent_id == agent_id,
            ObservabilityAlertRecommendationFeedbackModel.feedback_at.between(
                window_started_at, window_ended_at
            ),
        )
        async with self._session_factory() as session:
            counts = (
                await session.scalar(
                    select(func.count(ObservabilityAlertLifecycleModel.id)).where(
                        *lifecycle_filters
                    )
                )
                or 0,
                await session.scalar(
                    select(func.count(ObservabilityAlertDispositionEventModel.id)).where(
                        *event_filters
                    )
                )
                or 0,
                await session.scalar(
                    select(func.count(ObservabilityAlertReplayReviewModel.id)).where(
                        *review_filters
                    )
                )
                or 0,
                await session.scalar(
                    select(func.count(ObservabilityAlertRecommendationFeedbackModel.id)).where(
                        *feedback_filters
                    )
                )
                or 0,
            )
            if sum(counts) > max_records:
                return ObservabilityAlertHistorySnapshot((), (), (), (), truncated=True)
            lifecycles = (
                await session.scalars(
                    select(ObservabilityAlertLifecycleModel)
                    .where(*lifecycle_filters)
                    .order_by(
                        ObservabilityAlertLifecycleModel.updated_at,
                        ObservabilityAlertLifecycleModel.id,
                    )
                    .limit(max_records + 1)
                )
            ).all()
            events = (
                await session.scalars(
                    select(ObservabilityAlertDispositionEventModel)
                    .where(*event_filters)
                    .order_by(
                        ObservabilityAlertDispositionEventModel.occurred_at,
                        ObservabilityAlertDispositionEventModel.id,
                    )
                    .limit(max_records + 1)
                )
            ).all()
            reviews = (
                await session.scalars(
                    select(ObservabilityAlertReplayReviewModel)
                    .where(*review_filters)
                    .order_by(
                        ObservabilityAlertReplayReviewModel.reviewed_at,
                        ObservabilityAlertReplayReviewModel.id,
                    )
                    .limit(max_records + 1)
                )
            ).all()
            feedback = (
                await session.scalars(
                    select(ObservabilityAlertRecommendationFeedbackModel)
                    .where(*feedback_filters)
                    .order_by(
                        ObservabilityAlertRecommendationFeedbackModel.feedback_at,
                        ObservabilityAlertRecommendationFeedbackModel.id,
                    )
                    .limit(max_records + 1)
                )
            ).all()
        return ObservabilityAlertHistorySnapshot(
            lifecycles=tuple(self._observability_lifecycle(item) for item in lifecycles),
            disposition_events=tuple(
                self._observability_disposition_event(item) for item in events
            ),
            replay_reviews=tuple(self._observability_replay_review(item) for item in reviews),
            recommendation_feedback=tuple(
                self._observability_recommendation_feedback(item) for item in feedback
            ),
        )

    async def purge_observability_alert_history(
        self,
        *,
        tenant_id: UUID,
        disposition_events_before: datetime,
        replay_reviews_before: datetime,
        recommendation_feedback_before: datetime,
        limit: int,
    ) -> ObservabilityHistoryRetentionResult:
        async with self._session_factory.begin() as session:
            event_ids = list(
                (
                    await session.scalars(
                        select(ObservabilityAlertDispositionEventModel.id)
                        .where(
                            ObservabilityAlertDispositionEventModel.tenant_id == tenant_id,
                            ObservabilityAlertDispositionEventModel.occurred_at
                            <= disposition_events_before,
                        )
                        .order_by(
                            ObservabilityAlertDispositionEventModel.occurred_at,
                            ObservabilityAlertDispositionEventModel.id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            review_ids = list(
                (
                    await session.scalars(
                        select(ObservabilityAlertReplayReviewModel.id)
                        .where(
                            ObservabilityAlertReplayReviewModel.tenant_id == tenant_id,
                            ObservabilityAlertReplayReviewModel.reviewed_at
                            <= replay_reviews_before,
                        )
                        .order_by(
                            ObservabilityAlertReplayReviewModel.reviewed_at,
                            ObservabilityAlertReplayReviewModel.id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            feedback_ids = list(
                (
                    await session.scalars(
                        select(ObservabilityAlertRecommendationFeedbackModel.id)
                        .where(
                            ObservabilityAlertRecommendationFeedbackModel.tenant_id == tenant_id,
                            ObservabilityAlertRecommendationFeedbackModel.feedback_at
                            <= recommendation_feedback_before,
                        )
                        .order_by(
                            ObservabilityAlertRecommendationFeedbackModel.feedback_at,
                            ObservabilityAlertRecommendationFeedbackModel.id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            if event_ids:
                await session.execute(
                    delete(ObservabilityAlertDispositionEventModel).where(
                        ObservabilityAlertDispositionEventModel.id.in_(event_ids)
                    )
                )
            if review_ids:
                await session.execute(
                    delete(ObservabilityAlertReplayReviewModel).where(
                        ObservabilityAlertReplayReviewModel.id.in_(review_ids)
                    )
                )
            if feedback_ids:
                await session.execute(
                    delete(ObservabilityAlertRecommendationFeedbackModel).where(
                        ObservabilityAlertRecommendationFeedbackModel.id.in_(feedback_ids)
                    )
                )
            return ObservabilityHistoryRetentionResult(
                disposition_events_purged=len(event_ids),
                replay_reviews_purged=len(review_ids),
                recommendation_feedback_purged=len(feedback_ids),
            )

    async def mark_observability_alert_lifecycles_escalated(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        escalation_level: int,
        escalated_at: datetime,
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        async with self._session_factory.begin() as session:
            rows = (
                await session.scalars(
                    select(ObservabilityAlertLifecycleModel)
                    .where(
                        ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                        ObservabilityAlertLifecycleModel.agent_id == agent_id,
                        ObservabilityAlertLifecycleModel.id.in_(lifecycle_ids),
                        ObservabilityAlertLifecycleModel.status
                        == ObservabilityAlertLifecycleStatus.ACTIVE.value,
                    )
                    .with_for_update()
                )
            ).all()
            updated: list[ObservabilityAlertLifecycle] = []
            for row in rows:
                if row.escalation_level >= escalation_level:
                    continue
                row.escalated_at = row.escalated_at or escalated_at
                row.escalation_level = escalation_level
                row.last_escalated_at = escalated_at
                row.updated_at = escalated_at
                updated.append(self._observability_lifecycle(row))
            return tuple(updated)

    async def list_observability_agent_ids(self) -> tuple[tuple[UUID, UUID], ...]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Agent.tenant_id, Agent.id)
                    .where(Agent.status == "active")
                    .order_by(Agent.tenant_id, Agent.id)
                )
            ).all()
        return tuple((row[0], row[1]) for row in rows)

    @staticmethod
    def _observability_lifecycle(
        row: ObservabilityAlertLifecycleModel,
    ) -> ObservabilityAlertLifecycle:
        return ObservabilityAlertLifecycle(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            source_type=row.source_type,
            source_key=row.source_key,
            code=row.code,
            status=ObservabilityAlertLifecycleStatus(row.status),
            severity=AlertSeverity(row.severity),
            occurrences=row.occurrences,
            current_value=float(row.current_value),
            threshold_value=float(row.threshold_value),
            unit=row.unit,
            first_occurred_at=row.first_occurred_at,
            last_occurred_at=row.last_occurred_at,
            last_evaluated_at=row.last_evaluated_at,
            escalated_at=row.escalated_at,
            resolved_at=row.resolved_at,
            recovery_duration_seconds=row.recovery_duration_seconds,
            created_at=row.created_at,
            updated_at=row.updated_at,
            escalation_level=row.escalation_level,
            last_escalated_at=row.last_escalated_at,
        )

    @staticmethod
    def _observability_disposition(
        row: ObservabilityAlertDispositionModel,
    ) -> ObservabilityAlertDisposition:
        return ObservabilityAlertDisposition(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            source_type=row.source_type,
            source_key=row.source_key,
            code=row.code,
            status=ObservabilityAlertDispositionStatus(row.status),
            reason=row.reason,
            actor_id=row.actor_id,
            expires_at=row.expires_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _observability_disposition_event(
        row: ObservabilityAlertDispositionEventModel,
    ) -> ObservabilityAlertDispositionEvent:
        return ObservabilityAlertDispositionEvent(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            lifecycle_id=row.lifecycle_id,
            source_type=row.source_type,
            source_key=row.source_key,
            code=row.code,
            action=ObservabilityAlertDispositionAction(row.action),
            reason=row.reason,
            actor_id=row.actor_id,
            expires_at=row.expires_at,
            occurred_at=row.occurred_at,
        )

    @staticmethod
    def _observability_recommendation_feedback(
        row: ObservabilityAlertRecommendationFeedbackModel,
    ) -> ObservabilityAlertRecommendationFeedback:
        return ObservabilityAlertRecommendationFeedback(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            lifecycle_id=row.lifecycle_id,
            source_type=row.source_type,
            source_key=row.source_key,
            code=row.code,
            recommendation_action=ObservabilityAlertRecommendationAction(row.recommendation_action),
            priority=ObservabilityAlertRecommendationPriority(row.priority),
            reason_codes=tuple(
                ObservabilityAlertRecommendationReason(item) for item in row.reason_codes
            ),
            decision=ObservabilityAlertRecommendationFeedbackDecision(row.decision),
            actor_id=row.actor_id,
            feedback_at=row.feedback_at,
            alternative_action=(
                ObservabilityAlertRecommendationAction(row.alternative_action)
                if row.alternative_action is not None
                else None
            ),
        )

    @staticmethod
    def _observability_replay_review(
        row: ObservabilityAlertReplayReviewModel,
    ) -> ObservabilityAlertReplayReview:
        return ObservabilityAlertReplayReview(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            source_job_id=row.source_job_id,
            source_type=row.source_type,
            source_key=row.source_key,
            decision=ObservabilityAlertReplayDecision(row.decision),
            reason_code=ObservabilityAlertReplayReason(row.reason_code),
            actor_id=row.actor_id,
            suppression_expires_at=row.suppression_expires_at,
            reviewed_at=row.reviewed_at,
        )

    @staticmethod
    async def _channel_delivery_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
        agent_id: UUID | None = None,
    ) -> ChannelDeliveryMetrics:
        statement = select(
            func.count(ChannelDiagnosticEventModel.id).filter(
                ChannelDiagnosticEventModel.status.in_(
                    ("delivered", "degraded", "failed", "rejected", "rate_limited")
                )
            ),
            func.count(ChannelDiagnosticEventModel.id).filter(
                ChannelDiagnosticEventModel.status == "delivered"
            ),
            func.count(ChannelDiagnosticEventModel.id).filter(
                ChannelDiagnosticEventModel.status == "degraded"
            ),
            func.count(ChannelDiagnosticEventModel.id).filter(
                ChannelDiagnosticEventModel.status.in_(("failed", "rejected"))
            ),
            func.count(ChannelDiagnosticEventModel.id).filter(
                ChannelDiagnosticEventModel.status == "rate_limited"
            ),
        ).where(
            ChannelDiagnosticEventModel.tenant_id == tenant_id,
            ChannelDiagnosticEventModel.direction == "outbound",
            ChannelDiagnosticEventModel.occurred_at >= started_at,
            ChannelDiagnosticEventModel.occurred_at <= ended_at,
        )
        if agent_id is not None:
            statement = statement.join(
                ChannelInstanceModel,
                ChannelInstanceModel.id == ChannelDiagnosticEventModel.channel_id,
            ).where(ChannelInstanceModel.agent_id == agent_id)
        row = (await session.execute(statement)).one()
        return ChannelDeliveryMetrics(
            attempts=int(row[0] or 0),
            delivered=int(row[1] or 0),
            degraded=int(row[2] or 0),
            failed=int(row[3] or 0),
            rate_limited=int(row[4] or 0),
        )

    @staticmethod
    async def _notification_delivery_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
        agent_id: UUID | None = None,
    ) -> NotificationDeliveryMetrics:
        window_statement = select(
            func.count(BackgroundJobModel.id),
            func.count(BackgroundJobModel.id).filter(BackgroundJobModel.status == "pending"),
            func.count(BackgroundJobModel.id).filter(BackgroundJobModel.status == "running"),
            func.count(BackgroundJobModel.id).filter(BackgroundJobModel.status == "retrying"),
            func.count(BackgroundJobModel.id).filter(BackgroundJobModel.status == "succeeded"),
            func.count(BackgroundJobModel.id).filter(BackgroundJobModel.status == "failed"),
        ).where(
            BackgroundJobModel.tenant_id == tenant_id,
            BackgroundJobModel.kind == "notification_delivery",
            BackgroundJobModel.created_at >= started_at,
            BackgroundJobModel.created_at <= ended_at,
        )
        replay_job = aliased(BackgroundJobModel)
        has_replay = (
            select(replay_job.id)
            .where(replay_job.replayed_from_id == BackgroundJobModel.id)
            .exists()
        )
        dead_letter_statement = select(func.count(BackgroundJobModel.id)).where(
            BackgroundJobModel.tenant_id == tenant_id,
            BackgroundJobModel.kind == "notification_delivery",
            BackgroundJobModel.status == "dead_letter",
            ~has_replay,
        )
        if agent_id is not None:
            agent_condition = BackgroundJobModel.agent_id == agent_id
            window_statement = window_statement.where(agent_condition)
            dead_letter_statement = dead_letter_statement.where(agent_condition)
        row = (await session.execute(window_statement)).one()
        dead_letters = int((await session.scalar(dead_letter_statement)) or 0)
        return NotificationDeliveryMetrics(
            total=int(row[0] or 0),
            pending=int(row[1] or 0),
            running=int(row[2] or 0),
            retrying=int(row[3] or 0),
            succeeded=int(row[4] or 0),
            failed=int(row[5] or 0),
            dead_letters=dead_letters,
        )

    @staticmethod
    async def _api_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
        agent_id: UUID | None = None,
    ) -> ApiSloMetrics:
        statement = select(
            func.count(ApiRequestMetricModel.id),
            func.count(ApiRequestMetricModel.id).filter(ApiRequestMetricModel.status_code >= 500),
            *_percentile_columns(ApiRequestMetricModel.duration_ms),
        ).where(
            ApiRequestMetricModel.tenant_id == tenant_id,
            ApiRequestMetricModel.occurred_at >= started_at,
            ApiRequestMetricModel.occurred_at <= ended_at,
        )
        if agent_id is not None:
            statement = statement.where(ApiRequestMetricModel.agent_id == agent_id)
        row = (await session.execute(statement)).one()
        requests, errors = int(row[0]), int(row[1])
        return ApiSloMetrics(
            requests=requests,
            server_errors=errors,
            error_rate_percent=_rate(errors, requests),
            latency=_row_percentiles(row, 2),
        )

    @staticmethod
    async def _agent_run_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
        agent_id: UUID | None = None,
    ) -> AgentRunSloMetrics:
        duration_ms = (
            func.extract("epoch", AgentRunModel.completed_at - AgentRunModel.started_at) * 1000
        )
        terminal_statuses = ("completed", "failed", "cancelled")
        statement = select(
            func.count(AgentRunModel.id),
            func.count(AgentRunModel.id).filter(AgentRunModel.status == "completed"),
            func.count(AgentRunModel.id).filter(AgentRunModel.status != "completed"),
            *_percentile_columns(duration_ms),
        ).where(
            AgentRunModel.tenant_id == tenant_id,
            AgentRunModel.status.in_(terminal_statuses),
            AgentRunModel.started_at.is_not(None),
            AgentRunModel.completed_at >= started_at,
            AgentRunModel.completed_at <= ended_at,
        )
        if agent_id is not None:
            statement = statement.where(AgentRunModel.agent_id == agent_id)
        row = (await session.execute(statement)).one()
        total, completed, unsuccessful = int(row[0]), int(row[1]), int(row[2])
        return AgentRunSloMetrics(
            terminal_runs=total,
            completed_runs=completed,
            unsuccessful_runs=unsuccessful,
            success_rate_percent=100.0 if total == 0 else _rate(completed, total),
            latency=_row_percentiles(row, 3),
        )

    @staticmethod
    async def _model_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
        agent_id: UUID | None = None,
    ) -> tuple[ModelUsageMetrics, ...]:
        statement = (
            select(
                ModelInvocationModel.provider,
                ModelInvocationModel.model,
                func.count(ModelInvocationModel.id),
                func.count(ModelInvocationModel.id).filter(
                    ModelInvocationModel.status.in_(("failed", "timed_out"))
                ),
                func.coalesce(func.sum(ModelInvocationModel.input_tokens), 0),
                func.coalesce(func.sum(ModelInvocationModel.output_tokens), 0),
                func.coalesce(func.sum(ModelInvocationModel.estimated_cost_microusd), 0),
                *_percentile_columns(ModelInvocationModel.latency_ms),
            )
            .where(
                ModelInvocationModel.tenant_id == tenant_id,
                ModelInvocationModel.created_at >= started_at,
                ModelInvocationModel.created_at <= ended_at,
            )
            .group_by(ModelInvocationModel.provider, ModelInvocationModel.model)
            .order_by(func.sum(ModelInvocationModel.estimated_cost_microusd).desc())
        )
        if agent_id is not None:
            statement = statement.join(
                AgentRunModel,
                AgentRunModel.id == ModelInvocationModel.run_id,
            ).where(AgentRunModel.agent_id == agent_id)
        rows = (await session.execute(statement)).all()
        return tuple(
            ModelUsageMetrics(
                provider=str(row[0]),
                model=str(row[1]),
                invocations=int(row[2]),
                failed_invocations=int(row[3]),
                input_tokens=int(row[4]),
                output_tokens=int(row[5]),
                estimated_cost_microusd=int(row[6]),
                latency=_row_percentiles(row, 7),
            )
            for row in rows
        )

    @staticmethod
    async def _queue_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        now: datetime,
        agent_id: UUID | None = None,
    ) -> QueueMetrics:
        statement = select(
            func.count(BackgroundJobModel.id),
            func.min(BackgroundJobModel.available_at),
        ).where(
            BackgroundJobModel.tenant_id == tenant_id,
            BackgroundJobModel.status.in_(("pending", "retrying")),
        )
        if agent_id is not None:
            statement = statement.where(BackgroundJobModel.agent_id == agent_id)
        row = (await session.execute(statement)).one()
        oldest = row[1]
        wait_seconds = (
            max(0, int((now - oldest).total_seconds())) if isinstance(oldest, datetime) else 0
        )
        return QueueMetrics(backlog=int(row[0]), oldest_wait_seconds=wait_seconds)


def _percentile_columns(value: Any) -> tuple[Any, Any, Any]:
    return (
        func.percentile_cont(0.5).within_group(value),
        func.percentile_cont(0.95).within_group(value),
        func.percentile_cont(0.99).within_group(value),
    )


def _row_percentiles(row: Row[Any], offset: int) -> LatencyPercentiles:
    return LatencyPercentiles(
        p50_ms=max(0, round(float(row[offset] or 0))),
        p95_ms=max(0, round(float(row[offset + 1] or 0))),
        p99_ms=max(0, round(float(row[offset + 2] or 0))),
    )


def _memory_percentiles(values: tuple[int, ...]) -> LatencyPercentiles:
    if not values:
        return LatencyPercentiles(0, 0, 0)
    ordered = sorted(values)

    def percentile(value: float) -> int:
        return ordered[max(0, ceil(len(ordered) * value) - 1)]

    return LatencyPercentiles(percentile(0.5), percentile(0.95), percentile(0.99))


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator * 100 / denominator, 4) if denominator else 0.0
