"""渠道实例、诊断事件与数据库限流仓储实现。"""

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_domain import (
    ChannelDiagnosticEvent,
    ChannelEventDirection,
    ChannelEventStatus,
    ChannelHealthStatus,
    ChannelInstance,
    ChannelInstanceStatus,
    ChannelPlatform,
    JsonValue,
)
from cnb_infrastructure.models import (
    AuditLog,
    ChannelDiagnosticEventModel,
    ChannelInstanceModel,
    ChannelRateLimitWindowModel,
)


class MemoryChannelRepository:
    """测试与本地无基础设施模式使用的并发安全渠道仓储。"""

    def __init__(self) -> None:
        self.instances: dict[UUID, ChannelInstance] = {}
        self.events: dict[UUID, ChannelDiagnosticEvent] = {}
        self.rate_windows: dict[tuple[UUID, datetime], int] = {}
        self._lock = asyncio.Lock()

    async def create_instance(self, instance: ChannelInstance) -> ChannelInstance:
        async with self._lock:
            if any(
                item.tenant_id == instance.tenant_id
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
        channel_id: UUID,
    ) -> ChannelInstance | None:
        item = self.instances.get(channel_id)
        return item if item is not None and item.tenant_id == tenant_id else None

    async def list_instances(self, *, tenant_id: UUID) -> tuple[ChannelInstance, ...]:
        return tuple(
            sorted(
                (item for item in self.instances.values() if item.tenant_id == tenant_id),
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
            if current is None or current.tenant_id != instance.tenant_id:
                raise LookupError(f"渠道实例不存在：{instance.id}")
            if any(
                item.id != instance.id
                and item.tenant_id == instance.tenant_id
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
                if existing.status not in {
                    ChannelEventStatus.DELIVERED,
                    ChannelEventStatus.DEGRADED,
                    ChannelEventStatus.ACCEPTED,
                } and event.status in {
                    ChannelEventStatus.DELIVERED,
                    ChannelEventStatus.DEGRADED,
                }:
                    updated = replace(event, id=existing.id)
                    self.events[existing.id] = updated
                    return updated
                return existing
            self.events[event.id] = event
            return event

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
        channel_id: UUID | None,
        limit: int,
    ) -> tuple[ChannelDiagnosticEvent, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self.events.values()
                    if item.tenant_id == tenant_id
                    and (channel_id is None or item.channel_id == channel_id)
                ),
                key=lambda item: (item.occurred_at, str(item.id)),
                reverse=True,
            )[:limit]
        )

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
        channel_id: UUID,
    ) -> ChannelInstance | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ChannelInstanceModel).where(
                    ChannelInstanceModel.id == channel_id,
                    ChannelInstanceModel.tenant_id == tenant_id,
                )
            )
        return self._instance(row) if row is not None else None

    async def list_instances(self, *, tenant_id: UUID) -> tuple[ChannelInstance, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ChannelInstanceModel)
                    .where(ChannelInstanceModel.tenant_id == tenant_id)
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
                    if existing.status not in {
                        ChannelEventStatus.DELIVERED.value,
                        ChannelEventStatus.DEGRADED.value,
                        ChannelEventStatus.ACCEPTED.value,
                    } and event.status in {
                        ChannelEventStatus.DELIVERED,
                        ChannelEventStatus.DEGRADED,
                    }:
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
        channel_id: UUID | None,
        limit: int,
    ) -> tuple[ChannelDiagnosticEvent, ...]:
        statement = select(ChannelDiagnosticEventModel).where(
            ChannelDiagnosticEventModel.tenant_id == tenant_id
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
    def _instance(row: ChannelInstanceModel) -> ChannelInstance:
        return ChannelInstance(
            id=row.id,
            tenant_id=row.tenant_id,
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
                    "platform": instance.platform.value,
                    "status": instance.status.value,
                    "rate_limit_per_minute": instance.rate_limit_per_minute,
                },
            )
        )
