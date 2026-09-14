"""可观测请求指标写入与 PostgreSQL 租户、Agent 隔离聚合。"""

import asyncio
from dataclasses import replace
from datetime import datetime
from math import ceil
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from cnb_application import ApiRequestObservation
from cnb_domain import (
    ActiveAlert,
    AgentRunSloMetrics,
    AlertSeverity,
    ApiSloMetrics,
    ChannelDeliveryMetrics,
    LatencyPercentiles,
    ModelUsageMetrics,
    NotificationDeliveryMetrics,
    ObservabilityAlertLifecycle,
    ObservabilityAlertLifecycleStatus,
    ObservabilityMetrics,
    QueueMetrics,
)
from cnb_infrastructure.models import (
    Agent,
    AgentRunModel,
    ApiRequestMetricModel,
    BackgroundJobModel,
    ChannelDiagnosticEventModel,
    ChannelInstanceModel,
    ModelInvocationModel,
    ObservabilityAlertLifecycleModel,
)


class MemoryObservabilityRepository:
    """供测试与无数据库联调使用的请求指标仓储。"""

    def __init__(self) -> None:
        self._requests: list[ApiRequestObservation] = []
        self._lifecycles: dict[tuple[UUID, UUID, str, str], ObservabilityAlertLifecycle] = {}
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
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        async with self._lock:
            keys = {(tenant_id, agent_id, item.source_type, item.alert_key) for item in alerts}
            current = {
                key: item
                for key, item in self._lifecycles.items()
                if key[0] == tenant_id
                and key[1] == agent_id
                and item.status is ObservabilityAlertLifecycleStatus.ACTIVE
            }
            for alert in alerts:
                key = (tenant_id, agent_id, alert.source_type, alert.alert_key)
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
                self._lifecycles[key] = row
            for key, row in tuple(self._lifecycles.items()):
                if (
                    key[0] != tenant_id
                    or key[1] != agent_id
                    or key in keys
                    or row.status is not ObservabilityAlertLifecycleStatus.ACTIVE
                ):
                    continue
                self._lifecycles[key] = replace(
                    row,
                    status=ObservabilityAlertLifecycleStatus.RESOLVED,
                    last_evaluated_at=observed_at,
                    resolved_at=observed_at,
                    recovery_duration_seconds=max(
                        0, int((observed_at - row.first_occurred_at).total_seconds())
                    ),
                    updated_at=observed_at,
                )
            return tuple(
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

    async def list_observability_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ObservabilityAlertLifecycleStatus | None = None,
        limit: int = 100,
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
        async with self._lock:
            values = [
                item
                for item in self._lifecycles.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (status is None or item.status is status)
            ]
            return tuple(sorted(values, key=lambda item: item.updated_at, reverse=True)[:limit])

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
    ) -> tuple[ObservabilityAlertLifecycle, ...]:
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
            return tuple(self._observability_lifecycle(row) for row in active_rows)

    async def list_observability_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ObservabilityAlertLifecycleStatus | None = None,
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
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._observability_lifecycle(row) for row in rows)

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
