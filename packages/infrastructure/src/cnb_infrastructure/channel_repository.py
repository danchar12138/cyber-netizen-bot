"""渠道实例、诊断事件与数据库限流仓储实现。"""

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import TypedDict, cast
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_domain import (
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
    AuditLog,
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
        aggregates: dict[tuple[UUID, str], tuple[int, datetime]] = {}
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
            count, latest = aggregates.get(key, (0, event.occurred_at))
            aggregates[key] = (count + 1, max(latest, event.occurred_at))
        return tuple(
            ChannelErrorMetrics(
                channel_id=key[0],
                error_code=key[1],
                occurrences=value[0],
                last_occurred_at=value[1],
            )
            for key, value in sorted(
                aggregates.items(),
                key=lambda item: (item[1][1], str(item[0][0]), item[0][1]),
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
        # Keep the aggregate query narrow and construct zero rows for quiet channels.
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
                last_occurred_at=row[3],
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
