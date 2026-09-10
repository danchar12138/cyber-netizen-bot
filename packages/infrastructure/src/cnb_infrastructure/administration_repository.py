"""Agent、用户与审计资源的内存及 PostgreSQL 管理仓储。"""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import (
    AdministrationConflictError,
    AdministrationNotFoundError,
    AuditCursor,
    EntityCursor,
)
from cnb_domain import (
    AgentImpactCounts,
    AgentLifecycleStatus,
    AuditRecord,
    DevelopmentIdentity,
    EntityStatus,
    JsonValue,
    ManagedAgent,
    ManagedUser,
    ManagementOverview,
)
from cnb_infrastructure.models import (
    Agent,
    AgentRunModel,
    AuditLog,
    ChannelInstanceModel,
    CognitionResourceVersionModel,
    ConversationModel,
    EvaluationRunModel,
    EvaluationSuiteModel,
    MemoryModel,
    RelationshipModel,
    ScheduledActionModel,
    User,
)


class CognitionResourceCloner(Protocol):
    """内存开发模式复制已发布认知资源所需的最小边界。"""

    async def copy_published_resources(
        self,
        *,
        tenant_id: UUID,
        source_agent_id: UUID,
        target_agent_id: UUID,
        actor_id: UUID,
    ) -> int: ...


class MemoryAdministrationRepository:
    """供无数据库测试和前端联调使用的并发安全管理仓储。"""

    def __init__(
        self,
        identity: DevelopmentIdentity,
        cognition_cloner: CognitionResourceCloner | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self._agents = {
            identity.agent_id: ManagedAgent(
                id=identity.agent_id,
                tenant_id=identity.tenant_id,
                name=identity.agent_name,
                status=AgentLifecycleStatus.ACTIVE,
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
        self._agent_impacts: dict[UUID, AgentImpactCounts] = {}
        self._next_audit_id = 1
        self._cognition_cloner = cognition_cloner
        self._lock = asyncio.Lock()

    async def get_overview(self, *, tenant_id: UUID) -> ManagementOverview:
        async with self._lock:
            return ManagementOverview(
                active_agents=sum(
                    item.tenant_id == tenant_id and item.status is AgentLifecycleStatus.ACTIVE
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
        status: AgentLifecycleStatus | None,
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

    async def get_agent(self, *, tenant_id: UUID, agent_id: UUID) -> ManagedAgent | None:
        async with self._lock:
            agent = self._agents.get(agent_id)
            return agent if agent is not None and agent.tenant_id == tenant_id else None

    async def create_agent(
        self,
        *,
        tenant_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent:
        async with self._lock:
            self._ensure_unique_agent_name(tenant_id, name)
            agent = ManagedAgent(
                id=uuid4(),
                tenant_id=tenant_id,
                name=name,
                status=AgentLifecycleStatus.ACTIVE,
                created_at=datetime.now(UTC),
            )
            self._agents[agent.id] = agent
            self._append_audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="agent.created",
                resource_type="agent",
                resource_id=str(agent.id),
                detail={"name": name},
            )
            return agent

    async def copy_agent(
        self,
        *,
        tenant_id: UUID,
        source_agent_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent:
        async with self._lock:
            source = self._agents.get(source_agent_id)
            if source is None or source.tenant_id != tenant_id:
                raise AdministrationNotFoundError("要复制的 Agent 不存在")
            self._ensure_unique_agent_name(tenant_id, name)
            agent = ManagedAgent(
                id=uuid4(),
                tenant_id=tenant_id,
                name=name,
                status=AgentLifecycleStatus.ACTIVE,
                created_at=datetime.now(UTC),
            )
            self._agents[agent.id] = agent

        copied_resources = 0
        try:
            if self._cognition_cloner is not None:
                copied_resources = await self._cognition_cloner.copy_published_resources(
                    tenant_id=tenant_id,
                    source_agent_id=source_agent_id,
                    target_agent_id=agent.id,
                    actor_id=actor_id,
                )
        except Exception:
            async with self._lock:
                self._agents.pop(agent.id, None)
            raise

        async with self._lock:
            self._append_audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="agent.copied",
                resource_type="agent",
                resource_id=str(agent.id),
                detail={
                    "source_agent_id": str(source_agent_id),
                    "copied_resource_count": copied_resources,
                },
            )
        return agent

    async def rename_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent:
        async with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AdministrationNotFoundError("Agent 不存在")
            if agent.status is AgentLifecycleStatus.DELETED:
                raise AdministrationConflictError("已删除的 Agent 不能重命名")
            self._ensure_unique_agent_name(tenant_id, name, excluding_agent_id=agent_id)
            updated = replace(agent, name=name)
            self._agents[agent_id] = updated
            self._append_audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="agent.renamed",
                resource_type="agent",
                resource_id=str(agent_id),
                detail={"previous_name": agent.name, "name": name},
            )
            return updated

    async def get_agent_impact(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> tuple[AgentImpactCounts, int]:
        async with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AdministrationNotFoundError("Agent 不存在")
            active_replacements = sum(
                item.tenant_id == tenant_id
                and item.id != agent_id
                and item.status is AgentLifecycleStatus.ACTIVE
                for item in self._agents.values()
            )
            return self._agent_impacts.get(agent_id, AgentImpactCounts()), active_replacements

    async def archive_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        archived_at: datetime,
    ) -> ManagedAgent:
        async with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AdministrationNotFoundError("Agent 不存在")
            if agent.status not in {
                AgentLifecycleStatus.ACTIVE,
                AgentLifecycleStatus.DISABLED,
            }:
                raise AdministrationConflictError("只有已启用或已停用的 Agent 可以归档")
            has_replacement = any(
                item.tenant_id == tenant_id
                and item.id != agent_id
                and item.status is AgentLifecycleStatus.ACTIVE
                for item in self._agents.values()
            )
            if not has_replacement:
                raise AdministrationConflictError("归档前必须保留至少一个其他已启用 Agent")
            updated = replace(
                agent,
                status=AgentLifecycleStatus.ARCHIVED,
                archived_at=archived_at,
            )
            self._agents[agent_id] = updated
            self._append_audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="agent.archived",
                resource_type="agent",
                resource_id=str(agent_id),
                detail={
                    "previous_status": agent.status.value,
                    "archived_at": archived_at.isoformat(),
                    "runtime_intake_blocked": True,
                },
            )
            return updated

    async def soft_delete_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        deleted_at: datetime,
        purge_after: datetime,
        retention_days: int,
    ) -> ManagedAgent:
        async with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None or agent.tenant_id != tenant_id:
                raise AdministrationNotFoundError("Agent 不存在")
            if agent.status is not AgentLifecycleStatus.ARCHIVED:
                raise AdministrationConflictError("只有已归档的 Agent 可以进入软删除保留期")
            updated = replace(
                agent,
                status=AgentLifecycleStatus.DELETED,
                deleted_at=deleted_at,
                purge_after=purge_after,
            )
            self._agents[agent_id] = updated
            self._append_audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="agent.soft_deleted",
                resource_type="agent",
                resource_id=str(agent_id),
                detail={
                    "retention_days": retention_days,
                    "deleted_at": deleted_at.isoformat(),
                    "purge_after": purge_after.isoformat(),
                    "physical_delete_performed": False,
                },
            )
            return updated

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
            if any(
                item.status not in {AgentLifecycleStatus.ACTIVE, AgentLifecycleStatus.DISABLED}
                for item in targets
            ):
                raise AdministrationConflictError("已归档或已删除的 Agent 不能变更启停状态")
            lifecycle_status = AgentLifecycleStatus(status.value)
            updated = tuple(replace(item, status=lifecycle_status) for item in targets)
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

    def seed_agent_impact(self, agent_id: UUID, counts: AgentImpactCounts) -> None:
        """仅供无数据库测试设置影响统计。"""
        self._agent_impacts[agent_id] = counts

    def _ensure_unique_agent_name(
        self,
        tenant_id: UUID,
        name: str,
        *,
        excluding_agent_id: UUID | None = None,
    ) -> None:
        if any(
            item.tenant_id == tenant_id
            and item.id != excluding_agent_id
            and item.name.casefold() == name.casefold()
            for item in self._agents.values()
        ):
            raise AdministrationConflictError("当前租户已存在同名 Agent")


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
        status: AgentLifecycleStatus | None,
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

    async def get_agent(self, *, tenant_id: UUID, agent_id: UUID) -> ManagedAgent | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(Agent).where(Agent.tenant_id == tenant_id, Agent.id == agent_id)
            )
            return None if row is None else self._agent(row)

    async def create_agent(
        self,
        *,
        tenant_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent:
        try:
            async with self._session_factory() as session, session.begin():
                await self._ensure_unique_agent_name(session, tenant_id, name)
                row = Agent(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    name=name,
                    status=AgentLifecycleStatus.ACTIVE.value,
                )
                session.add(row)
                self._add_agent_audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="agent.created",
                    resource_id=row.id,
                    detail={"name": name},
                )
                await session.flush()
                return self._agent(row)
        except IntegrityError as error:
            raise AdministrationConflictError("当前租户已存在同名 Agent") from error

    async def rename_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent:
        try:
            async with self._session_factory() as session, session.begin():
                row = await session.scalar(
                    select(Agent)
                    .where(Agent.tenant_id == tenant_id, Agent.id == agent_id)
                    .with_for_update()
                )
                if row is None:
                    raise AdministrationNotFoundError("Agent 不存在")
                if row.status == AgentLifecycleStatus.DELETED.value:
                    raise AdministrationConflictError("已删除的 Agent 不能重命名")
                await self._ensure_unique_agent_name(
                    session,
                    tenant_id,
                    name,
                    excluding_agent_id=agent_id,
                )
                previous_name = row.name
                row.name = name
                self._add_agent_audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="agent.renamed",
                    resource_id=agent_id,
                    detail={"previous_name": previous_name, "name": name},
                )
                await session.flush()
                return self._agent(row)
        except IntegrityError as error:
            raise AdministrationConflictError("当前租户已存在同名 Agent") from error

    async def get_agent_impact(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> tuple[AgentImpactCounts, int]:
        async with self._session_factory() as session:
            exists = await session.scalar(
                select(Agent.id).where(Agent.tenant_id == tenant_id, Agent.id == agent_id)
            )
            if exists is None:
                raise AdministrationNotFoundError("Agent 不存在")

            counts = AgentImpactCounts(
                conversations=(
                    await session.scalar(
                        select(func.count())
                        .select_from(ConversationModel)
                        .where(
                            ConversationModel.tenant_id == tenant_id,
                            ConversationModel.agent_id == agent_id,
                        )
                    )
                    or 0
                ),
                agent_runs=(
                    await session.scalar(
                        select(func.count())
                        .select_from(AgentRunModel)
                        .where(
                            AgentRunModel.tenant_id == tenant_id,
                            AgentRunModel.agent_id == agent_id,
                        )
                    )
                    or 0
                ),
                cognition_resource_versions=(
                    await session.scalar(
                        select(func.count())
                        .select_from(CognitionResourceVersionModel)
                        .where(
                            CognitionResourceVersionModel.tenant_id == tenant_id,
                            CognitionResourceVersionModel.agent_id == agent_id,
                        )
                    )
                    or 0
                ),
                memories=(
                    await session.scalar(
                        select(func.count())
                        .select_from(MemoryModel)
                        .where(
                            MemoryModel.tenant_id == tenant_id,
                            MemoryModel.agent_id == agent_id,
                        )
                    )
                    or 0
                ),
                relationships=(
                    await session.scalar(
                        select(func.count())
                        .select_from(RelationshipModel)
                        .where(
                            RelationshipModel.tenant_id == tenant_id,
                            RelationshipModel.agent_id == agent_id,
                        )
                    )
                    or 0
                ),
                evaluation_suites=(
                    await session.scalar(
                        select(func.count())
                        .select_from(EvaluationSuiteModel)
                        .where(
                            EvaluationSuiteModel.tenant_id == tenant_id,
                            EvaluationSuiteModel.agent_id == agent_id,
                        )
                    )
                    or 0
                ),
                evaluation_runs=(
                    await session.scalar(
                        select(func.count())
                        .select_from(EvaluationRunModel)
                        .where(
                            EvaluationRunModel.tenant_id == tenant_id,
                            EvaluationRunModel.agent_id == agent_id,
                        )
                    )
                    or 0
                ),
                channel_instances=(
                    await session.scalar(
                        select(func.count())
                        .select_from(ChannelInstanceModel)
                        .where(
                            ChannelInstanceModel.tenant_id == tenant_id,
                            ChannelInstanceModel.agent_id == agent_id,
                        )
                    )
                    or 0
                ),
                scheduled_actions=(
                    await session.scalar(
                        select(func.count())
                        .select_from(ScheduledActionModel)
                        .where(
                            ScheduledActionModel.tenant_id == tenant_id,
                            ScheduledActionModel.agent_id == agent_id,
                        )
                    )
                    or 0
                ),
            )
            active_replacements = await session.scalar(
                select(func.count())
                .select_from(Agent)
                .where(
                    Agent.tenant_id == tenant_id,
                    Agent.id != agent_id,
                    Agent.status == AgentLifecycleStatus.ACTIVE.value,
                )
            )
            return counts, active_replacements or 0

    async def archive_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        archived_at: datetime,
    ) -> ManagedAgent:
        async with self._session_factory() as session, session.begin():
            rows = (
                await session.scalars(
                    select(Agent).where(Agent.tenant_id == tenant_id).with_for_update()
                )
            ).all()
            row = next((item for item in rows if item.id == agent_id), None)
            if row is None:
                raise AdministrationNotFoundError("Agent 不存在")
            if row.status not in {
                AgentLifecycleStatus.ACTIVE.value,
                AgentLifecycleStatus.DISABLED.value,
            }:
                raise AdministrationConflictError("只有已启用或已停用的 Agent 可以归档")
            if not any(
                item.id != agent_id and item.status == AgentLifecycleStatus.ACTIVE.value
                for item in rows
            ):
                raise AdministrationConflictError("归档前必须保留至少一个其他已启用 Agent")
            previous_status = row.status
            row.status = AgentLifecycleStatus.ARCHIVED.value
            row.archived_at = archived_at
            await session.execute(
                update(ChannelInstanceModel)
                .where(
                    ChannelInstanceModel.tenant_id == tenant_id,
                    ChannelInstanceModel.agent_id == agent_id,
                )
                .values(
                    status="disabled",
                    health_status="disabled",
                    health_detail="所属 Agent 已归档",
                    updated_at=archived_at,
                )
            )
            await session.execute(
                update(ScheduledActionModel)
                .where(
                    ScheduledActionModel.tenant_id == tenant_id,
                    ScheduledActionModel.agent_id == agent_id,
                    ScheduledActionModel.status.in_(("pending", "dispatched")),
                )
                .values(
                    status="canceled",
                    completed_at=archived_at,
                    updated_at=archived_at,
                )
            )
            self._add_agent_audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="agent.archived",
                resource_id=agent_id,
                detail={
                    "previous_status": previous_status,
                    "archived_at": archived_at.isoformat(),
                    "runtime_intake_blocked": True,
                },
            )
            await session.flush()
            return self._agent(row)

    async def soft_delete_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        deleted_at: datetime,
        purge_after: datetime,
        retention_days: int,
    ) -> ManagedAgent:
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(Agent)
                .where(Agent.tenant_id == tenant_id, Agent.id == agent_id)
                .with_for_update()
            )
            if row is None:
                raise AdministrationNotFoundError("Agent 不存在")
            if row.status != AgentLifecycleStatus.ARCHIVED.value:
                raise AdministrationConflictError("只有已归档的 Agent 可以进入软删除保留期")
            row.status = AgentLifecycleStatus.DELETED.value
            row.deleted_at = deleted_at
            row.purge_after = purge_after
            self._add_agent_audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="agent.soft_deleted",
                resource_id=agent_id,
                detail={
                    "retention_days": retention_days,
                    "deleted_at": deleted_at.isoformat(),
                    "purge_after": purge_after.isoformat(),
                    "physical_delete_performed": False,
                },
            )
            await session.flush()
            return self._agent(row)

    async def copy_agent(
        self,
        *,
        tenant_id: UUID,
        source_agent_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent:
        try:
            async with self._session_factory() as session, session.begin():
                source = await session.scalar(
                    select(Agent).where(
                        Agent.tenant_id == tenant_id,
                        Agent.id == source_agent_id,
                    )
                )
                if source is None:
                    raise AdministrationNotFoundError("要复制的 Agent 不存在")
                await self._ensure_unique_agent_name(session, tenant_id, name)
                row = Agent(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    name=name,
                    status=AgentLifecycleStatus.ACTIVE.value,
                )
                session.add(row)
                await session.flush()
                published = (
                    await session.scalars(
                        select(CognitionResourceVersionModel).where(
                            CognitionResourceVersionModel.tenant_id == tenant_id,
                            CognitionResourceVersionModel.agent_id == source_agent_id,
                            CognitionResourceVersionModel.status == "published",
                        )
                    )
                ).all()
                published_at = datetime.now(UTC)
                for resource in published:
                    session.add(
                        CognitionResourceVersionModel(
                            id=uuid4(),
                            tenant_id=tenant_id,
                            agent_id=row.id,
                            kind=resource.kind,
                            key=resource.key,
                            name=resource.name,
                            version=1,
                            status="published",
                            payload=deepcopy(resource.payload),
                            note=(
                                f"复制自 Agent {source_agent_id} 的 "
                                f"{resource.kind}/{resource.key} v{resource.version}"
                            ),
                            created_by=actor_id,
                            published_at=published_at,
                        )
                    )
                self._add_agent_audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="agent.copied",
                    resource_id=row.id,
                    detail={
                        "source_agent_id": str(source_agent_id),
                        "copied_resource_count": len(published),
                    },
                )
                await session.flush()
                return self._agent(row)
        except IntegrityError as error:
            raise AdministrationConflictError("当前租户已存在同名 Agent") from error

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
            if any(
                row.status
                not in {AgentLifecycleStatus.ACTIVE.value, AgentLifecycleStatus.DISABLED.value}
                for row in rows
            ):
                raise AdministrationConflictError("已归档或已删除的 Agent 不能变更启停状态")
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
            status=AgentLifecycleStatus(row.status),
            created_at=row.created_at,
            archived_at=row.archived_at,
            deleted_at=row.deleted_at,
            purge_after=row.purge_after,
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

    @staticmethod
    async def _ensure_unique_agent_name(
        session: AsyncSession,
        tenant_id: UUID,
        name: str,
        *,
        excluding_agent_id: UUID | None = None,
    ) -> None:
        statement = select(Agent.id).where(
            Agent.tenant_id == tenant_id,
            func.lower(Agent.name) == name.lower(),
        )
        if excluding_agent_id is not None:
            statement = statement.where(Agent.id != excluding_agent_id)
        existing = await session.scalar(statement)
        if existing is not None:
            raise AdministrationConflictError("当前租户已存在同名 Agent")

    @staticmethod
    def _add_agent_audit(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_id: UUID,
        detail: dict[str, JsonValue],
    ) -> None:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type="agent",
                resource_id=str(resource_id),
                detail=detail,
            )
        )
