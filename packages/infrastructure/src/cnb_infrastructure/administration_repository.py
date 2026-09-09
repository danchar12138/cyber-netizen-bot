"""Agent、用户与审计资源的内存及 PostgreSQL 管理仓储。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import AdministrationNotFoundError, AuditCursor, EntityCursor
from cnb_domain import (
    AuditRecord,
    DevelopmentIdentity,
    EntityStatus,
    JsonValue,
    ManagedAgent,
    ManagedUser,
    ManagementOverview,
)
from cnb_infrastructure.models import Agent, AuditLog, ConversationModel, User


class MemoryAdministrationRepository:
    """供无数据库测试和前端联调使用的并发安全管理仓储。"""

    def __init__(self, identity: DevelopmentIdentity) -> None:
        now = datetime.now(UTC)
        self._agents = {
            identity.agent_id: ManagedAgent(
                id=identity.agent_id,
                tenant_id=identity.tenant_id,
                name=identity.agent_name,
                status=EntityStatus.ACTIVE,
                created_at=now,
            )
        }
        self._users = {
            identity.user_id: ManagedUser(
                id=identity.user_id,
                tenant_id=identity.tenant_id,
                display_name=identity.user_name,
                status=EntityStatus.ACTIVE,
                created_at=now,
            )
        }
        self._audit_records: list[AuditRecord] = []
        self._next_audit_id = 1
        self._lock = asyncio.Lock()

    async def get_overview(self, *, tenant_id: UUID) -> ManagementOverview:
        async with self._lock:
            return ManagementOverview(
                active_agents=sum(
                    item.tenant_id == tenant_id and item.status is EntityStatus.ACTIVE
                    for item in self._agents.values()
                ),
                active_conversations=0,
                pending_jobs=0,
            )

    async def list_agents(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: EntityStatus | None,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[ManagedAgent, ...]:
        async with self._lock:
            rows = [
                item
                for item in self._agents.values()
                if item.tenant_id == tenant_id
                and (status is None or item.status is status)
                and (search is None or search.casefold() in item.name.casefold())
                and (
                    cursor is None
                    or (item.created_at, item.id) < (cursor.occurred_at, cursor.entity_id)
                )
            ]
            return tuple(
                sorted(rows, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]
            )

    async def update_agent_status(
        self,
        *,
        tenant_id: UUID,
        agent_ids: tuple[UUID, ...],
        status: EntityStatus,
        actor_id: UUID,
    ) -> tuple[ManagedAgent, ...]:
        async with self._lock:
            targets = tuple(
                item
                for agent_id in agent_ids
                if (item := self._agents.get(agent_id)) is not None and item.tenant_id == tenant_id
            )
            if len(targets) != len(agent_ids):
                raise AdministrationNotFoundError("一个或多个 Agent 不存在")
            updated = tuple(replace(item, status=status) for item in targets)
            self._agents.update((item.id, item) for item in updated)
            self._append_audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="agent.status_updated",
                resource_type="agent",
                resource_id=None,
                detail={"ids": [str(item.id) for item in updated], "status": status.value},
            )
            return updated

    async def list_users(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: EntityStatus | None,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[ManagedUser, ...]:
        async with self._lock:
            rows = [
                item
                for item in self._users.values()
                if item.tenant_id == tenant_id
                and (status is None or item.status is status)
                and (search is None or search.casefold() in item.display_name.casefold())
                and (
                    cursor is None
                    or (item.created_at, item.id) < (cursor.occurred_at, cursor.entity_id)
                )
            ]
            return tuple(
                sorted(rows, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]
            )

    async def update_user_status(
        self,
        *,
        tenant_id: UUID,
        user_ids: tuple[UUID, ...],
        status: EntityStatus,
        actor_id: UUID,
    ) -> tuple[ManagedUser, ...]:
        async with self._lock:
            targets = tuple(
                item
                for user_id in user_ids
                if (item := self._users.get(user_id)) is not None and item.tenant_id == tenant_id
            )
            if len(targets) != len(user_ids):
                raise AdministrationNotFoundError("一个或多个用户不存在")
            updated = tuple(replace(item, status=status) for item in targets)
            self._users.update((item.id, item) for item in updated)
            self._append_audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="user.status_updated",
                resource_type="user",
                resource_id=None,
                detail={"ids": [str(item.id) for item in updated], "status": status.value},
            )
            return updated

    async def list_audit_records(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        action: str | None,
        limit: int,
        cursor: AuditCursor | None,
    ) -> tuple[AuditRecord, ...]:
        async with self._lock:
            query = search.casefold() if search else None
            rows = [
                item
                for item in self._audit_records
                if (action is None or item.action == action)
                and (
                    query is None
                    or query in item.action.casefold()
                    or query in item.resource_type.casefold()
                    or query in (item.resource_id or "").casefold()
                )
                and (
                    cursor is None
                    or (item.created_at, item.id) < (cursor.occurred_at, cursor.record_id)
                )
            ]
            return tuple(
                sorted(rows, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]
            )

    def _append_audit(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: str | None,
        detail: dict[str, JsonValue],
    ) -> None:
        self._audit_records.append(
            AuditRecord(
                id=self._next_audit_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                detail=detail,
                created_at=datetime.now(UTC),
            )
        )
        self._next_audit_id += 1


class SqlAlchemyAdministrationRepository:
    """使用 PostgreSQL 提供租户隔离查询、批量状态更新和审计。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_overview(self, *, tenant_id: UUID) -> ManagementOverview:
        async with self._session_factory() as session:
            active_agents = await session.scalar(
                select(func.count())
                .select_from(Agent)
                .where(
                    Agent.tenant_id == tenant_id,
                    Agent.status == EntityStatus.ACTIVE.value,
                )
            )
            active_conversations = await session.scalar(
                select(func.count())
                .select_from(ConversationModel)
                .where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.status == "active",
                )
            )
            return ManagementOverview(
                active_agents=active_agents or 0,
                active_conversations=active_conversations or 0,
                pending_jobs=0,
            )

    async def list_agents(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: EntityStatus | None,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[ManagedAgent, ...]:
        statement = select(Agent).where(Agent.tenant_id == tenant_id)
        if search is not None:
            statement = statement.where(Agent.name.ilike(f"%{search}%"))
        if status is not None:
            statement = statement.where(Agent.status == status.value)
        if cursor is not None:
            statement = statement.where(
                or_(
                    Agent.created_at < cursor.occurred_at,
                    and_(Agent.created_at == cursor.occurred_at, Agent.id < cursor.entity_id),
                )
            )
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(Agent.created_at.desc(), Agent.id.desc()).limit(limit)
                )
            ).all()
            return tuple(self._agent(row) for row in rows)

    async def update_agent_status(
        self,
        *,
        tenant_id: UUID,
        agent_ids: tuple[UUID, ...],
        status: EntityStatus,
        actor_id: UUID,
    ) -> tuple[ManagedAgent, ...]:
        async with self._session_factory() as session, session.begin():
            rows = (
                await session.scalars(
                    select(Agent)
                    .where(Agent.tenant_id == tenant_id, Agent.id.in_(agent_ids))
                    .with_for_update()
                )
            ).all()
            if len(rows) != len(agent_ids):
                raise AdministrationNotFoundError("一个或多个 Agent 不存在")
            for row in rows:
                row.status = status.value
            self._add_audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="agent.status_updated",
                resource_type="agent",
                ids=agent_ids,
                status=status,
            )
            await session.flush()
            return tuple(self._agent(row) for row in rows)

    async def list_users(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: EntityStatus | None,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[ManagedUser, ...]:
        statement = select(User).where(User.tenant_id == tenant_id)
        if search is not None:
            statement = statement.where(User.display_name.ilike(f"%{search}%"))
        if status is not None:
            statement = statement.where(User.status == status.value)
        if cursor is not None:
            statement = statement.where(
                or_(
                    User.created_at < cursor.occurred_at,
                    and_(User.created_at == cursor.occurred_at, User.id < cursor.entity_id),
                )
            )
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(User.created_at.desc(), User.id.desc()).limit(limit)
                )
            ).all()
            return tuple(self._user(row) for row in rows)

    async def update_user_status(
        self,
        *,
        tenant_id: UUID,
        user_ids: tuple[UUID, ...],
        status: EntityStatus,
        actor_id: UUID,
    ) -> tuple[ManagedUser, ...]:
        async with self._session_factory() as session, session.begin():
            rows = (
                await session.scalars(
                    select(User)
                    .where(User.tenant_id == tenant_id, User.id.in_(user_ids))
                    .with_for_update()
                )
            ).all()
            if len(rows) != len(user_ids):
                raise AdministrationNotFoundError("一个或多个用户不存在")
            for row in rows:
                row.status = status.value
            self._add_audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="user.status_updated",
                resource_type="user",
                ids=user_ids,
                status=status,
            )
            await session.flush()
            return tuple(self._user(row) for row in rows)

    async def list_audit_records(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        action: str | None,
        limit: int,
        cursor: AuditCursor | None,
    ) -> tuple[AuditRecord, ...]:
        statement = select(AuditLog).where(
            or_(AuditLog.tenant_id == tenant_id, AuditLog.tenant_id.is_(None))
        )
        if search is not None:
            pattern = f"%{search}%"
            statement = statement.where(
                or_(
                    AuditLog.action.ilike(pattern),
                    AuditLog.resource_type.ilike(pattern),
                    AuditLog.resource_id.ilike(pattern),
                )
            )
        if action is not None:
            statement = statement.where(AuditLog.action == action)
        if cursor is not None:
            statement = statement.where(
                or_(
                    AuditLog.created_at < cursor.occurred_at,
                    and_(
                        AuditLog.created_at == cursor.occurred_at,
                        AuditLog.id < cursor.record_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
                )
            ).all()
            return tuple(self._audit(row) for row in rows)

    @staticmethod
    def _agent(row: Agent) -> ManagedAgent:
        return ManagedAgent(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            status=EntityStatus(row.status),
            created_at=row.created_at,
        )

    @staticmethod
    def _user(row: User) -> ManagedUser:
        return ManagedUser(
            id=row.id,
            tenant_id=row.tenant_id,
            display_name=row.display_name,
            status=EntityStatus(row.status),
            created_at=row.created_at,
        )

    @staticmethod
    def _audit(row: AuditLog) -> AuditRecord:
        return AuditRecord(
            id=row.id,
            actor_id=row.actor_id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            detail=cast(dict[str, JsonValue], row.detail),
            created_at=row.created_at,
        )

    @staticmethod
    def _add_audit(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        ids: tuple[UUID, ...],
        status: EntityStatus,
    ) -> None:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=None,
                detail={"ids": [str(item) for item in ids], "status": status.value},
            )
        )
