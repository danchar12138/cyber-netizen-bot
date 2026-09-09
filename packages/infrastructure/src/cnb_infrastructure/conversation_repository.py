"""最小对话闭环的内存与 PostgreSQL 仓储实现。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import (
    AgentRunNotFoundError,
    ConversationConflictError,
    EntityCursor,
)
from cnb_cognition import ModelUsage
from cnb_domain import (
    AgentRun,
    AgentRunStatus,
    Conversation,
    ConversationEvent,
    ConversationStatus,
    DevelopmentIdentity,
    JsonValue,
    Message,
    MessageSenderType,
    MessageStatus,
    PendingAgentRun,
)
from cnb_infrastructure.models import (
    Agent,
    AgentRunModel,
    ConversationEventModel,
    ConversationMember,
    ConversationModel,
    MessageModel,
    Tenant,
    User,
)


class MemoryConversationRepository:
    """为测试和无基础设施开发提供的并发安全内存仓储。"""

    def __init__(self) -> None:
        self._identities: dict[UUID, DevelopmentIdentity] = {}
        self._conversations: dict[UUID, Conversation] = {}
        self._members: set[tuple[UUID, UUID]] = set()
        self._messages: dict[UUID, Message] = {}
        self._conversation_messages: dict[UUID, list[UUID]] = {}
        self._runs: dict[UUID, AgentRun] = {}
        self._client_runs: dict[tuple[UUID, UUID, UUID], UUID] = {}
        self._events: dict[UUID, list[ConversationEvent]] = {}
        self._lock = asyncio.Lock()

    async def ensure_development_identity(
        self, identity: DevelopmentIdentity
    ) -> DevelopmentIdentity:
        async with self._lock:
            self._identities[identity.user_id] = identity
            return identity

    async def list_conversations(
        self,
        *,
        user_id: UUID,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[Conversation, ...]:
        async with self._lock:
            rows = [
                item
                for item in self._conversations.values()
                if (item.id, user_id) in self._members
                and (
                    cursor is None
                    or (item.updated_at, item.id) < (cursor.occurred_at, cursor.entity_id)
                )
            ]
            return tuple(
                sorted(rows, key=lambda item: (item.updated_at, item.id), reverse=True)[:limit]
            )

    async def create_conversation(
        self,
        *,
        identity: DevelopmentIdentity,
        title: str,
    ) -> Conversation:
        async with self._lock:
            now = datetime.now(UTC)
            conversation = Conversation(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                agent_id=identity.agent_id,
                title=title,
                status=ConversationStatus.ACTIVE,
                created_by=identity.user_id,
                event_sequence=0,
                created_at=now,
                updated_at=now,
            )
            self._conversations[conversation.id] = conversation
            self._members.add((conversation.id, identity.user_id))
            self._conversation_messages[conversation.id] = []
            self._events[conversation.id] = []
            self._emit(
                conversation.id,
                "conversation.created",
                {"conversation_id": str(conversation.id), "title": conversation.title},
            )
            return self._conversations[conversation.id]

    async def get_conversation_for_user(
        self, conversation_id: UUID, user_id: UUID
    ) -> Conversation | None:
        async with self._lock:
            if (conversation_id, user_id) not in self._members:
                return None
            return self._conversations.get(conversation_id)

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[Message, ...]:
        async with self._lock:
            if (conversation_id, user_id) not in self._members:
                return ()
            rows = [
                self._messages[message_id]
                for message_id in self._conversation_messages.get(conversation_id, [])
            ]
            rows = [
                item
                for item in rows
                if cursor is None
                or (item.created_at, item.id) < (cursor.occurred_at, cursor.entity_id)
            ]
            return tuple(
                sorted(rows, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]
            )

    async def begin_agent_run(
        self,
        *,
        identity: DevelopmentIdentity,
        conversation_id: UUID,
        client_message_id: UUID,
        content: str,
        configuration_version: int,
        persona_version: int,
        prompt_version: int,
        model_profile: str,
    ) -> PendingAgentRun:
        async with self._lock:
            client_key = (conversation_id, identity.user_id, client_message_id)
            existing_id = self._client_runs.get(client_key)
            if existing_id is not None:
                existing = self._runs[existing_id]
                return self._pending(existing, created=False)

            conversation = self._require_conversation(conversation_id, identity.user_id)
            now = datetime.now(UTC)
            trigger = Message(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation_id,
                sender_type=MessageSenderType.USER,
                sender_id=identity.user_id,
                content=content,
                status=MessageStatus.RECEIVED,
                client_message_id=client_message_id,
                created_at=now,
                updated_at=now,
            )
            response = Message(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation_id,
                sender_type=MessageSenderType.AGENT,
                sender_id=identity.agent_id,
                content="",
                status=MessageStatus.PROCESSING,
                client_message_id=None,
                created_at=now + timedelta(microseconds=1),
                updated_at=now + timedelta(microseconds=1),
            )
            run = AgentRun(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation_id,
                agent_id=identity.agent_id,
                trigger_message_id=trigger.id,
                response_message_id=response.id,
                status=AgentRunStatus.QUEUED,
                configuration_version=configuration_version,
                persona_version=persona_version,
                prompt_version=prompt_version,
                model_profile=model_profile,
                input_tokens=None,
                output_tokens=None,
                error_code=None,
                created_at=now,
                started_at=None,
                completed_at=None,
            )
            self._messages[trigger.id] = trigger
            self._messages[response.id] = response
            self._conversation_messages[conversation_id].extend((trigger.id, response.id))
            self._runs[run.id] = run
            self._client_runs[client_key] = run.id
            self._conversations[conversation_id] = replace(conversation, updated_at=now)
            self._emit(
                conversation_id,
                "message.created",
                self.message_payload(trigger),
                run_id=run.id,
                message_id=trigger.id,
            )
            self._emit(
                conversation_id,
                "message.created",
                self.message_payload(response),
                run_id=run.id,
                message_id=response.id,
            )
            self._emit(
                conversation_id,
                "run.queued",
                self.run_payload(run),
                run_id=run.id,
                message_id=response.id,
            )
            return self._pending(run, created=True)

    async def list_context_messages(
        self, *, conversation_id: UUID, user_id: UUID, limit: int
    ) -> tuple[Message, ...]:
        rows = await self.list_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=limit,
            cursor=None,
        )
        return tuple(reversed(rows))

    async def mark_run_started(self, run_id: UUID) -> AgentRun:
        async with self._lock:
            run = self._require_run(run_id)
            if run.status is not AgentRunStatus.QUEUED:
                raise ConversationConflictError("只有排队中的 Agent Run 可以启动")
            now = datetime.now(UTC)
            started = replace(run, status=AgentRunStatus.RUNNING, started_at=now)
            response = replace(
                self._messages[run.response_message_id],
                status=MessageStatus.STREAMING,
                updated_at=now,
            )
            self._runs[run.id] = started
            self._messages[response.id] = response
            self._emit(
                run.conversation_id,
                "run.started",
                self.run_payload(started),
                run_id=run.id,
                message_id=response.id,
            )
            return started

    async def append_run_delta(self, run_id: UUID, delta: str) -> Message:
        async with self._lock:
            run = self._require_running_run(run_id)
            message = self._messages[run.response_message_id]
            updated = replace(
                message,
                content=message.content + delta,
                status=MessageStatus.STREAMING,
                updated_at=datetime.now(UTC),
            )
            self._messages[updated.id] = updated
            self._emit(
                run.conversation_id,
                "message.delta",
                {"message_id": str(updated.id), "delta": delta, "status": updated.status.value},
                run_id=run.id,
                message_id=updated.id,
            )
            return updated

    async def complete_run(self, run_id: UUID, usage: ModelUsage | None) -> AgentRun:
        async with self._lock:
            run = self._require_running_run(run_id)
            now = datetime.now(UTC)
            completed = replace(
                run,
                status=AgentRunStatus.COMPLETED,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                completed_at=now,
            )
            response = replace(
                self._messages[run.response_message_id],
                status=MessageStatus.COMPLETED,
                updated_at=now,
            )
            self._runs[run.id] = completed
            self._messages[response.id] = response
            self._emit(
                run.conversation_id,
                "message.completed",
                self.message_payload(response),
                run_id=run.id,
                message_id=response.id,
            )
            self._emit(
                run.conversation_id,
                "run.completed",
                self.run_payload(completed),
                run_id=run.id,
                message_id=response.id,
            )
            return completed

    async def fail_run(self, run_id: UUID, error_code: str) -> AgentRun:
        async with self._lock:
            run = self._require_run(run_id)
            if run.status is AgentRunStatus.CANCELLED:
                return run
            now = datetime.now(UTC)
            failed = replace(
                run,
                status=AgentRunStatus.FAILED,
                error_code=error_code,
                completed_at=now,
            )
            response = replace(
                self._messages[run.response_message_id],
                status=MessageStatus.FAILED,
                updated_at=now,
            )
            self._runs[run.id] = failed
            self._messages[response.id] = response
            self._emit(
                run.conversation_id,
                "run.failed",
                self.run_payload(failed),
                run_id=run.id,
                message_id=response.id,
            )
            return failed

    async def cancel_run(self, run_id: UUID, *, user_id: UUID) -> AgentRun:
        async with self._lock:
            run = self._require_run(run_id)
            self._require_conversation(run.conversation_id, user_id)
            if run.status in {
                AgentRunStatus.COMPLETED,
                AgentRunStatus.CANCELLED,
                AgentRunStatus.FAILED,
            }:
                return run
            now = datetime.now(UTC)
            cancelled = replace(run, status=AgentRunStatus.CANCELLED, completed_at=now)
            response = replace(
                self._messages[run.response_message_id],
                status=MessageStatus.CANCELLED,
                updated_at=now,
            )
            self._runs[run.id] = cancelled
            self._messages[response.id] = response
            self._emit(
                run.conversation_id,
                "run.cancelled",
                self.run_payload(cancelled),
                run_id=run.id,
                message_id=response.id,
            )
            return cancelled

    async def list_events(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> tuple[ConversationEvent, ...]:
        async with self._lock:
            self._require_conversation(conversation_id, user_id)
            return tuple(
                item
                for item in self._events.get(conversation_id, [])
                if item.sequence > after_sequence
            )[:limit]

    def _require_conversation(self, conversation_id: UUID, user_id: UUID) -> Conversation:
        if (conversation_id, user_id) not in self._members:
            raise ConversationConflictError("当前用户无权访问该会话")
        try:
            return self._conversations[conversation_id]
        except KeyError as error:
            raise ConversationConflictError("会话不存在") from error

    def _require_run(self, run_id: UUID) -> AgentRun:
        try:
            return self._runs[run_id]
        except KeyError as error:
            raise AgentRunNotFoundError(f"Agent Run 不存在：{run_id}") from error

    def _require_running_run(self, run_id: UUID) -> AgentRun:
        run = self._require_run(run_id)
        if run.status is not AgentRunStatus.RUNNING:
            raise ConversationConflictError("Agent Run 已不处于运行状态")
        return run

    def _pending(self, run: AgentRun, *, created: bool) -> PendingAgentRun:
        return PendingAgentRun(
            conversation=self._conversations[run.conversation_id],
            trigger_message=self._messages[run.trigger_message_id],
            response_message=self._messages[run.response_message_id],
            run=run,
            created=created,
        )

    def _emit(
        self,
        conversation_id: UUID,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        run_id: UUID | None = None,
        message_id: UUID | None = None,
    ) -> ConversationEvent:
        conversation = self._conversations[conversation_id]
        sequence = conversation.event_sequence + 1
        self._conversations[conversation_id] = replace(conversation, event_sequence=sequence)
        event = ConversationEvent(
            id=uuid4(),
            tenant_id=conversation.tenant_id,
            conversation_id=conversation_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
            run_id=run_id,
            message_id=message_id,
            created_at=datetime.now(UTC),
        )
        self._events[conversation_id].append(event)
        return event

    @staticmethod
    def message_payload(message: Message) -> dict[str, JsonValue]:
        return {
            "id": str(message.id),
            "conversation_id": str(message.conversation_id),
            "sender_type": message.sender_type.value,
            "sender_id": str(message.sender_id) if message.sender_id else None,
            "content": message.content,
            "status": message.status.value,
            "client_message_id": (
                str(message.client_message_id) if message.client_message_id else None
            ),
            "created_at": message.created_at.isoformat(),
            "updated_at": message.updated_at.isoformat(),
        }

    @staticmethod
    def run_payload(run: AgentRun) -> dict[str, JsonValue]:
        return {
            "id": str(run.id),
            "conversation_id": str(run.conversation_id),
            "response_message_id": str(run.response_message_id),
            "status": run.status.value,
            "model_profile": run.model_profile,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "error_code": run.error_code,
        }


class SqlAlchemyConversationRepository:
    """使用行锁保证事件序号与运行状态原子更新的 PostgreSQL 仓储。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_development_identity(
        self, identity: DevelopmentIdentity
    ) -> DevelopmentIdentity:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                insert(Tenant)
                .values(id=identity.tenant_id, name="本地开发租户", status="active")
                .on_conflict_do_nothing(index_elements=[Tenant.id])
            )
            await session.execute(
                insert(Agent)
                .values(
                    id=identity.agent_id,
                    tenant_id=identity.tenant_id,
                    name=identity.agent_name,
                    status="active",
                )
                .on_conflict_do_nothing(index_elements=[Agent.id])
            )
            await session.execute(
                insert(User)
                .values(
                    id=identity.user_id,
                    tenant_id=identity.tenant_id,
                    display_name=identity.user_name,
                    status="active",
                )
                .on_conflict_do_nothing(index_elements=[User.id])
            )
        return identity

    async def list_conversations(
        self,
        *,
        user_id: UUID,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[Conversation, ...]:
        statement = (
            select(ConversationModel)
            .join(ConversationMember)
            .where(ConversationMember.user_id == user_id)
            .order_by(ConversationModel.updated_at.desc(), ConversationModel.id.desc())
            .limit(limit)
        )
        if cursor is not None:
            statement = statement.where(
                or_(
                    ConversationModel.updated_at < cursor.occurred_at,
                    and_(
                        ConversationModel.updated_at == cursor.occurred_at,
                        ConversationModel.id < cursor.entity_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            return tuple(self._conversation(row) for row in await session.scalars(statement))

    async def create_conversation(
        self,
        *,
        identity: DevelopmentIdentity,
        title: str,
    ) -> Conversation:
        async with self._session_factory() as session, session.begin():
            row = ConversationModel(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                agent_id=identity.agent_id,
                title=title,
                status=ConversationStatus.ACTIVE.value,
                created_by=identity.user_id,
                event_sequence=0,
            )
            session.add(row)
            session.add(
                ConversationMember(
                    id=uuid4(),
                    conversation_id=row.id,
                    user_id=identity.user_id,
                    role="owner",
                )
            )
            await session.flush()
            await self._emit(
                session,
                row,
                "conversation.created",
                {"conversation_id": str(row.id), "title": row.title},
            )
            await session.flush()
            return self._conversation(row)

    async def get_conversation_for_user(
        self, conversation_id: UUID, user_id: UUID
    ) -> Conversation | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ConversationModel)
                .join(ConversationMember)
                .where(
                    ConversationModel.id == conversation_id,
                    ConversationMember.user_id == user_id,
                )
            )
            return None if row is None else self._conversation(row)

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[Message, ...]:
        statement = (
            select(MessageModel)
            .join(
                ConversationMember,
                ConversationMember.conversation_id == MessageModel.conversation_id,
            )
            .where(
                MessageModel.conversation_id == conversation_id,
                ConversationMember.user_id == user_id,
            )
            .order_by(MessageModel.created_at.desc(), MessageModel.id.desc())
            .limit(limit)
        )
        if cursor is not None:
            statement = statement.where(
                or_(
                    MessageModel.created_at < cursor.occurred_at,
                    and_(
                        MessageModel.created_at == cursor.occurred_at,
                        MessageModel.id < cursor.entity_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            return tuple(self._message(row) for row in await session.scalars(statement))

    async def begin_agent_run(
        self,
        *,
        identity: DevelopmentIdentity,
        conversation_id: UUID,
        client_message_id: UUID,
        content: str,
        configuration_version: int,
        persona_version: int,
        prompt_version: int,
        model_profile: str,
    ) -> PendingAgentRun:
        async with self._session_factory() as session, session.begin():
            conversation = await self._locked_conversation(
                session, conversation_id, identity.user_id
            )
            existing_trigger = await session.scalar(
                select(MessageModel).where(
                    MessageModel.conversation_id == conversation_id,
                    MessageModel.sender_id == identity.user_id,
                    MessageModel.client_message_id == client_message_id,
                )
            )
            if existing_trigger is not None:
                existing_run = await session.scalar(
                    select(AgentRunModel).where(
                        AgentRunModel.trigger_message_id == existing_trigger.id
                    )
                )
                if existing_run is None:
                    raise ConversationConflictError("幂等消息缺少关联的 Agent Run")
                response = await session.get(MessageModel, existing_run.response_message_id)
                if response is None:
                    raise ConversationConflictError("Agent Run 缺少回复消息")
                return PendingAgentRun(
                    conversation=self._conversation(conversation),
                    trigger_message=self._message(existing_trigger),
                    response_message=self._message(response),
                    run=self._run(existing_run),
                    created=False,
                )

            now = datetime.now(UTC)
            trigger = MessageModel(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation_id,
                sender_type=MessageSenderType.USER.value,
                sender_id=identity.user_id,
                content=content,
                status=MessageStatus.RECEIVED.value,
                client_message_id=client_message_id,
                created_at=now,
                updated_at=now,
            )
            response = MessageModel(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation_id,
                sender_type=MessageSenderType.AGENT.value,
                sender_id=identity.agent_id,
                content="",
                status=MessageStatus.PROCESSING.value,
                client_message_id=None,
                created_at=now + timedelta(microseconds=1),
                updated_at=now + timedelta(microseconds=1),
            )
            session.add_all((trigger, response))
            await session.flush()
            run = AgentRunModel(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation_id,
                agent_id=identity.agent_id,
                trigger_message_id=trigger.id,
                response_message_id=response.id,
                status=AgentRunStatus.QUEUED.value,
                configuration_version=configuration_version,
                persona_version=persona_version,
                prompt_version=prompt_version,
                model_profile=model_profile,
            )
            session.add(run)
            conversation.updated_at = datetime.now(UTC)
            await session.flush()
            await self._emit(
                session,
                conversation,
                "message.created",
                self.message_payload(self._message(trigger)),
                run_id=run.id,
                message_id=trigger.id,
            )
            await self._emit(
                session,
                conversation,
                "message.created",
                self.message_payload(self._message(response)),
                run_id=run.id,
                message_id=response.id,
            )
            await self._emit(
                session,
                conversation,
                "run.queued",
                self.run_payload(self._run(run)),
                run_id=run.id,
                message_id=response.id,
            )
            await session.flush()
            return PendingAgentRun(
                conversation=self._conversation(conversation),
                trigger_message=self._message(trigger),
                response_message=self._message(response),
                run=self._run(run),
                created=True,
            )

    async def list_context_messages(
        self, *, conversation_id: UUID, user_id: UUID, limit: int
    ) -> tuple[Message, ...]:
        rows = await self.list_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=limit,
            cursor=None,
        )
        return tuple(reversed(rows))

    async def mark_run_started(self, run_id: UUID) -> AgentRun:
        async with self._session_factory() as session, session.begin():
            run = await self._locked_run(session, run_id)
            if run.status != AgentRunStatus.QUEUED.value:
                raise ConversationConflictError("只有排队中的 Agent Run 可以启动")
            conversation = await self._locked_conversation_by_id(session, run.conversation_id)
            response = await self._required_message(session, run.response_message_id)
            now = datetime.now(UTC)
            run.status = AgentRunStatus.RUNNING.value
            run.started_at = now
            response.status = MessageStatus.STREAMING.value
            response.updated_at = now
            await self._emit(
                session,
                conversation,
                "run.started",
                self.run_payload(self._run(run)),
                run_id=run.id,
                message_id=response.id,
            )
            await session.flush()
            return self._run(run)

    async def append_run_delta(self, run_id: UUID, delta: str) -> Message:
        async with self._session_factory() as session, session.begin():
            run = await self._locked_running_run(session, run_id)
            conversation = await self._locked_conversation_by_id(session, run.conversation_id)
            response = await self._required_message(session, run.response_message_id)
            response.content += delta
            response.status = MessageStatus.STREAMING.value
            response.updated_at = datetime.now(UTC)
            await self._emit(
                session,
                conversation,
                "message.delta",
                {"message_id": str(response.id), "delta": delta, "status": response.status},
                run_id=run.id,
                message_id=response.id,
            )
            await session.flush()
            return self._message(response)

    async def complete_run(self, run_id: UUID, usage: ModelUsage | None) -> AgentRun:
        async with self._session_factory() as session, session.begin():
            run = await self._locked_running_run(session, run_id)
            conversation = await self._locked_conversation_by_id(session, run.conversation_id)
            response = await self._required_message(session, run.response_message_id)
            now = datetime.now(UTC)
            run.status = AgentRunStatus.COMPLETED.value
            run.input_tokens = usage.input_tokens if usage else None
            run.output_tokens = usage.output_tokens if usage else None
            run.completed_at = now
            response.status = MessageStatus.COMPLETED.value
            response.updated_at = now
            await self._emit(
                session,
                conversation,
                "message.completed",
                self.message_payload(self._message(response)),
                run_id=run.id,
                message_id=response.id,
            )
            await self._emit(
                session,
                conversation,
                "run.completed",
                self.run_payload(self._run(run)),
                run_id=run.id,
                message_id=response.id,
            )
            await session.flush()
            return self._run(run)

    async def fail_run(self, run_id: UUID, error_code: str) -> AgentRun:
        async with self._session_factory() as session, session.begin():
            run = await self._locked_run(session, run_id)
            if run.status == AgentRunStatus.CANCELLED.value:
                return self._run(run)
            conversation = await self._locked_conversation_by_id(session, run.conversation_id)
            response = await self._required_message(session, run.response_message_id)
            now = datetime.now(UTC)
            run.status = AgentRunStatus.FAILED.value
            run.error_code = error_code
            run.completed_at = now
            response.status = MessageStatus.FAILED.value
            response.updated_at = now
            await self._emit(
                session,
                conversation,
                "run.failed",
                self.run_payload(self._run(run)),
                run_id=run.id,
                message_id=response.id,
            )
            await session.flush()
            return self._run(run)

    async def cancel_run(self, run_id: UUID, *, user_id: UUID) -> AgentRun:
        async with self._session_factory() as session, session.begin():
            run = await self._locked_run(session, run_id)
            conversation = await self._locked_conversation(session, run.conversation_id, user_id)
            if run.status in {
                AgentRunStatus.COMPLETED.value,
                AgentRunStatus.CANCELLED.value,
                AgentRunStatus.FAILED.value,
            }:
                return self._run(run)
            response = await self._required_message(session, run.response_message_id)
            now = datetime.now(UTC)
            run.status = AgentRunStatus.CANCELLED.value
            run.completed_at = now
            response.status = MessageStatus.CANCELLED.value
            response.updated_at = now
            await self._emit(
                session,
                conversation,
                "run.cancelled",
                self.run_payload(self._run(run)),
                run_id=run.id,
                message_id=response.id,
            )
            await session.flush()
            return self._run(run)

    async def list_events(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> tuple[ConversationEvent, ...]:
        async with self._session_factory() as session:
            member = await session.scalar(
                select(ConversationMember.id).where(
                    ConversationMember.conversation_id == conversation_id,
                    ConversationMember.user_id == user_id,
                )
            )
            if member is None:
                raise ConversationConflictError("当前用户无权访问该会话")
            rows = await session.scalars(
                select(ConversationEventModel)
                .where(
                    ConversationEventModel.conversation_id == conversation_id,
                    ConversationEventModel.sequence > after_sequence,
                )
                .order_by(ConversationEventModel.sequence)
                .limit(limit)
            )
            return tuple(self._event(row) for row in rows)

    @staticmethod
    async def _locked_conversation(
        session: AsyncSession, conversation_id: UUID, user_id: UUID
    ) -> ConversationModel:
        row = await session.scalar(
            select(ConversationModel)
            .join(ConversationMember)
            .where(
                ConversationModel.id == conversation_id,
                ConversationMember.user_id == user_id,
            )
            .with_for_update()
        )
        if row is None:
            raise ConversationConflictError("当前用户无权访问该会话")
        return row

    @staticmethod
    async def _locked_conversation_by_id(
        session: AsyncSession, conversation_id: UUID
    ) -> ConversationModel:
        row = await session.scalar(
            select(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .with_for_update()
        )
        if row is None:
            raise ConversationConflictError("会话不存在")
        return row

    @staticmethod
    async def _locked_run(session: AsyncSession, run_id: UUID) -> AgentRunModel:
        row = await session.scalar(
            select(AgentRunModel).where(AgentRunModel.id == run_id).with_for_update()
        )
        if row is None:
            raise AgentRunNotFoundError(f"Agent Run 不存在：{run_id}")
        return row

    @classmethod
    async def _locked_running_run(cls, session: AsyncSession, run_id: UUID) -> AgentRunModel:
        row = await cls._locked_run(session, run_id)
        if row.status != AgentRunStatus.RUNNING.value:
            raise ConversationConflictError("Agent Run 已不处于运行状态")
        return row

    @staticmethod
    async def _required_message(session: AsyncSession, message_id: UUID) -> MessageModel:
        row = await session.get(MessageModel, message_id)
        if row is None:
            raise ConversationConflictError("Agent Run 关联的消息不存在")
        return row

    @staticmethod
    async def _emit(
        session: AsyncSession,
        conversation: ConversationModel,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        run_id: UUID | None = None,
        message_id: UUID | None = None,
    ) -> None:
        conversation.event_sequence += 1
        session.add(
            ConversationEventModel(
                id=uuid4(),
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                sequence=conversation.event_sequence,
                event_type=event_type,
                payload=cast(dict[str, object], payload),
                run_id=run_id,
                message_id=message_id,
            )
        )

    @staticmethod
    def _conversation(row: ConversationModel) -> Conversation:
        return Conversation(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            title=row.title,
            status=ConversationStatus(row.status),
            created_by=row.created_by,
            event_sequence=row.event_sequence,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _message(row: MessageModel) -> Message:
        return Message(
            id=row.id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            sender_type=MessageSenderType(row.sender_type),
            sender_id=row.sender_id,
            content=row.content,
            status=MessageStatus(row.status),
            client_message_id=row.client_message_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _run(row: AgentRunModel) -> AgentRun:
        return AgentRun(
            id=row.id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            agent_id=row.agent_id,
            trigger_message_id=row.trigger_message_id,
            response_message_id=row.response_message_id,
            status=AgentRunStatus(row.status),
            configuration_version=row.configuration_version,
            persona_version=row.persona_version,
            prompt_version=row.prompt_version,
            model_profile=row.model_profile,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            error_code=row.error_code,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    def _event(row: ConversationEventModel) -> ConversationEvent:
        return ConversationEvent(
            id=row.id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            sequence=row.sequence,
            event_type=row.event_type,
            payload=cast(dict[str, JsonValue], row.payload),
            run_id=row.run_id,
            message_id=row.message_id,
            created_at=row.created_at,
        )

    message_payload = staticmethod(MemoryConversationRepository.message_payload)
    run_payload = staticmethod(MemoryConversationRepository.run_payload)
