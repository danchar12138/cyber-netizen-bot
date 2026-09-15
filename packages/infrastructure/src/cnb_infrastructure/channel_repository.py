"""渠道实例、诊断事件与数据库限流仓储实现。"""

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import TypedDict, cast
from uuid import UUID, uuid4

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_domain import (
    AlertSeverity,
    ChannelAlert,
    ChannelAlertDisposition,
    ChannelAlertDispositionStatus,
    ChannelAlertLifecycle,
    ChannelAlertLifecycleStatus,
    ChannelDiagnosticEvent,
    ChannelErrorMetrics,
    ChannelEventDirection,
    ChannelEventStatus,
    ChannelHealthSnapshot,
    ChannelHealthStatus,
    ChannelInstance,
    ChannelInstanceStatus,
    ChannelOperationMetrics,
    ChannelPlatform,
    JsonValue,
)
from cnb_infrastructure.models import (
    Agent,
    AuditLog,
    ChannelAlertDispositionModel,
    ChannelAlertLifecycleModel,
    ChannelDiagnosticEventModel,
    ChannelHealthSnapshotModel,
    ChannelInstanceModel,
    ChannelRateLimitWindowModel,
)


class _OperationMetricCounts(TypedDict):
    inbound_events: int
    outbound_events: int
    outbound_delivered: int
    outbound_degraded: int
    outbound_failed: int
    outbound_rate_limited: int
    last_failure_at: datetime | None


_OperationAggregateRow = tuple[
    UUID,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
    datetime | None,
]


class MemoryChannelRepository:
    """测试与本地无基础设施模式使用的并发安全渠道仓储。"""

    def __init__(self) -> None:
        self.instances: dict[UUID, ChannelInstance] = {}
        self.events: dict[UUID, ChannelDiagnosticEvent] = {}
        self.health_snapshots: dict[UUID, ChannelHealthSnapshot] = {}
        self.alert_dispositions: dict[str, ChannelAlertDisposition] = {}
        self.alert_lifecycles: dict[UUID, ChannelAlertLifecycle] = {}
        self.rate_windows: dict[tuple[UUID, datetime], int] = {}
        self._lock = asyncio.Lock()

    async def create_instance(self, instance: ChannelInstance) -> ChannelInstance:
        async with self._lock:
            if any(
                item.tenant_id == instance.tenant_id
                and item.agent_id == instance.agent_id
                and item.name.casefold() == instance.name.casefold()
                for item in self.instances.values()
            ):
                raise ValueError(f"渠道名称已存在：{instance.name}")
            self.instances[instance.id] = instance
            return instance

    async def get_instance(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
    ) -> ChannelInstance | None:
        item = self.instances.get(channel_id)
        return (
            item
            if item is not None and item.tenant_id == tenant_id and item.agent_id == agent_id
            else None
        )

    async def get_instance_by_id(self, *, channel_id: UUID) -> ChannelInstance | None:
        return self.instances.get(channel_id)

    async def list_instances(
        self, *, tenant_id: UUID, agent_id: UUID
    ) -> tuple[ChannelInstance, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self.instances.values()
                    if item.tenant_id == tenant_id and item.agent_id == agent_id
                ),
                key=lambda item: (item.created_at, str(item.id)),
            )
        )

    async def list_probe_candidates(self) -> tuple[ChannelInstance, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self.instances.values()
                    if item.status is ChannelInstanceStatus.ENABLED
                ),
                key=lambda item: (item.updated_at, str(item.id)),
            )
        )

    async def update_instance(
        self,
        *,
        instance: ChannelInstance,
        actor_id: UUID,
        action: str,
    ) -> ChannelInstance:
        del actor_id, action
        async with self._lock:
            current = self.instances.get(instance.id)
            if (
                current is None
                or current.tenant_id != instance.tenant_id
                or current.agent_id != instance.agent_id
            ):
                raise LookupError(f"渠道实例不存在：{instance.id}")
            if any(
                item.id != instance.id
                and item.tenant_id == instance.tenant_id
                and item.agent_id == instance.agent_id
                and item.name.casefold() == instance.name.casefold()
                for item in self.instances.values()
            ):
                raise ValueError(f"渠道名称已存在：{instance.name}")
            self.instances[instance.id] = instance
            return instance

    async def record_event(
        self,
        event: ChannelDiagnosticEvent,
    ) -> ChannelDiagnosticEvent:
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self.events.values()
                    if item.channel_id == event.channel_id
                    and item.direction is event.direction
                    and item.idempotency_key == event.idempotency_key
                ),
                None,
            )
            if existing is not None:
                if (
                    existing.status is ChannelEventStatus.ACCEPTED
                    and event.status is not ChannelEventStatus.ACCEPTED
                ) or (
                    existing.status
                    not in {ChannelEventStatus.DELIVERED, ChannelEventStatus.DEGRADED}
                    and event.status in {ChannelEventStatus.DELIVERED, ChannelEventStatus.DEGRADED}
                ):
                    updated = replace(event, id=existing.id)
                    self.events[existing.id] = updated
                    return updated
                return existing
            self.events[event.id] = event
            return event

    async def reserve_delivery(self, event: ChannelDiagnosticEvent) -> bool:
        """原子占用出站幂等键；失败终态允许显式重试。"""
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self.events.values()
                    if item.channel_id == event.channel_id
                    and item.direction is event.direction
                    and item.idempotency_key == event.idempotency_key
                ),
                None,
            )
            if existing is None:
                self.events[event.id] = event
                return True
            if existing.status in {ChannelEventStatus.DELIVERED, ChannelEventStatus.DEGRADED}:
                return False
            if existing.status is ChannelEventStatus.ACCEPTED:
                return False
            self.events[existing.id] = replace(event, id=existing.id)
            return True

    async def get_event_by_idempotency(
        self,
        *,
        tenant_id: UUID,
        channel_id: UUID,
        direction: ChannelEventDirection,
        idempotency_key: str,
    ) -> ChannelDiagnosticEvent | None:
        return next(
            (
                item
                for item in self.events.values()
                if item.tenant_id == tenant_id
                and item.channel_id == channel_id
                and item.direction is direction
                and item.idempotency_key == idempotency_key
            ),
            None,
        )

    async def list_events(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        event_type: str | None,
        limit: int,
    ) -> tuple[ChannelDiagnosticEvent, ...]:
        visible_channel_ids = {
            item.id
            for item in self.instances.values()
            if item.tenant_id == tenant_id and item.agent_id == agent_id
        }
        return tuple(
            sorted(
                (
                    item
                    for item in self.events.values()
                    if item.tenant_id == tenant_id
                    and item.channel_id in visible_channel_ids
                    and (channel_id is None or item.channel_id == channel_id)
                    and (event_type is None or item.event_type == event_type)
                ),
                key=lambda item: (item.occurred_at, str(item.id)),
                reverse=True,
            )[:limit]
        )

    async def get_operation_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> tuple[ChannelOperationMetrics, ...]:
        visible_channels = tuple(
            item
            for item in self.instances.values()
            if item.tenant_id == tenant_id
            and item.agent_id == agent_id
            and (channel_id is None or item.id == channel_id)
        )
        by_channel: dict[UUID, _OperationMetricCounts] = {
            item.id: {
                "inbound_events": 0,
                "outbound_events": 0,
                "outbound_delivered": 0,
                "outbound_degraded": 0,
                "outbound_failed": 0,
                "outbound_rate_limited": 0,
                "last_failure_at": None,
            }
            for item in visible_channels
        }
        for event in self.events.values():
            if (
                event.channel_id not in by_channel
                or not window_started_at <= event.occurred_at <= window_ended_at
            ):
                continue
            values = by_channel[event.channel_id]
            if event.direction is ChannelEventDirection.INBOUND:
                values["inbound_events"] = int(values["inbound_events"]) + 1
            elif event.direction is ChannelEventDirection.OUTBOUND:
                values["outbound_events"] = int(values["outbound_events"]) + 1
                if event.status is ChannelEventStatus.DELIVERED:
                    values["outbound_delivered"] = int(values["outbound_delivered"]) + 1
                elif event.status is ChannelEventStatus.DEGRADED:
                    values["outbound_degraded"] = int(values["outbound_degraded"]) + 1
                elif event.status is ChannelEventStatus.RATE_LIMITED:
                    values["outbound_rate_limited"] = int(values["outbound_rate_limited"]) + 1
                elif event.status in {
                    ChannelEventStatus.FAILED,
                    ChannelEventStatus.REJECTED,
                }:
                    values["outbound_failed"] = int(values["outbound_failed"]) + 1
                if event.status in {
                    ChannelEventStatus.FAILED,
                    ChannelEventStatus.REJECTED,
                    ChannelEventStatus.RATE_LIMITED,
                }:
                    previous = values["last_failure_at"]
                    if previous is None or event.occurred_at > previous:
                        values["last_failure_at"] = event.occurred_at
        return tuple(
            ChannelOperationMetrics(
                channel_id=item.id,
                window_started_at=window_started_at,
                window_ended_at=window_ended_at,
                inbound_events=values["inbound_events"],
                outbound_events=values["outbound_events"],
                outbound_delivered=values["outbound_delivered"],
                outbound_degraded=values["outbound_degraded"],
                outbound_failed=values["outbound_failed"],
                outbound_rate_limited=values["outbound_rate_limited"],
                last_failure_at=values["last_failure_at"],
            )
            for item in sorted(
                visible_channels, key=lambda value: (value.created_at, str(value.id))
            )
            for values in (by_channel[item.id],)
        )

    async def get_error_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> tuple[ChannelErrorMetrics, ...]:
        visible_channels = {
            item.id
            for item in self.instances.values()
            if item.tenant_id == tenant_id
            and item.agent_id == agent_id
            and (channel_id is None or item.id == channel_id)
        }
        aggregates: dict[tuple[UUID, str], tuple[int, datetime, datetime]] = {}
        for event in self.events.values():
            if (
                event.channel_id not in visible_channels
                or event.direction is not ChannelEventDirection.OUTBOUND
                or event.status
                not in {
                    ChannelEventStatus.FAILED,
                    ChannelEventStatus.REJECTED,
                    ChannelEventStatus.RATE_LIMITED,
                }
                or event.error_code is None
                or not window_started_at <= event.occurred_at <= window_ended_at
            ):
                continue
            key = (event.channel_id, event.error_code)
            count, earliest, latest = aggregates.get(key, (0, event.occurred_at, event.occurred_at))
            aggregates[key] = (
                count + 1,
                min(earliest, event.occurred_at),
                max(latest, event.occurred_at),
            )
        return tuple(
            ChannelErrorMetrics(
                channel_id=key[0],
                error_code=key[1],
                occurrences=value[0],
                first_occurred_at=value[1],
                last_occurred_at=value[2],
            )
            for key, value in sorted(
                aggregates.items(),
                key=lambda item: (item[1][2], str(item[0][0]), item[0][1]),
                reverse=True,
            )
        )

    async def record_health_snapshot(
        self, snapshot: ChannelHealthSnapshot
    ) -> ChannelHealthSnapshot:
        async with self._lock:
            instance = self.instances.get(snapshot.channel_id)
            if (
                instance is None
                or instance.tenant_id != snapshot.tenant_id
                or instance.agent_id != snapshot.agent_id
            ):
                raise LookupError(f"渠道实例不存在：{snapshot.channel_id}")
            self.health_snapshots[snapshot.id] = snapshot
            return snapshot

    async def list_health_snapshots(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_started_at: datetime,
        window_ended_at: datetime,
        limit: int,
    ) -> tuple[ChannelHealthSnapshot, ...]:
        visible_channels = {
            item.id
            for item in self.instances.values()
            if item.tenant_id == tenant_id
            and item.agent_id == agent_id
            and (channel_id is None or item.id == channel_id)
        }
        rows = [
            item
            for item in self.health_snapshots.values()
            if item.channel_id in visible_channels
            and window_started_at <= item.sampled_at <= window_ended_at
        ]
        rows.sort(key=lambda item: (item.sampled_at, str(item.id)), reverse=True)
        return tuple(rows[:limit])

    async def reserve_rate_limit(
        self,
        *,
        tenant_id: UUID,
        channel_id: UUID,
        limit: int,
        now: datetime,
    ) -> bool:
        del tenant_id
        window = now.replace(second=0, microsecond=0)
        key = (channel_id, window)
        async with self._lock:
            used = self.rate_windows.get(key, 0)
            if used >= limit:
                return False
            self.rate_windows[key] = used + 1
            return True

    async def get_alert_disposition(
        self, *, tenant_id: UUID, agent_id: UUID, alert_key: str
    ) -> ChannelAlertDisposition | None:
        item = self.alert_dispositions.get(alert_key)
        return (
            item
            if item is not None and item.tenant_id == tenant_id and item.agent_id == agent_id
            else None
        )

    async def list_alert_dispositions(
        self, *, tenant_id: UUID, agent_id: UUID
    ) -> tuple[ChannelAlertDisposition, ...]:
        return tuple(
            item
            for item in self.alert_dispositions.values()
            if item.tenant_id == tenant_id and item.agent_id == agent_id
        )

    async def save_alert_disposition(
        self, disposition: ChannelAlertDisposition
    ) -> ChannelAlertDisposition:
        async with self._lock:
            channel = self.instances.get(disposition.channel_id)
            if (
                channel is None
                or channel.tenant_id != disposition.tenant_id
                or channel.agent_id != disposition.agent_id
            ):
                raise LookupError(f"渠道实例不存在：{disposition.channel_id}")
            self.alert_dispositions[disposition.alert_key] = disposition
            return disposition

    async def clear_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alert_key: str,
        actor_id: UUID | None = None,
    ) -> None:
        del actor_id
        async with self._lock:
            item = self.alert_dispositions.get(alert_key)
            if item is not None and item.tenant_id == tenant_id and item.agent_id == agent_id:
                del self.alert_dispositions[alert_key]

    async def reconcile_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alerts: tuple[ChannelAlert, ...],
        observed_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        """在内存锁内原子开启、更新和恢复当前 Agent 的告警事件。"""
        async with self._lock:
            current = {
                item.alert_key: item
                for item in self.alert_lifecycles.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and item.status is ChannelAlertLifecycleStatus.ACTIVE
            }
            active_keys = {item.alert_key for item in alerts}
            for alert in alerts:
                existing = current.get(alert.alert_key)
                first_occurred_at = min(alert.first_occurred_at, observed_at)
                if existing is None:
                    lifecycle = ChannelAlertLifecycle(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        channel_id=alert.channel_id,
                        alert_key=alert.alert_key,
                        code=alert.code,
                        error_code=alert.error_code,
                        status=ChannelAlertLifecycleStatus.ACTIVE,
                        severity=alert.severity,
                        occurrences=alert.occurrences,
                        current_value=alert.current_value,
                        threshold_value=alert.threshold_value,
                        unit=alert.unit,
                        first_occurred_at=first_occurred_at,
                        last_occurred_at=alert.last_occurred_at,
                        last_evaluated_at=observed_at,
                        escalated_at=None,
                        resolved_at=None,
                        recovery_duration_seconds=None,
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                else:
                    lifecycle = replace(
                        existing,
                        severity=alert.severity,
                        occurrences=max(existing.occurrences, alert.occurrences),
                        current_value=alert.current_value,
                        threshold_value=alert.threshold_value,
                        unit=alert.unit,
                        first_occurred_at=min(existing.first_occurred_at, first_occurred_at),
                        last_occurred_at=max(existing.last_occurred_at, alert.last_occurred_at),
                        last_evaluated_at=observed_at,
                        updated_at=observed_at,
                    )
                self.alert_lifecycles[lifecycle.id] = lifecycle
            for lifecycle in current.values():
                if lifecycle.alert_key in active_keys:
                    continue
                self.alert_lifecycles[lifecycle.id] = replace(
                    lifecycle,
                    status=ChannelAlertLifecycleStatus.RESOLVED,
                    last_evaluated_at=observed_at,
                    resolved_at=observed_at,
                    recovery_duration_seconds=max(
                        0, int((observed_at - lifecycle.first_occurred_at).total_seconds())
                    ),
                    updated_at=observed_at,
                )
            return tuple(
                sorted(
                    (
                        item
                        for item in self.alert_lifecycles.values()
                        if item.tenant_id == tenant_id
                        and item.agent_id == agent_id
                        and item.status is ChannelAlertLifecycleStatus.ACTIVE
                    ),
                    key=lambda item: (item.first_occurred_at, str(item.id)),
                    reverse=True,
                )
            )

    async def mark_alert_lifecycles_escalated(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        escalation_level: int,
        escalated_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        """只在可靠通知已入队后，为仍活动的事件原子推进一级。"""
        async with self._lock:
            marked: list[ChannelAlertLifecycle] = []
            for lifecycle_id in lifecycle_ids:
                item = self.alert_lifecycles.get(lifecycle_id)
                if (
                    item is None
                    or item.tenant_id != tenant_id
                    or item.agent_id != agent_id
                    or item.status is not ChannelAlertLifecycleStatus.ACTIVE
                    or item.escalation_level != escalation_level - 1
                ):
                    continue
                updated = replace(
                    item,
                    escalated_at=item.escalated_at or escalated_at,
                    escalation_level=escalation_level,
                    last_escalated_at=escalated_at,
                    updated_at=escalated_at,
                )
                self.alert_lifecycles[lifecycle_id] = updated
                marked.append(updated)
            return tuple(marked)

    async def list_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ChannelAlertLifecycleStatus | None,
        severity: AlertSeverity | None,
        limit: int,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        rows = [
            item
            for item in self.alert_lifecycles.values()
            if item.tenant_id == tenant_id
            and item.agent_id == agent_id
            and (channel_id is None or item.channel_id == channel_id)
            and (status is None or item.status is status)
            and (severity is None or item.severity is severity)
        ]
        rows.sort(key=lambda item: (item.first_occurred_at, str(item.id)), reverse=True)
        return tuple(rows[:limit])

    async def list_alert_lifecycles_in_window(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        rows = [
            item
            for item in self.alert_lifecycles.values()
            if item.tenant_id == tenant_id
            and item.agent_id == agent_id
            and (
                item.status is ChannelAlertLifecycleStatus.ACTIVE
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
        rows.sort(key=lambda item: (item.first_occurred_at, str(item.id)), reverse=True)
        return tuple(rows)


class SqlAlchemyChannelRepository:
    """使用 PostgreSQL 唯一键、事务和原子 UPSERT 的渠道仓储。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_instance(self, instance: ChannelInstance) -> ChannelInstance:
        try:
            async with self._session_factory.begin() as session:
                session.add(self._instance_model(instance))
                self._audit(
                    session,
                    instance=instance,
                    actor_id=instance.created_by,
                    action="channel_instance.created",
                )
        except IntegrityError as error:
            raise ValueError(f"渠道名称已存在：{instance.name}") from error
        return instance

    async def get_instance(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
    ) -> ChannelInstance | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ChannelInstanceModel).where(
                    ChannelInstanceModel.id == channel_id,
                    ChannelInstanceModel.tenant_id == tenant_id,
                    ChannelInstanceModel.agent_id == agent_id,
                )
            )
        return self._instance(row) if row is not None else None

    async def get_instance_by_id(self, *, channel_id: UUID) -> ChannelInstance | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ChannelInstanceModel).where(ChannelInstanceModel.id == channel_id)
            )
        return self._instance(row) if row is not None else None

    async def list_instances(
        self, *, tenant_id: UUID, agent_id: UUID
    ) -> tuple[ChannelInstance, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ChannelInstanceModel)
                    .where(
                        ChannelInstanceModel.tenant_id == tenant_id,
                        ChannelInstanceModel.agent_id == agent_id,
                    )
                    .order_by(ChannelInstanceModel.created_at, ChannelInstanceModel.id)
                )
            ).all()
        return tuple(self._instance(row) for row in rows)

    async def list_probe_candidates(self) -> tuple[ChannelInstance, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ChannelInstanceModel)
                    .where(ChannelInstanceModel.status == ChannelInstanceStatus.ENABLED.value)
                    .order_by(ChannelInstanceModel.updated_at, ChannelInstanceModel.id)
                )
            ).all()
        return tuple(self._instance(row) for row in rows)

    async def update_instance(
        self,
        *,
        instance: ChannelInstance,
        actor_id: UUID,
        action: str,
    ) -> ChannelInstance:
        try:
            async with self._session_factory.begin() as session:
                row = await session.scalar(
                    select(ChannelInstanceModel)
                    .where(
                        ChannelInstanceModel.id == instance.id,
                        ChannelInstanceModel.tenant_id == instance.tenant_id,
                        ChannelInstanceModel.agent_id == instance.agent_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise LookupError(f"渠道实例不存在：{instance.id}")
                row.name = instance.name
                row.status = instance.status.value
                row.rate_limit_per_minute = instance.rate_limit_per_minute
                row.settings = instance.settings
                row.health_status = instance.health_status.value
                row.health_detail = instance.health_detail
                row.last_checked_at = instance.last_checked_at
                row.updated_at = instance.updated_at
                self._audit(session, instance=instance, actor_id=actor_id, action=action)
        except IntegrityError as error:
            raise ValueError(f"渠道名称已存在：{instance.name}") from error
        return instance

    async def record_event(
        self,
        event: ChannelDiagnosticEvent,
    ) -> ChannelDiagnosticEvent:
        try:
            async with self._session_factory.begin() as session:
                existing = await session.scalar(
                    select(ChannelDiagnosticEventModel)
                    .where(
                        ChannelDiagnosticEventModel.channel_id == event.channel_id,
                        ChannelDiagnosticEventModel.direction == event.direction.value,
                        ChannelDiagnosticEventModel.idempotency_key == event.idempotency_key,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    if (
                        existing.status == ChannelEventStatus.ACCEPTED.value
                        and event.status is not ChannelEventStatus.ACCEPTED
                    ) or (
                        existing.status
                        not in {
                            ChannelEventStatus.DELIVERED.value,
                            ChannelEventStatus.DEGRADED.value,
                        }
                        and event.status
                        in {ChannelEventStatus.DELIVERED, ChannelEventStatus.DEGRADED}
                    ):
                        existing.event_type = event.event_type
                        existing.status = event.status.value
                        existing.external_message_id = event.external_message_id
                        existing.payload_summary = event.payload_summary
                        existing.error_code = event.error_code
                        existing.degradations = list(event.degradations)
                        existing.occurred_at = event.occurred_at
                        return replace(event, id=existing.id)
                    return self._event(existing)
                session.add(self._event_model(event))
        except IntegrityError:
            existing = await self.get_event_by_idempotency(
                tenant_id=event.tenant_id,
                channel_id=event.channel_id,
                direction=event.direction,
                idempotency_key=event.idempotency_key,
            )
            if existing is None:
                raise
            return existing
        return event

    async def reserve_delivery(self, event: ChannelDiagnosticEvent) -> bool:
        """用数据库唯一键和行锁原子占用出站幂等键。"""
        try:
            async with self._session_factory.begin() as session:
                existing = await session.scalar(
                    select(ChannelDiagnosticEventModel)
                    .where(
                        ChannelDiagnosticEventModel.channel_id == event.channel_id,
                        ChannelDiagnosticEventModel.direction == event.direction.value,
                        ChannelDiagnosticEventModel.idempotency_key == event.idempotency_key,
                    )
                    .with_for_update()
                )
                if existing is None:
                    session.add(self._event_model(event))
                    return True
                if existing.status in {
                    ChannelEventStatus.DELIVERED.value,
                    ChannelEventStatus.DEGRADED.value,
                    ChannelEventStatus.ACCEPTED.value,
                }:
                    return False
                existing.event_type = event.event_type
                existing.status = event.status.value
                existing.external_message_id = None
                existing.payload_summary = event.payload_summary
                existing.error_code = None
                existing.degradations = list(event.degradations)
                existing.occurred_at = event.occurred_at
                return True
        except IntegrityError:
            return False

    async def get_event_by_idempotency(
        self,
        *,
        tenant_id: UUID,
        channel_id: UUID,
        direction: ChannelEventDirection,
        idempotency_key: str,
    ) -> ChannelDiagnosticEvent | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ChannelDiagnosticEventModel).where(
                    ChannelDiagnosticEventModel.tenant_id == tenant_id,
                    ChannelDiagnosticEventModel.channel_id == channel_id,
                    ChannelDiagnosticEventModel.direction == direction.value,
                    ChannelDiagnosticEventModel.idempotency_key == idempotency_key,
                )
            )
        return self._event(row) if row is not None else None

    async def list_events(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        event_type: str | None,
        limit: int,
    ) -> tuple[ChannelDiagnosticEvent, ...]:
        statement = (
            select(ChannelDiagnosticEventModel)
            .join(
                ChannelInstanceModel,
                ChannelInstanceModel.id == ChannelDiagnosticEventModel.channel_id,
            )
            .where(
                ChannelDiagnosticEventModel.tenant_id == tenant_id,
                ChannelInstanceModel.tenant_id == tenant_id,
                ChannelInstanceModel.agent_id == agent_id,
            )
        )
        if channel_id is not None:
            statement = statement.where(ChannelDiagnosticEventModel.channel_id == channel_id)
        if event_type is not None:
            statement = statement.where(ChannelDiagnosticEventModel.event_type == event_type)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(
                        ChannelDiagnosticEventModel.occurred_at.desc(),
                        ChannelDiagnosticEventModel.id.desc(),
                    ).limit(limit)
                )
            ).all()
        return tuple(self._event(row) for row in rows)

    async def get_operation_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> tuple[ChannelOperationMetrics, ...]:
        channel_statement = select(ChannelInstanceModel).where(
            ChannelInstanceModel.tenant_id == tenant_id,
            ChannelInstanceModel.agent_id == agent_id,
        )
        if channel_id is not None:
            channel_statement = channel_statement.where(ChannelInstanceModel.id == channel_id)
        aggregate_statement = (
            select(
                ChannelDiagnosticEventModel.channel_id,
                func.sum(
                    case(
                        (
                            ChannelDiagnosticEventModel.direction
                            == ChannelEventDirection.INBOUND.value,
                            1,
                        ),
                        else_=0,
                    )
                ).label("inbound_events"),
                func.sum(
                    case(
                        (
                            ChannelDiagnosticEventModel.direction
                            == ChannelEventDirection.OUTBOUND.value,
                            1,
                        ),
                        else_=0,
                    )
                ).label("outbound_events"),
                func.sum(
                    case(
                        (
                            (
                                ChannelDiagnosticEventModel.direction
                                == ChannelEventDirection.OUTBOUND.value
                            )
                            & (
                                ChannelDiagnosticEventModel.status
                                == ChannelEventStatus.DELIVERED.value
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("outbound_delivered"),
                func.sum(
                    case(
                        (
                            (
                                ChannelDiagnosticEventModel.direction
                                == ChannelEventDirection.OUTBOUND.value
                            )
                            & (
                                ChannelDiagnosticEventModel.status
                                == ChannelEventStatus.DEGRADED.value
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("outbound_degraded"),
                func.sum(
                    case(
                        (
                            (
                                ChannelDiagnosticEventModel.direction
                                == ChannelEventDirection.OUTBOUND.value
                            )
                            & ChannelDiagnosticEventModel.status.in_(
                                [
                                    ChannelEventStatus.FAILED.value,
                                    ChannelEventStatus.REJECTED.value,
                                ]
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("outbound_failed"),
                func.sum(
                    case(
                        (
                            (
                                ChannelDiagnosticEventModel.direction
                                == ChannelEventDirection.OUTBOUND.value
                            )
                            & (
                                ChannelDiagnosticEventModel.status
                                == ChannelEventStatus.RATE_LIMITED.value
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("outbound_rate_limited"),
                func.max(
                    case(
                        (
                            (
                                ChannelDiagnosticEventModel.direction
                                == ChannelEventDirection.OUTBOUND.value
                            )
                            & ChannelDiagnosticEventModel.status.in_(
                                [
                                    ChannelEventStatus.FAILED.value,
                                    ChannelEventStatus.REJECTED.value,
                                    ChannelEventStatus.RATE_LIMITED.value,
                                ]
                            ),
                            ChannelDiagnosticEventModel.occurred_at,
                        ),
                        else_=None,
                    )
                ).label("last_failure_at"),
            )
            .join(
                ChannelInstanceModel,
                ChannelInstanceModel.id == ChannelDiagnosticEventModel.channel_id,
            )
            .where(
                ChannelDiagnosticEventModel.tenant_id == tenant_id,
                ChannelInstanceModel.tenant_id == tenant_id,
                ChannelInstanceModel.agent_id == agent_id,
                ChannelDiagnosticEventModel.occurred_at >= window_started_at,
                ChannelDiagnosticEventModel.occurred_at <= window_ended_at,
            )
            .group_by(ChannelDiagnosticEventModel.channel_id)
        )
        if channel_id is not None:
            aggregate_statement = aggregate_statement.where(
                ChannelDiagnosticEventModel.channel_id == channel_id
            )
        async with self._session_factory() as session:
            channels = (await session.scalars(channel_statement)).all()
            rows = cast(
                tuple[_OperationAggregateRow, ...],
                (await session.execute(aggregate_statement)).all(),
            )
        # 聚合查询只读取必要字段；没有事件的安静渠道由应用层补齐零值行。
        aggregates = {row[0]: row for row in rows}
        return tuple(
            ChannelOperationMetrics(
                channel_id=channel.id,
                window_started_at=window_started_at,
                window_ended_at=window_ended_at,
                inbound_events=aggregates.get(channel.id, (channel.id, 0, 0, 0, 0, 0, 0, None))[1]
                or 0,
                outbound_events=aggregates.get(channel.id, (channel.id, 0, 0, 0, 0, 0, 0, None))[2]
                or 0,
                outbound_delivered=aggregates.get(channel.id, (channel.id, 0, 0, 0, 0, 0, 0, None))[
                    3
                ]
                or 0,
                outbound_degraded=aggregates.get(channel.id, (channel.id, 0, 0, 0, 0, 0, 0, None))[
                    4
                ]
                or 0,
                outbound_failed=aggregates.get(channel.id, (channel.id, 0, 0, 0, 0, 0, 0, None))[5]
                or 0,
                outbound_rate_limited=aggregates.get(
                    channel.id, (channel.id, 0, 0, 0, 0, 0, 0, None)
                )[6]
                or 0,
                last_failure_at=aggregates.get(channel.id, (channel.id, 0, 0, 0, 0, 0, 0, None))[7],
            )
            for channel in channels
        )

    async def get_error_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> tuple[ChannelErrorMetrics, ...]:
        statement = (
            select(
                ChannelDiagnosticEventModel.channel_id,
                ChannelDiagnosticEventModel.error_code,
                func.count().label("occurrences"),
                func.min(ChannelDiagnosticEventModel.occurred_at).label("first_occurred_at"),
                func.max(ChannelDiagnosticEventModel.occurred_at).label("last_occurred_at"),
            )
            .join(
                ChannelInstanceModel,
                ChannelInstanceModel.id == ChannelDiagnosticEventModel.channel_id,
            )
            .where(
                ChannelDiagnosticEventModel.tenant_id == tenant_id,
                ChannelInstanceModel.tenant_id == tenant_id,
                ChannelInstanceModel.agent_id == agent_id,
                ChannelDiagnosticEventModel.direction == ChannelEventDirection.OUTBOUND.value,
                ChannelDiagnosticEventModel.status.in_(
                    [
                        ChannelEventStatus.FAILED.value,
                        ChannelEventStatus.REJECTED.value,
                        ChannelEventStatus.RATE_LIMITED.value,
                    ]
                ),
                ChannelDiagnosticEventModel.error_code.is_not(None),
                ChannelDiagnosticEventModel.occurred_at >= window_started_at,
                ChannelDiagnosticEventModel.occurred_at <= window_ended_at,
            )
            .group_by(
                ChannelDiagnosticEventModel.channel_id,
                ChannelDiagnosticEventModel.error_code,
            )
            .order_by(
                func.max(ChannelDiagnosticEventModel.occurred_at).desc(),
                ChannelDiagnosticEventModel.channel_id,
                ChannelDiagnosticEventModel.error_code,
            )
        )
        if channel_id is not None:
            statement = statement.where(ChannelDiagnosticEventModel.channel_id == channel_id)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return tuple(
            ChannelErrorMetrics(
                channel_id=row[0],
                error_code=row[1],
                occurrences=int(row[2]),
                first_occurred_at=row[3],
                last_occurred_at=row[4],
            )
            for row in rows
            if row[1] is not None
        )

    async def record_health_snapshot(
        self, snapshot: ChannelHealthSnapshot
    ) -> ChannelHealthSnapshot:
        async with self._session_factory.begin() as session:
            channel = await session.scalar(
                select(ChannelInstanceModel).where(
                    ChannelInstanceModel.id == snapshot.channel_id,
                    ChannelInstanceModel.tenant_id == snapshot.tenant_id,
                    ChannelInstanceModel.agent_id == snapshot.agent_id,
                )
            )
            if channel is None:
                raise LookupError(f"渠道实例不存在：{snapshot.channel_id}")
            session.add(self._snapshot_model(snapshot))
        return snapshot

    async def list_health_snapshots(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_started_at: datetime,
        window_ended_at: datetime,
        limit: int,
    ) -> tuple[ChannelHealthSnapshot, ...]:
        statement = (
            select(ChannelHealthSnapshotModel)
            .join(
                ChannelInstanceModel,
                ChannelInstanceModel.id == ChannelHealthSnapshotModel.channel_id,
            )
            .where(
                ChannelHealthSnapshotModel.tenant_id == tenant_id,
                ChannelInstanceModel.tenant_id == tenant_id,
                ChannelInstanceModel.agent_id == agent_id,
                ChannelHealthSnapshotModel.sampled_at >= window_started_at,
                ChannelHealthSnapshotModel.sampled_at <= window_ended_at,
            )
            .order_by(
                ChannelHealthSnapshotModel.sampled_at.desc(),
                ChannelHealthSnapshotModel.id.desc(),
            )
            .limit(limit)
        )
        if channel_id is not None:
            statement = statement.where(ChannelHealthSnapshotModel.channel_id == channel_id)
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._snapshot(row) for row in rows)

    async def reserve_rate_limit(
        self,
        *,
        tenant_id: UUID,
        channel_id: UUID,
        limit: int,
        now: datetime,
    ) -> bool:
        window = now.replace(second=0, microsecond=0)
        statement = (
            pg_insert(ChannelRateLimitWindowModel)
            .values(
                tenant_id=tenant_id,
                channel_id=channel_id,
                window_started_at=window,
                used_count=1,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[
                    ChannelRateLimitWindowModel.tenant_id,
                    ChannelRateLimitWindowModel.channel_id,
                    ChannelRateLimitWindowModel.window_started_at,
                ],
                set_={
                    "used_count": ChannelRateLimitWindowModel.used_count + 1,
                    "updated_at": now,
                },
                where=ChannelRateLimitWindowModel.used_count < limit,
            )
            .returning(ChannelRateLimitWindowModel.used_count)
        )
        async with self._session_factory.begin() as session:
            used = await session.scalar(statement)
        return used is not None

    async def get_alert_disposition(
        self, *, tenant_id: UUID, agent_id: UUID, alert_key: str
    ) -> ChannelAlertDisposition | None:
        statement = select(ChannelAlertDispositionModel).where(
            ChannelAlertDispositionModel.tenant_id == tenant_id,
            ChannelAlertDispositionModel.agent_id == agent_id,
            ChannelAlertDispositionModel.alert_key == alert_key,
        )
        async with self._session_factory() as session:
            row = await session.scalar(statement)
        return None if row is None else self._disposition(row)

    async def list_alert_dispositions(
        self, *, tenant_id: UUID, agent_id: UUID
    ) -> tuple[ChannelAlertDisposition, ...]:
        statement = (
            select(ChannelAlertDispositionModel)
            .where(
                ChannelAlertDispositionModel.tenant_id == tenant_id,
                ChannelAlertDispositionModel.agent_id == agent_id,
            )
            .order_by(ChannelAlertDispositionModel.updated_at.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._disposition(row) for row in rows)

    async def save_alert_disposition(
        self, disposition: ChannelAlertDisposition
    ) -> ChannelAlertDisposition:
        async with self._session_factory.begin() as session:
            channel = await session.scalar(
                select(ChannelInstanceModel).where(
                    ChannelInstanceModel.id == disposition.channel_id,
                    ChannelInstanceModel.tenant_id == disposition.tenant_id,
                    ChannelInstanceModel.agent_id == disposition.agent_id,
                )
            )
            if channel is None:
                raise LookupError(f"渠道实例不存在：{disposition.channel_id}")
            row = await session.scalar(
                select(ChannelAlertDispositionModel)
                .where(
                    ChannelAlertDispositionModel.tenant_id == disposition.tenant_id,
                    ChannelAlertDispositionModel.agent_id == disposition.agent_id,
                    ChannelAlertDispositionModel.alert_key == disposition.alert_key,
                )
                .with_for_update()
            )
            if row is None:
                row = self._disposition_model(disposition)
                session.add(row)
            else:
                row.channel_id = disposition.channel_id
                row.code = disposition.code
                row.error_code = disposition.error_code
                row.status = disposition.status.value
                row.reason = disposition.reason
                row.actor_id = disposition.actor_id
                row.expires_at = disposition.expires_at
                row.updated_at = disposition.updated_at
            session.add(
                AuditLog(
                    tenant_id=disposition.tenant_id,
                    actor_id=disposition.actor_id,
                    action=f"channel_alert.{disposition.status.value}",
                    resource_type="channel_alert_disposition",
                    resource_id=disposition.alert_key,
                    detail={
                        "agent_id": str(disposition.agent_id),
                        "channel_id": str(disposition.channel_id),
                        "code": disposition.code,
                        "error_code": disposition.error_code,
                        "status": disposition.status.value,
                        "expires_at": disposition.expires_at.isoformat()
                        if disposition.expires_at
                        else None,
                    },
                )
            )
            await session.flush()
            return self._disposition(row)

    async def clear_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alert_key: str,
        actor_id: UUID | None = None,
    ) -> None:
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(ChannelAlertDispositionModel)
                .where(
                    ChannelAlertDispositionModel.tenant_id == tenant_id,
                    ChannelAlertDispositionModel.agent_id == agent_id,
                    ChannelAlertDispositionModel.alert_key == alert_key,
                )
                .with_for_update()
            )
            if row is None:
                return
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_id=actor_id or row.actor_id,
                    action="channel_alert.disposition_cleared",
                    resource_type="channel_alert_disposition",
                    resource_id=alert_key,
                    detail={"agent_id": str(agent_id), "channel_id": str(row.channel_id)},
                )
            )
            await session.delete(row)

    async def reconcile_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alerts: tuple[ChannelAlert, ...],
        observed_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        """锁定 Agent 后原子对账，避免并发探测创建重复活动事件。"""
        async with self._session_factory.begin() as session:
            locked_agent = await session.scalar(
                select(Agent.id)
                .where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
                .with_for_update()
            )
            if locked_agent is None:
                raise LookupError(f"Agent 不存在：{agent_id}")
            rows = (
                await session.scalars(
                    select(ChannelAlertLifecycleModel)
                    .where(
                        ChannelAlertLifecycleModel.tenant_id == tenant_id,
                        ChannelAlertLifecycleModel.agent_id == agent_id,
                        ChannelAlertLifecycleModel.status
                        == ChannelAlertLifecycleStatus.ACTIVE.value,
                    )
                    .with_for_update()
                )
            ).all()
            current = {row.alert_key: row for row in rows}
            active_keys = {item.alert_key for item in alerts}
            for alert in alerts:
                row = current.get(alert.alert_key)
                first_occurred_at = min(alert.first_occurred_at, observed_at)
                if row is None:
                    row = ChannelAlertLifecycleModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        channel_id=alert.channel_id,
                        alert_key=alert.alert_key,
                        code=alert.code,
                        error_code=alert.error_code,
                        status=ChannelAlertLifecycleStatus.ACTIVE.value,
                        severity=alert.severity.value,
                        occurrences=alert.occurrences,
                        current_value=alert.current_value,
                        threshold_value=alert.threshold_value,
                        unit=alert.unit,
                        first_occurred_at=first_occurred_at,
                        last_occurred_at=alert.last_occurred_at,
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
                    row.occurrences = max(row.occurrences, alert.occurrences)
                    row.current_value = alert.current_value
                    row.threshold_value = alert.threshold_value
                    row.unit = alert.unit
                    row.first_occurred_at = min(row.first_occurred_at, first_occurred_at)
                    row.last_occurred_at = max(row.last_occurred_at, alert.last_occurred_at)
                    row.last_evaluated_at = observed_at
                    row.updated_at = observed_at
            for row in rows:
                if row.alert_key in active_keys:
                    continue
                row.status = ChannelAlertLifecycleStatus.RESOLVED.value
                row.last_evaluated_at = observed_at
                row.resolved_at = observed_at
                row.recovery_duration_seconds = max(
                    0, int((observed_at - row.first_occurred_at).total_seconds())
                )
                row.updated_at = observed_at
            await session.flush()
            active_rows = (
                await session.scalars(
                    select(ChannelAlertLifecycleModel)
                    .where(
                        ChannelAlertLifecycleModel.tenant_id == tenant_id,
                        ChannelAlertLifecycleModel.agent_id == agent_id,
                        ChannelAlertLifecycleModel.status
                        == ChannelAlertLifecycleStatus.ACTIVE.value,
                    )
                    .order_by(
                        ChannelAlertLifecycleModel.first_occurred_at.desc(),
                        ChannelAlertLifecycleModel.id.desc(),
                    )
                )
            ).all()
            return tuple(self._lifecycle(row) for row in active_rows)

    async def mark_alert_lifecycles_escalated(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        escalation_level: int,
        escalated_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        if not lifecycle_ids:
            return ()
        async with self._session_factory.begin() as session:
            rows = (
                await session.scalars(
                    select(ChannelAlertLifecycleModel)
                    .where(
                        ChannelAlertLifecycleModel.tenant_id == tenant_id,
                        ChannelAlertLifecycleModel.agent_id == agent_id,
                        ChannelAlertLifecycleModel.id.in_(lifecycle_ids),
                        ChannelAlertLifecycleModel.status
                        == ChannelAlertLifecycleStatus.ACTIVE.value,
                        ChannelAlertLifecycleModel.escalation_level == escalation_level - 1,
                    )
                    .with_for_update()
                )
            ).all()
            for row in rows:
                if row.escalated_at is None:
                    row.escalated_at = escalated_at
                row.escalation_level = escalation_level
                row.last_escalated_at = escalated_at
                row.updated_at = escalated_at
            await session.flush()
            return tuple(self._lifecycle(row) for row in rows)

    async def list_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ChannelAlertLifecycleStatus | None,
        severity: AlertSeverity | None,
        limit: int,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        statement = (
            select(ChannelAlertLifecycleModel)
            .where(
                ChannelAlertLifecycleModel.tenant_id == tenant_id,
                ChannelAlertLifecycleModel.agent_id == agent_id,
            )
            .order_by(
                ChannelAlertLifecycleModel.first_occurred_at.desc(),
                ChannelAlertLifecycleModel.id.desc(),
            )
            .limit(limit)
        )
        if channel_id is not None:
            statement = statement.where(ChannelAlertLifecycleModel.channel_id == channel_id)
        if status is not None:
            statement = statement.where(ChannelAlertLifecycleModel.status == status.value)
        if severity is not None:
            statement = statement.where(ChannelAlertLifecycleModel.severity == severity.value)
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._lifecycle(row) for row in rows)

    async def list_alert_lifecycles_in_window(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        statement = (
            select(ChannelAlertLifecycleModel)
            .where(
                ChannelAlertLifecycleModel.tenant_id == tenant_id,
                ChannelAlertLifecycleModel.agent_id == agent_id,
                or_(
                    ChannelAlertLifecycleModel.status == ChannelAlertLifecycleStatus.ACTIVE.value,
                    ChannelAlertLifecycleModel.first_occurred_at.between(
                        window_started_at, window_ended_at
                    ),
                    ChannelAlertLifecycleModel.resolved_at.between(
                        window_started_at, window_ended_at
                    ),
                    ChannelAlertLifecycleModel.escalated_at.between(
                        window_started_at, window_ended_at
                    ),
                ),
            )
            .order_by(
                ChannelAlertLifecycleModel.first_occurred_at.desc(),
                ChannelAlertLifecycleModel.id.desc(),
            )
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(self._lifecycle(row) for row in rows)

    @staticmethod
    def _instance_model(item: ChannelInstance) -> ChannelInstanceModel:
        return ChannelInstanceModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            name=item.name,
            platform=item.platform.value,
            status=item.status.value,
            rate_limit_per_minute=item.rate_limit_per_minute,
            settings=item.settings,
            health_status=item.health_status.value,
            health_detail=item.health_detail,
            last_checked_at=item.last_checked_at,
            created_by=item.created_by,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _event_model(item: ChannelDiagnosticEvent) -> ChannelDiagnosticEventModel:
        return ChannelDiagnosticEventModel(
            id=item.id,
            tenant_id=item.tenant_id,
            channel_id=item.channel_id,
            direction=item.direction.value,
            event_type=item.event_type,
            status=item.status.value,
            external_event_id=item.external_event_id,
            idempotency_key=item.idempotency_key,
            external_message_id=item.external_message_id,
            payload_summary=item.payload_summary,
            error_code=item.error_code,
            degradations=list(item.degradations),
            occurred_at=item.occurred_at,
        )

    @staticmethod
    def _snapshot_model(item: ChannelHealthSnapshot) -> ChannelHealthSnapshotModel:
        return ChannelHealthSnapshotModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            channel_id=item.channel_id,
            platform=item.platform.value,
            status=item.status.value,
            configured=item.configured,
            pending_update_count=item.pending_update_count,
            remote_error_present=item.remote_error_present,
            sampled_at=item.sampled_at,
        )

    @staticmethod
    def _disposition_model(item: ChannelAlertDisposition) -> ChannelAlertDispositionModel:
        return ChannelAlertDispositionModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            channel_id=item.channel_id,
            alert_key=item.alert_key,
            code=item.code,
            error_code=item.error_code,
            status=item.status.value,
            reason=item.reason,
            actor_id=item.actor_id,
            expires_at=item.expires_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _instance(row: ChannelInstanceModel) -> ChannelInstance:
        return ChannelInstance(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            name=row.name,
            platform=ChannelPlatform(row.platform),
            status=ChannelInstanceStatus(row.status),
            rate_limit_per_minute=row.rate_limit_per_minute,
            settings=cast(dict[str, JsonValue], row.settings),
            health_status=ChannelHealthStatus(row.health_status),
            health_detail=row.health_detail,
            last_checked_at=row.last_checked_at,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _event(row: ChannelDiagnosticEventModel) -> ChannelDiagnosticEvent:
        return ChannelDiagnosticEvent(
            id=row.id,
            tenant_id=row.tenant_id,
            channel_id=row.channel_id,
            direction=ChannelEventDirection(row.direction),
            event_type=row.event_type,
            status=ChannelEventStatus(row.status),
            external_event_id=row.external_event_id,
            idempotency_key=row.idempotency_key,
            external_message_id=row.external_message_id,
            payload_summary=cast(dict[str, JsonValue], row.payload_summary),
            error_code=row.error_code,
            degradations=tuple(row.degradations),
            occurred_at=row.occurred_at,
        )

    @staticmethod
    def _snapshot(row: ChannelHealthSnapshotModel) -> ChannelHealthSnapshot:
        return ChannelHealthSnapshot(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            channel_id=row.channel_id,
            platform=ChannelPlatform(row.platform),
            status=ChannelHealthStatus(row.status),
            configured=row.configured,
            pending_update_count=row.pending_update_count,
            remote_error_present=row.remote_error_present,
            sampled_at=row.sampled_at,
        )

    @staticmethod
    def _disposition(row: ChannelAlertDispositionModel) -> ChannelAlertDisposition:
        return ChannelAlertDisposition(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            channel_id=row.channel_id,
            alert_key=row.alert_key,
            code=row.code,
            error_code=row.error_code,
            status=ChannelAlertDispositionStatus(row.status),
            reason=row.reason,
            actor_id=row.actor_id,
            expires_at=row.expires_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _lifecycle(row: ChannelAlertLifecycleModel) -> ChannelAlertLifecycle:
        return ChannelAlertLifecycle(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            channel_id=row.channel_id,
            alert_key=row.alert_key,
            code=row.code,
            error_code=row.error_code,
            status=ChannelAlertLifecycleStatus(row.status),
            severity=AlertSeverity(row.severity),
            occurrences=row.occurrences,
            current_value=row.current_value,
            threshold_value=row.threshold_value,
            unit=row.unit,
            first_occurred_at=row.first_occurred_at,
            last_occurred_at=row.last_occurred_at,
            last_evaluated_at=row.last_evaluated_at,
            escalated_at=row.escalated_at,
            escalation_level=row.escalation_level,
            last_escalated_at=row.last_escalated_at,
            resolved_at=row.resolved_at,
            recovery_duration_seconds=row.recovery_duration_seconds,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _audit(
        session: AsyncSession,
        *,
        instance: ChannelInstance,
        actor_id: UUID,
        action: str,
    ) -> None:
        session.add(
            AuditLog(
                tenant_id=instance.tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type="channel_instance",
                resource_id=str(instance.id),
                detail={
                    "agent_id": str(instance.agent_id),
                    "platform": instance.platform.value,
                    "status": instance.status.value,
                    "rate_limit_per_minute": instance.rate_limit_per_minute,
                },
            )
        )
