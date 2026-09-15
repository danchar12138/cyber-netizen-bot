"""可观测请求指标写入与 PostgreSQL 租户、Agent 隔离聚合。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from cnb_application import ApiRequestObservation, ObservabilityAlertLifecycleReconciliation
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
    ObservabilityAlertDispositionStatus,
    ObservabilityAlertLifecycle,
    ObservabilityAlertLifecycleStatus,
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
    ObservabilityAlertDispositionModel,
    ObservabilityAlertLifecycleModel,
)


class MemoryObservabilityRepository:
    """供测试与无数据库联调使用的请求指标仓储。"""

    def __init__(self) -> None:
        self._requests: list[ApiRequestObservation] = []
        self._lifecycles: dict[UUID, ObservabilityAlertLifecycle] = {}
        self._dispositions: dict[tuple[UUID, UUID, str, str], ObservabilityAlertDisposition] = {}
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
            ]
            return tuple(sorted(values, key=lambda item: item.updated_at, reverse=True)[:limit])

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
    ) -> tuple[ObservabilityAlertDisposition, ...]:
        async with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._dispositions.values()
                        if item.tenant_id == tenant_id and item.agent_id == agent_id
                    ),
                    key=lambda item: item.updated_at,
                    reverse=True,
                )
            )

    async def save_observability_alert_disposition(
        self, disposition: ObservabilityAlertDisposition
    ) -> ObservabilityAlertDisposition:
        async with self._lock:
            lifecycle_exists = any(
                item.tenant_id == disposition.tenant_id
                and item.agent_id == disposition.agent_id
                and item.source_type == disposition.source_type
                and item.source_key == disposition.source_key
                and item.status is ObservabilityAlertLifecycleStatus.ACTIVE
                for item in self._lifecycles.values()
            )
            if not lifecycle_exists:
                raise LookupError("活动通用告警不存在")
            key = (
                disposition.tenant_id,
                disposition.agent_id,
                disposition.source_type,
                disposition.source_key,
            )
            self._dispositions[key] = disposition
            return disposition

    async def clear_observability_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        source_type: str,
        source_key: str,
        actor_id: UUID,
    ) -> ObservabilityAlertDisposition | None:
        del actor_id
        async with self._lock:
            return self._dispositions.pop((tenant_id, agent_id, source_type, source_key), None)

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
        limit: int = 100,
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        statement = (
            select(ObservabilityAlertLifecycleModel)
            .where(
                ObservabilityAlertLifecycleModel.tenant_id == tenant_id,
                ObservabilityAlertLifecycleModel.agent_id == agent_id,
            )
            .order_by(ObservabilityAlertLifecycleModel.updated_at.desc())
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
    ) -> tuple[ObservabilityAlertDisposition, ...]:
        statement = (
            select(ObservabilityAlertDispositionModel)
            .where(
                ObservabilityAlertDispositionModel.tenant_id == tenant_id,
                ObservabilityAlertDispositionModel.agent_id == agent_id,
            )
            .order_by(ObservabilityAlertDispositionModel.updated_at.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._observability_disposition(row) for row in rows)

    async def save_observability_alert_disposition(
        self, disposition: ObservabilityAlertDisposition
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
            await session.flush()
            return self._observability_disposition(row)

    async def clear_observability_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        source_type: str,
        source_key: str,
        actor_id: UUID,
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
            await session.delete(row)
            return removed

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
