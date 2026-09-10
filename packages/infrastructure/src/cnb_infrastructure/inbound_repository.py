"""外部身份与会话线程映射的内存及 PostgreSQL 实现。"""

import asyncio
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_domain import (
    ChannelPlatform,
    DevelopmentIdentity,
    ExternalConversationKind,
    ExternalConversationMapping,
    ExternalIdentityMapping,
    ExternalMappingStatus,
)
from cnb_infrastructure.models import (
    Agent,
    AuditLog,
    ChannelInstanceModel,
    ConversationModel,
    ExternalConversationMappingModel,
    ExternalIdentityMappingModel,
    User,
)


class MemoryInboundGatewayRepository:
    """用于测试和本地联调的并发安全映射仓储。"""

    def __init__(self, identity: DevelopmentIdentity) -> None:
        self._identity = identity
        self._users: set[UUID] = {identity.user_id}
        self._identities: dict[UUID, ExternalIdentityMapping] = {}
        self._conversations: dict[UUID, ExternalConversationMapping] = {}
        self._lock = asyncio.Lock()

    def seed_user(self, user_id: UUID) -> None:
        self._users.add(user_id)

    async def user_exists(self, *, tenant_id: UUID, user_id: UUID) -> bool:
        return tenant_id == self._identity.tenant_id and user_id in self._users

    async def create_identity_mapping(
        self, mapping: ExternalIdentityMapping
    ) -> ExternalIdentityMapping:
        async with self._lock:
            if any(
                item.tenant_id == mapping.tenant_id
                and item.platform is mapping.platform
                and item.channel_id == mapping.channel_id
                and item.external_subject_id == mapping.external_subject_id
                for item in self._identities.values()
            ):
                raise ValueError("该渠道已存在相同外部主体 ID 的身份映射")
            self._identities[mapping.id] = mapping
            return mapping

    async def get_identity_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
    ) -> ExternalIdentityMapping | None:
        async with self._lock:
            item = self._identities.get(mapping_id)
            return (
                item
                if item is not None and item.tenant_id == tenant_id and item.agent_id == agent_id
                else None
            )

    async def find_identity_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        external_subject_id: str,
        enabled_only: bool,
    ) -> ExternalIdentityMapping | None:
        async with self._lock:
            return next(
                (
                    item
                    for item in self._identities.values()
                    if item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.channel_id == channel_id
                    and item.external_subject_id == external_subject_id
                    and (not enabled_only or item.status is ExternalMappingStatus.ENABLED)
                ),
                None,
            )

    async def list_identity_mappings(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ExternalMappingStatus | None,
        limit: int,
    ) -> tuple[ExternalIdentityMapping, ...]:
        async with self._lock:
            rows = [
                item
                for item in self._identities.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (channel_id is None or item.channel_id == channel_id)
                and (status is None or item.status is status)
            ]
            rows.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
            return tuple(rows[:limit])

    async def update_identity_mapping_status(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
        status: ExternalMappingStatus,
        actor_id: UUID,
        updated_at: datetime,
    ) -> ExternalIdentityMapping | None:
        del actor_id
        async with self._lock:
            item = self._identities.get(mapping_id)
            if item is None or item.tenant_id != tenant_id or item.agent_id != agent_id:
                return None
            updated = replace(item, status=status, updated_at=updated_at)
            self._identities[mapping_id] = updated
            return updated

    async def create_conversation_mapping(
        self, mapping: ExternalConversationMapping
    ) -> ExternalConversationMapping:
        async with self._lock:
            if any(
                item.tenant_id == mapping.tenant_id
                and item.platform is mapping.platform
                and item.channel_id == mapping.channel_id
                and item.external_conversation_id == mapping.external_conversation_id
                and item.external_thread_id == mapping.external_thread_id
                for item in self._conversations.values()
            ):
                raise ValueError("该渠道已存在相同外部会话与线程的路由映射")
            if any(
                item.channel_id == mapping.channel_id
                and item.conversation_id == mapping.conversation_id
                for item in self._conversations.values()
            ):
                raise ValueError("该内部会话已绑定到当前渠道的其他外部路由")
            self._conversations[mapping.id] = mapping
            return mapping

    async def get_conversation_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
    ) -> ExternalConversationMapping | None:
        async with self._lock:
            item = self._conversations.get(mapping_id)
            return (
                item
                if item is not None and item.tenant_id == tenant_id and item.agent_id == agent_id
                else None
            )

    async def find_conversation_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        external_conversation_id: str,
        external_thread_id: str | None,
        enabled_only: bool,
    ) -> ExternalConversationMapping | None:
        async with self._lock:
            return next(
                (
                    item
                    for item in self._conversations.values()
                    if item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.channel_id == channel_id
                    and item.external_conversation_id == external_conversation_id
                    and item.external_thread_id == external_thread_id
                    and (not enabled_only or item.status is ExternalMappingStatus.ENABLED)
                ),
                None,
            )

    async def list_conversation_mappings(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ExternalMappingStatus | None,
        limit: int,
    ) -> tuple[ExternalConversationMapping, ...]:
        async with self._lock:
            rows = [
                item
                for item in self._conversations.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (channel_id is None or item.channel_id == channel_id)
                and (status is None or item.status is status)
            ]
            rows.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
            return tuple(rows[:limit])

    async def update_conversation_mapping_status(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
        status: ExternalMappingStatus,
        actor_id: UUID,
        updated_at: datetime,
    ) -> ExternalConversationMapping | None:
        del actor_id
        async with self._lock:
            item = self._conversations.get(mapping_id)
            if item is None or item.tenant_id != tenant_id or item.agent_id != agent_id:
                return None
            updated = replace(item, status=status, updated_at=updated_at)
            self._conversations[mapping_id] = updated
            return updated


class SqlAlchemyInboundGatewayRepository:
    """以数据库唯一键和双重作用域查询实现真实映射真相源。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def user_exists(self, *, tenant_id: UUID, user_id: UUID) -> bool:
        async with self._session_factory() as session:
            return (
                await session.scalar(
                    select(User.id).where(User.tenant_id == tenant_id, User.id == user_id)
                )
                is not None
            )

    async def create_identity_mapping(
        self, mapping: ExternalIdentityMapping
    ) -> ExternalIdentityMapping:
        try:
            async with self._session_factory.begin() as session:
                await self._validate_channel(session, mapping)
                user_exists = await session.scalar(
                    select(User.id).where(
                        User.tenant_id == mapping.tenant_id,
                        User.id == mapping.user_id,
                    )
                )
                if user_exists is None:
                    raise ValueError("本地用户不存在")
                session.add(self._identity_model(mapping))
                self._audit_identity(session, mapping, "external_identity.created")
        except IntegrityError as error:
            raise ValueError("该渠道已存在相同外部主体 ID 的身份映射") from error
        return mapping

    async def get_identity_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
    ) -> ExternalIdentityMapping | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ExternalIdentityMappingModel).where(
                    ExternalIdentityMappingModel.tenant_id == tenant_id,
                    ExternalIdentityMappingModel.agent_id == agent_id,
                    ExternalIdentityMappingModel.id == mapping_id,
                )
            )
        return None if row is None else self._identity(row)

    async def find_identity_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        external_subject_id: str,
        enabled_only: bool,
    ) -> ExternalIdentityMapping | None:
        statement = select(ExternalIdentityMappingModel).where(
            ExternalIdentityMappingModel.tenant_id == tenant_id,
            ExternalIdentityMappingModel.agent_id == agent_id,
            ExternalIdentityMappingModel.channel_id == channel_id,
            ExternalIdentityMappingModel.external_subject_id == external_subject_id,
        )
        if enabled_only:
            statement = statement.where(
                ExternalIdentityMappingModel.status == ExternalMappingStatus.ENABLED.value
            )
        async with self._session_factory() as session:
            row = await session.scalar(statement)
        return None if row is None else self._identity(row)

    async def list_identity_mappings(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ExternalMappingStatus | None,
        limit: int,
    ) -> tuple[ExternalIdentityMapping, ...]:
        statement = select(ExternalIdentityMappingModel).where(
            ExternalIdentityMappingModel.tenant_id == tenant_id,
            ExternalIdentityMappingModel.agent_id == agent_id,
        )
        if channel_id is not None:
            statement = statement.where(ExternalIdentityMappingModel.channel_id == channel_id)
        if status is not None:
            statement = statement.where(ExternalIdentityMappingModel.status == status.value)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(
                        ExternalIdentityMappingModel.updated_at.desc(),
                        ExternalIdentityMappingModel.id.desc(),
                    ).limit(limit)
                )
            ).all()
        return tuple(self._identity(row) for row in rows)

    async def update_identity_mapping_status(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
        status: ExternalMappingStatus,
        actor_id: UUID,
        updated_at: datetime,
    ) -> ExternalIdentityMapping | None:
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(ExternalIdentityMappingModel)
                .where(
                    ExternalIdentityMappingModel.tenant_id == tenant_id,
                    ExternalIdentityMappingModel.agent_id == agent_id,
                    ExternalIdentityMappingModel.id == mapping_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            row.status = status.value
            row.updated_at = updated_at
            mapping = self._identity(row)
            self._audit_identity(session, mapping, "external_identity.status_updated", actor_id)
            return mapping

    async def create_conversation_mapping(
        self, mapping: ExternalConversationMapping
    ) -> ExternalConversationMapping:
        try:
            async with self._session_factory.begin() as session:
                await self._validate_channel(session, mapping)
                conversation_exists = await session.scalar(
                    select(ConversationModel.id).where(
                        ConversationModel.tenant_id == mapping.tenant_id,
                        ConversationModel.agent_id == mapping.agent_id,
                        ConversationModel.id == mapping.conversation_id,
                        ConversationModel.deleted_at.is_(None),
                    )
                )
                if conversation_exists is None:
                    raise ValueError("内部会话不存在")
                session.add(self._conversation_model(mapping))
                self._audit_conversation(session, mapping, "external_conversation.created")
        except IntegrityError as error:
            raise ValueError("外部路由或内部会话已存在冲突映射") from error
        return mapping

    async def get_conversation_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
    ) -> ExternalConversationMapping | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ExternalConversationMappingModel).where(
                    ExternalConversationMappingModel.tenant_id == tenant_id,
                    ExternalConversationMappingModel.agent_id == agent_id,
                    ExternalConversationMappingModel.id == mapping_id,
                )
            )
        return None if row is None else self._conversation(row)

    async def find_conversation_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        external_conversation_id: str,
        external_thread_id: str | None,
        enabled_only: bool,
    ) -> ExternalConversationMapping | None:
        statement = select(ExternalConversationMappingModel).where(
            ExternalConversationMappingModel.tenant_id == tenant_id,
            ExternalConversationMappingModel.agent_id == agent_id,
            ExternalConversationMappingModel.channel_id == channel_id,
            ExternalConversationMappingModel.external_conversation_id == external_conversation_id,
            ExternalConversationMappingModel.external_thread_id == (external_thread_id or ""),
        )
        if enabled_only:
            statement = statement.where(
                ExternalConversationMappingModel.status == ExternalMappingStatus.ENABLED.value
            )
        async with self._session_factory() as session:
            row = await session.scalar(statement)
        return None if row is None else self._conversation(row)

    async def list_conversation_mappings(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ExternalMappingStatus | None,
        limit: int,
    ) -> tuple[ExternalConversationMapping, ...]:
        statement = select(ExternalConversationMappingModel).where(
            ExternalConversationMappingModel.tenant_id == tenant_id,
            ExternalConversationMappingModel.agent_id == agent_id,
        )
        if channel_id is not None:
            statement = statement.where(ExternalConversationMappingModel.channel_id == channel_id)
        if status is not None:
            statement = statement.where(ExternalConversationMappingModel.status == status.value)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(
                        ExternalConversationMappingModel.updated_at.desc(),
                        ExternalConversationMappingModel.id.desc(),
                    ).limit(limit)
                )
            ).all()
        return tuple(self._conversation(row) for row in rows)

    async def update_conversation_mapping_status(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
        status: ExternalMappingStatus,
        actor_id: UUID,
        updated_at: datetime,
    ) -> ExternalConversationMapping | None:
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(ExternalConversationMappingModel)
                .where(
                    ExternalConversationMappingModel.tenant_id == tenant_id,
                    ExternalConversationMappingModel.agent_id == agent_id,
                    ExternalConversationMappingModel.id == mapping_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            row.status = status.value
            row.updated_at = updated_at
            mapping = self._conversation(row)
            self._audit_conversation(
                session,
                mapping,
                "external_conversation.status_updated",
                actor_id,
            )
            return mapping

    @staticmethod
    async def _validate_channel(
        session: AsyncSession,
        mapping: ExternalIdentityMapping | ExternalConversationMapping,
    ) -> None:
        channel = await session.scalar(
            select(ChannelInstanceModel).where(
                ChannelInstanceModel.id == mapping.channel_id,
                ChannelInstanceModel.tenant_id == mapping.tenant_id,
                ChannelInstanceModel.agent_id == mapping.agent_id,
                ChannelInstanceModel.platform == mapping.platform.value,
            )
        )
        agent = await session.scalar(
            select(Agent.id).where(
                Agent.id == mapping.agent_id,
                Agent.tenant_id == mapping.tenant_id,
                Agent.status.in_(("active", "disabled")),
            )
        )
        if channel is None or agent is None:
            raise ValueError("渠道实例或 Agent 不存在")

    @staticmethod
    def _identity_model(item: ExternalIdentityMapping) -> ExternalIdentityMappingModel:
        return ExternalIdentityMappingModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            channel_id=item.channel_id,
            platform=item.platform.value,
            external_subject_id=item.external_subject_id,
            user_id=item.user_id,
            status=item.status.value,
            created_by=item.created_by,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _conversation_model(
        item: ExternalConversationMapping,
    ) -> ExternalConversationMappingModel:
        return ExternalConversationMappingModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            channel_id=item.channel_id,
            platform=item.platform.value,
            kind=item.kind.value,
            external_conversation_id=item.external_conversation_id,
            external_thread_id=item.external_thread_id or "",
            conversation_id=item.conversation_id,
            status=item.status.value,
            created_by=item.created_by,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _identity(row: ExternalIdentityMappingModel) -> ExternalIdentityMapping:
        return ExternalIdentityMapping(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            channel_id=row.channel_id,
            platform=ChannelPlatform(row.platform),
            external_subject_id=row.external_subject_id,
            user_id=row.user_id,
            status=ExternalMappingStatus(row.status),
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _conversation(row: ExternalConversationMappingModel) -> ExternalConversationMapping:
        return ExternalConversationMapping(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            channel_id=row.channel_id,
            platform=ChannelPlatform(row.platform),
            kind=ExternalConversationKind(row.kind),
            external_conversation_id=row.external_conversation_id,
            external_thread_id=row.external_thread_id or None,
            conversation_id=row.conversation_id,
            status=ExternalMappingStatus(row.status),
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _audit_identity(
        session: AsyncSession,
        item: ExternalIdentityMapping,
        action: str,
        actor_id: UUID | None = None,
    ) -> None:
        session.add(
            AuditLog(
                tenant_id=item.tenant_id,
                actor_id=actor_id or item.created_by,
                action=action,
                resource_type="external_identity_mapping",
                resource_id=str(item.id),
                detail={
                    "agent_id": str(item.agent_id),
                    "channel_id": str(item.channel_id),
                    "platform": item.platform.value,
                    "user_id": str(item.user_id),
                    "status": item.status.value,
                },
            )
        )

    @staticmethod
    def _audit_conversation(
        session: AsyncSession,
        item: ExternalConversationMapping,
        action: str,
        actor_id: UUID | None = None,
    ) -> None:
        session.add(
            AuditLog(
                tenant_id=item.tenant_id,
                actor_id=actor_id or item.created_by,
                action=action,
                resource_type="external_conversation_mapping",
                resource_id=str(item.id),
                detail={
                    "agent_id": str(item.agent_id),
                    "channel_id": str(item.channel_id),
                    "platform": item.platform.value,
                    "conversation_id": str(item.conversation_id),
                    "kind": item.kind.value,
                    "status": item.status.value,
                },
            )
        )
