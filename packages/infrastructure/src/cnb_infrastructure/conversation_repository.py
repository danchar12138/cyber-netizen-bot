"""最小对话闭环的内存与 PostgreSQL 仓储实现。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
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
    MessageFeedback,
    MessageFeedbackRating,
    MessageSearchResult,
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
    MessageFeedbackModel,
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
        self._edited_branches: dict[tuple[UUID, UUID, UUID], UUID] = {}
        self._feedback: dict[tuple[UUID, UUID], MessageFeedback] = {}
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
        search: str | None,
        status: ConversationStatus | None,
    ) -> tuple[Conversation, ...]:
        async with self._lock:
            rows = [
                item
                for item in self._conversations.values()
                if (item.id, user_id) in self._members
                and item.deleted_at is None
                and (search is None or search.casefold() in item.title.casefold())
                and (status is None or item.status is status)
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
            conversation = self._conversations.get(conversation_id)
            return (
                conversation
                if conversation is not None and conversation.deleted_at is None
                else None
            )

    async def update_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        title: str | None,
        status: ConversationStatus | None,
        pinned: bool | None,
    ) -> Conversation:
        async with self._lock:
            conversation = self._require_conversation(conversation_id, user_id)
            now = datetime.now(UTC)
            updated = replace(
                conversation,
                title=title if title is not None else conversation.title,
                status=status if status is not None else conversation.status,
                pinned_at=(now if pinned else None)
                if pinned is not None
                else conversation.pinned_at,
                archived_at=(
                    now
                    if status is ConversationStatus.ARCHIVED
                    else None
                    if status is ConversationStatus.ACTIVE
                    else conversation.archived_at
                ),
                updated_at=now,
            )
            self._conversations[conversation_id] = updated
            self._emit(
                conversation_id,
                "conversation.updated",
                self.conversation_payload(updated),
            )
            return self._conversations[conversation_id]

    async def soft_delete_conversation(
        self, *, conversation_id: UUID, user_id: UUID
    ) -> Conversation:
        async with self._lock:
            if (conversation_id, user_id) not in self._members:
                raise ConversationConflictError("当前用户无权访问该会话")
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise ConversationConflictError("会话不存在")
            if conversation.deleted_at is not None:
                return conversation
            now = datetime.now(UTC)
            deleted = replace(conversation, deleted_at=now, pinned_at=None, updated_at=now)
            self._conversations[conversation_id] = deleted
            self._emit(
                conversation_id,
                "conversation.deleted",
                self.conversation_payload(deleted),
            )
            return self._conversations[conversation_id]

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
        policy_version: int,
        model_route_version: int,
        model_profile: str,
    ) -> PendingAgentRun:
        async with self._lock:
            client_key = (conversation_id, identity.user_id, client_message_id)
            existing_id = self._client_runs.get(client_key)
            if existing_id is not None:
                existing = self._runs[existing_id]
                return self._pending(existing, created=False)

            conversation = self._require_conversation(conversation_id, identity.user_id)
            now = self._next_message_time(conversation.id)
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
                policy_version=policy_version,
                model_route_version=model_route_version,
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

    async def begin_regeneration(
        self,
        *,
        identity: DevelopmentIdentity,
        response_message_id: UUID,
        client_request_id: UUID,
        configuration_version: int,
        persona_version: int,
        prompt_version: int,
        policy_version: int,
        model_route_version: int,
        model_profile: str,
    ) -> PendingAgentRun:
        async with self._lock:
            original_response = self._messages.get(response_message_id)
            if original_response is None:
                raise ConversationConflictError("要重新生成的回复不存在")
            conversation = self._require_conversation(
                original_response.conversation_id, identity.user_id
            )
            if original_response.sender_type is not MessageSenderType.AGENT:
                raise ConversationConflictError("只能重新生成 Agent 回复")
            client_key = (conversation.id, identity.agent_id, client_request_id)
            existing_id = self._client_runs.get(client_key)
            if existing_id is not None:
                return self._pending(self._runs[existing_id], created=False)
            original_run = next(
                (
                    item
                    for item in self._runs.values()
                    if item.response_message_id == response_message_id
                ),
                None,
            )
            if original_run is None:
                raise ConversationConflictError("要重新生成的回复缺少关联 Agent Run")
            now = self._next_message_time(conversation.id)
            response = Message(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation.id,
                sender_type=MessageSenderType.AGENT,
                sender_id=identity.agent_id,
                content="",
                status=MessageStatus.PROCESSING,
                client_message_id=client_request_id,
                created_at=now,
                updated_at=now,
            )
            run = AgentRun(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation.id,
                agent_id=identity.agent_id,
                trigger_message_id=original_run.trigger_message_id,
                response_message_id=response.id,
                status=AgentRunStatus.QUEUED,
                configuration_version=configuration_version,
                persona_version=persona_version,
                prompt_version=prompt_version,
                policy_version=policy_version,
                model_route_version=model_route_version,
                model_profile=model_profile,
                input_tokens=None,
                output_tokens=None,
                error_code=None,
                created_at=now,
                started_at=None,
                completed_at=None,
            )
            self._messages[response.id] = response
            self._conversation_messages[conversation.id].append(response.id)
            self._runs[run.id] = run
            self._client_runs[client_key] = run.id
            self._conversations[conversation.id] = replace(conversation, updated_at=now)
            self._emit(
                conversation.id,
                "message.created",
                self.message_payload(response),
                run_id=run.id,
                message_id=response.id,
            )
            self._emit(
                conversation.id,
                "run.queued",
                self.run_payload(run),
                run_id=run.id,
                message_id=response.id,
            )
            return self._pending(run, created=True)

    async def begin_edited_branch(
        self,
        *,
        identity: DevelopmentIdentity,
        source_message_id: UUID,
        client_message_id: UUID,
        content: str,
        configuration_version: int,
        persona_version: int,
        prompt_version: int,
        policy_version: int,
        model_route_version: int,
        model_profile: str,
    ) -> PendingAgentRun:
        async with self._lock:
            branch_key = (source_message_id, identity.user_id, client_message_id)
            existing_id = self._edited_branches.get(branch_key)
            if existing_id is not None:
                return self._pending(self._runs[existing_id], created=False)
            source = self._messages.get(source_message_id)
            if source is None:
                raise ConversationConflictError("要编辑的消息不存在")
            original = self._require_conversation(source.conversation_id, identity.user_id)
            if source.sender_type is not MessageSenderType.USER:
                raise ConversationConflictError("只能编辑用户消息并创建分支")
            now = datetime.now(UTC)
            branch = Conversation(
                id=uuid4(),
                tenant_id=original.tenant_id,
                agent_id=original.agent_id,
                title=f"{original.title}（分支）"[:200],
                status=ConversationStatus.ACTIVE,
                created_by=identity.user_id,
                event_sequence=0,
                created_at=now,
                updated_at=now,
                branched_from_conversation_id=original.id,
                branched_from_message_id=source.id,
            )
            self._conversations[branch.id] = branch
            self._members.add((branch.id, identity.user_id))
            self._conversation_messages[branch.id] = []
            self._events[branch.id] = []
            self._emit(branch.id, "conversation.created", self.conversation_payload(branch))
            ordered_source = [
                self._messages[item]
                for item in self._conversation_messages[original.id]
                if (self._messages[item].created_at, self._messages[item].id)
                < (source.created_at, source.id)
            ]
            for index, item in enumerate(ordered_source, start=1):
                copied = replace(
                    item,
                    id=uuid4(),
                    conversation_id=branch.id,
                    client_message_id=None,
                    created_at=now + timedelta(microseconds=index),
                    updated_at=now + timedelta(microseconds=index),
                )
                self._messages[copied.id] = copied
                self._conversation_messages[branch.id].append(copied.id)
                self._emit(
                    branch.id,
                    "message.created",
                    self.message_payload(copied),
                    message_id=copied.id,
                )
            trigger_time = now + timedelta(microseconds=len(ordered_source) + 1)
            trigger = Message(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=branch.id,
                sender_type=MessageSenderType.USER,
                sender_id=identity.user_id,
                content=content,
                status=MessageStatus.RECEIVED,
                client_message_id=client_message_id,
                created_at=trigger_time,
                updated_at=trigger_time,
                edited_from_id=source.id,
            )
            response = Message(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=branch.id,
                sender_type=MessageSenderType.AGENT,
                sender_id=identity.agent_id,
                content="",
                status=MessageStatus.PROCESSING,
                client_message_id=None,
                created_at=trigger_time + timedelta(microseconds=1),
                updated_at=trigger_time + timedelta(microseconds=1),
            )
            run = AgentRun(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=branch.id,
                agent_id=identity.agent_id,
                trigger_message_id=trigger.id,
                response_message_id=response.id,
                status=AgentRunStatus.QUEUED,
                configuration_version=configuration_version,
                persona_version=persona_version,
                prompt_version=prompt_version,
                policy_version=policy_version,
                model_route_version=model_route_version,
                model_profile=model_profile,
                input_tokens=None,
                output_tokens=None,
                error_code=None,
                created_at=trigger_time,
                started_at=None,
                completed_at=None,
            )
            self._messages[trigger.id] = trigger
            self._messages[response.id] = response
            self._conversation_messages[branch.id].extend((trigger.id, response.id))
            self._runs[run.id] = run
            self._client_runs[(branch.id, identity.user_id, client_message_id)] = run.id
            self._edited_branches[branch_key] = run.id
            for item in (trigger, response):
                self._emit(
                    branch.id,
                    "message.created",
                    self.message_payload(item),
                    run_id=run.id,
                    message_id=item.id,
                )
            self._emit(
                branch.id,
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

    async def complete_run(
        self,
        run_id: UUID,
        usage: ModelUsage | None,
        *,
        suppress_response: bool = False,
    ) -> AgentRun:
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
                status=(MessageStatus.SUPPRESSED if suppress_response else MessageStatus.COMPLETED),
                updated_at=now,
            )
            self._runs[run.id] = completed
            self._messages[response.id] = response
            self._emit(
                run.conversation_id,
                "message.suppressed" if suppress_response else "message.completed",
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

    async def set_message_feedback(
        self,
        *,
        message_id: UUID,
        user_id: UUID,
        rating: MessageFeedbackRating,
        comment: str | None,
    ) -> MessageFeedback:
        async with self._lock:
            message = self._messages.get(message_id)
            if message is None:
                raise ConversationConflictError("要反馈的消息不存在")
            self._require_conversation(message.conversation_id, user_id)
            if message.sender_type is not MessageSenderType.AGENT:
                raise ConversationConflictError("只能对 Agent 回复提交反馈")
            key = (message_id, user_id)
            now = datetime.now(UTC)
            existing = self._feedback.get(key)
            feedback = (
                replace(existing, rating=rating, comment=comment, updated_at=now)
                if existing is not None
                else MessageFeedback(
                    id=uuid4(),
                    tenant_id=message.tenant_id,
                    conversation_id=message.conversation_id,
                    message_id=message.id,
                    user_id=user_id,
                    rating=rating,
                    comment=comment,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._feedback[key] = feedback
            self._emit(
                message.conversation_id,
                "message.feedback.updated",
                self.feedback_payload(feedback),
                message_id=message.id,
            )
            return feedback

    async def delete_message_feedback(self, *, message_id: UUID, user_id: UUID) -> None:
        async with self._lock:
            message = self._messages.get(message_id)
            if message is None:
                raise ConversationConflictError("要反馈的消息不存在")
            self._require_conversation(message.conversation_id, user_id)
            if self._feedback.pop((message_id, user_id), None) is not None:
                self._emit(
                    message.conversation_id,
                    "message.feedback.deleted",
                    {"message_id": str(message.id), "user_id": str(user_id)},
                    message_id=message.id,
                )

    async def list_message_feedback(
        self, *, conversation_id: UUID, user_id: UUID
    ) -> tuple[MessageFeedback, ...]:
        async with self._lock:
            self._require_conversation(conversation_id, user_id)
            return tuple(
                sorted(
                    (
                        item
                        for item in self._feedback.values()
                        if item.conversation_id == conversation_id and item.user_id == user_id
                    ),
                    key=lambda item: (item.created_at, item.id),
                )
            )

    async def search_messages(
        self,
        *,
        user_id: UUID,
        query: str,
        conversation_id: UUID | None,
        limit: int,
    ) -> tuple[MessageSearchResult, ...]:
        async with self._lock:
            needle = query.casefold()
            results = (
                MessageSearchResult(
                    conversation=self._conversations[item.conversation_id], message=item
                )
                for item in self._messages.values()
                if needle in item.content.casefold()
                and (item.conversation_id, user_id) in self._members
                and self._conversations[item.conversation_id].deleted_at is None
                and (conversation_id is None or item.conversation_id == conversation_id)
            )
            return tuple(
                sorted(
                    results,
                    key=lambda item: (item.message.created_at, item.message.id),
                    reverse=True,
                )[:limit]
            )

    def _require_conversation(self, conversation_id: UUID, user_id: UUID) -> Conversation:
        if (conversation_id, user_id) not in self._members:
            raise ConversationConflictError("当前用户无权访问该会话")
        try:
            conversation = self._conversations[conversation_id]
        except KeyError as error:
            raise ConversationConflictError("会话不存在") from error
        if conversation.deleted_at is not None:
            raise ConversationConflictError("会话已删除")
        return conversation

    def _next_message_time(self, conversation_id: UUID) -> datetime:
        """在系统时钟精度不足时仍保证同一会话内新消息严格后置。"""
        now = datetime.now(UTC)
        message_ids = self._conversation_messages.get(conversation_id, [])
        if not message_ids:
            return now
        latest = max(self._messages[message_id].created_at for message_id in message_ids)
        return max(now, latest + timedelta(microseconds=1))

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
            "edited_from_id": str(message.edited_from_id) if message.edited_from_id else None,
        }

    @staticmethod
    def conversation_payload(conversation: Conversation) -> dict[str, JsonValue]:
        return {
            "id": str(conversation.id),
            "title": conversation.title,
            "status": conversation.status.value,
            "pinned_at": conversation.pinned_at.isoformat() if conversation.pinned_at else None,
            "archived_at": (
                conversation.archived_at.isoformat() if conversation.archived_at else None
            ),
            "deleted_at": (
                conversation.deleted_at.isoformat() if conversation.deleted_at else None
            ),
            "updated_at": conversation.updated_at.isoformat(),
        }

    @staticmethod
    def feedback_payload(feedback: MessageFeedback) -> dict[str, JsonValue]:
        return {
            "id": str(feedback.id),
            "conversation_id": str(feedback.conversation_id),
            "message_id": str(feedback.message_id),
            "user_id": str(feedback.user_id),
            "rating": feedback.rating.value,
            "comment": feedback.comment,
            "created_at": feedback.created_at.isoformat(),
            "updated_at": feedback.updated_at.isoformat(),
        }

    @staticmethod
    def run_payload(run: AgentRun) -> dict[str, JsonValue]:
        return {
            "id": str(run.id),
            "conversation_id": str(run.conversation_id),
            "response_message_id": str(run.response_message_id),
            "status": run.status.value,
            "configuration_version": run.configuration_version,
            "persona_version": run.persona_version,
            "prompt_version": run.prompt_version,
            "policy_version": run.policy_version,
            "model_route_version": run.model_route_version,
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
        search: str | None,
        status: ConversationStatus | None,
    ) -> tuple[Conversation, ...]:
        statement = (
            select(ConversationModel)
            .join(ConversationMember)
            .where(
                ConversationMember.user_id == user_id,
                ConversationModel.deleted_at.is_(None),
            )
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
        if search is not None:
            statement = statement.where(ConversationModel.title.ilike(f"%{search}%"))
        if status is not None:
            statement = statement.where(ConversationModel.status == status.value)
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
            # 未声明 ORM relationship 时不能依赖 Unit of Work 推断跨 Mapper 的插入顺序。
            await session.flush()
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
                    ConversationModel.deleted_at.is_(None),
                )
            )
            return None if row is None else self._conversation(row)

    async def update_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        title: str | None,
        status: ConversationStatus | None,
        pinned: bool | None,
    ) -> Conversation:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_conversation(session, conversation_id, user_id)
            now = datetime.now(UTC)
            if title is not None:
                row.title = title
            if status is not None:
                row.status = status.value
                row.archived_at = now if status is ConversationStatus.ARCHIVED else None
            if pinned is not None:
                row.pinned_at = now if pinned else None
            row.updated_at = now
            await self._emit(
                session,
                row,
                "conversation.updated",
                self.conversation_payload(self._conversation(row)),
            )
            await session.flush()
            return self._conversation(row)

    async def soft_delete_conversation(
        self, *, conversation_id: UUID, user_id: UUID
    ) -> Conversation:
        async with self._session_factory() as session, session.begin():
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
            if row.deleted_at is not None:
                return self._conversation(row)
            now = datetime.now(UTC)
            row.deleted_at = now
            row.pinned_at = None
            row.updated_at = now
            await self._emit(
                session,
                row,
                "conversation.deleted",
                self.conversation_payload(self._conversation(row)),
            )
            await session.flush()
            return self._conversation(row)

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
        policy_version: int,
        model_route_version: int,
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
                    select(AgentRunModel)
                    .where(AgentRunModel.trigger_message_id == existing_trigger.id)
                    .order_by(AgentRunModel.created_at, AgentRunModel.id)
                    .limit(1)
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

            now = await self._next_persisted_message_time(session, conversation_id)
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
                policy_version=policy_version,
                model_route_version=model_route_version,
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

    async def begin_regeneration(
        self,
        *,
        identity: DevelopmentIdentity,
        response_message_id: UUID,
        client_request_id: UUID,
        configuration_version: int,
        persona_version: int,
        prompt_version: int,
        policy_version: int,
        model_route_version: int,
        model_profile: str,
    ) -> PendingAgentRun:
        async with self._session_factory() as session, session.begin():
            original_response = await session.scalar(
                select(MessageModel)
                .join(
                    ConversationMember,
                    ConversationMember.conversation_id == MessageModel.conversation_id,
                )
                .join(
                    ConversationModel,
                    ConversationModel.id == MessageModel.conversation_id,
                )
                .where(
                    MessageModel.id == response_message_id,
                    ConversationMember.user_id == identity.user_id,
                    ConversationModel.deleted_at.is_(None),
                )
            )
            if original_response is None:
                raise ConversationConflictError("要重新生成的回复不存在")
            if original_response.sender_type != MessageSenderType.AGENT.value:
                raise ConversationConflictError("只能重新生成 Agent 回复")
            conversation = await self._locked_conversation(
                session, original_response.conversation_id, identity.user_id
            )
            existing_response = await session.scalar(
                select(MessageModel).where(
                    MessageModel.conversation_id == conversation.id,
                    MessageModel.sender_id == identity.agent_id,
                    MessageModel.client_message_id == client_request_id,
                )
            )
            if existing_response is not None:
                existing_run = await session.scalar(
                    select(AgentRunModel).where(
                        AgentRunModel.response_message_id == existing_response.id
                    )
                )
                if existing_run is None:
                    raise ConversationConflictError("幂等重新生成请求缺少 Agent Run")
                trigger = await self._required_message(session, existing_run.trigger_message_id)
                return PendingAgentRun(
                    conversation=self._conversation(conversation),
                    trigger_message=self._message(trigger),
                    response_message=self._message(existing_response),
                    run=self._run(existing_run),
                    created=False,
                )
            original_run = await session.scalar(
                select(AgentRunModel).where(
                    AgentRunModel.response_message_id == response_message_id
                )
            )
            if original_run is None:
                raise ConversationConflictError("要重新生成的回复缺少关联 Agent Run")
            trigger = await self._required_message(session, original_run.trigger_message_id)
            now = await self._next_persisted_message_time(session, conversation.id)
            response = MessageModel(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation.id,
                sender_type=MessageSenderType.AGENT.value,
                sender_id=identity.agent_id,
                content="",
                status=MessageStatus.PROCESSING.value,
                client_message_id=client_request_id,
                created_at=now,
                updated_at=now,
            )
            session.add(response)
            await session.flush()
            run = AgentRunModel(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=conversation.id,
                agent_id=identity.agent_id,
                trigger_message_id=trigger.id,
                response_message_id=response.id,
                status=AgentRunStatus.QUEUED.value,
                configuration_version=configuration_version,
                persona_version=persona_version,
                prompt_version=prompt_version,
                policy_version=policy_version,
                model_route_version=model_route_version,
                model_profile=model_profile,
            )
            session.add(run)
            conversation.updated_at = now
            await session.flush()
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

    async def begin_edited_branch(
        self,
        *,
        identity: DevelopmentIdentity,
        source_message_id: UUID,
        client_message_id: UUID,
        content: str,
        configuration_version: int,
        persona_version: int,
        prompt_version: int,
        policy_version: int,
        model_route_version: int,
        model_profile: str,
    ) -> PendingAgentRun:
        async with self._session_factory() as session, session.begin():
            source = await session.scalar(
                select(MessageModel)
                .join(
                    ConversationMember,
                    ConversationMember.conversation_id == MessageModel.conversation_id,
                )
                .join(
                    ConversationModel,
                    ConversationModel.id == MessageModel.conversation_id,
                )
                .where(
                    MessageModel.id == source_message_id,
                    ConversationMember.user_id == identity.user_id,
                    ConversationModel.deleted_at.is_(None),
                )
            )
            if source is None:
                raise ConversationConflictError("要编辑的消息不存在")
            if source.sender_type != MessageSenderType.USER.value:
                raise ConversationConflictError("只能编辑用户消息并创建分支")
            existing_trigger = await session.scalar(
                select(MessageModel)
                .join(
                    ConversationModel,
                    ConversationModel.id == MessageModel.conversation_id,
                )
                .where(
                    MessageModel.edited_from_id == source.id,
                    MessageModel.sender_id == identity.user_id,
                    MessageModel.client_message_id == client_message_id,
                    ConversationModel.branched_from_message_id == source.id,
                )
            )
            if existing_trigger is not None:
                existing_run = await session.scalar(
                    select(AgentRunModel).where(
                        AgentRunModel.trigger_message_id == existing_trigger.id
                    )
                )
                branch = await session.get(ConversationModel, existing_trigger.conversation_id)
                response = (
                    await session.get(MessageModel, existing_run.response_message_id)
                    if existing_run is not None
                    else None
                )
                if existing_run is None or branch is None or response is None:
                    raise ConversationConflictError("幂等编辑分支缺少关联资源")
                return PendingAgentRun(
                    conversation=self._conversation(branch),
                    trigger_message=self._message(existing_trigger),
                    response_message=self._message(response),
                    run=self._run(existing_run),
                    created=False,
                )
            original = await self._locked_conversation(
                session, source.conversation_id, identity.user_id
            )
            now = datetime.now(UTC)
            branch = ConversationModel(
                id=uuid4(),
                tenant_id=original.tenant_id,
                agent_id=original.agent_id,
                title=f"{original.title}（分支）"[:200],
                status=ConversationStatus.ACTIVE.value,
                created_by=identity.user_id,
                event_sequence=0,
                created_at=now,
                updated_at=now,
                branched_from_conversation_id=original.id,
                branched_from_message_id=source.id,
            )
            session.add(branch)
            # 先落会话主记录，再写成员外键；两步仍处于同一数据库事务。
            await session.flush()
            session.add(
                ConversationMember(
                    id=uuid4(),
                    conversation_id=branch.id,
                    user_id=identity.user_id,
                    role="owner",
                )
            )
            await session.flush()
            await self._emit(
                session,
                branch,
                "conversation.created",
                self.conversation_payload(self._conversation(branch)),
            )
            source_rows = await session.scalars(
                select(MessageModel)
                .where(
                    MessageModel.conversation_id == original.id,
                    or_(
                        MessageModel.created_at < source.created_at,
                        and_(
                            MessageModel.created_at == source.created_at,
                            MessageModel.id < source.id,
                        ),
                    ),
                )
                .order_by(MessageModel.created_at, MessageModel.id)
            )
            copied_count = 0
            for index, item in enumerate(source_rows, start=1):
                copied_count = index
                copied = MessageModel(
                    id=uuid4(),
                    tenant_id=item.tenant_id,
                    conversation_id=branch.id,
                    sender_type=item.sender_type,
                    sender_id=item.sender_id,
                    content=item.content,
                    status=item.status,
                    client_message_id=None,
                    created_at=now + timedelta(microseconds=index),
                    updated_at=now + timedelta(microseconds=index),
                )
                session.add(copied)
                await session.flush()
                await self._emit(
                    session,
                    branch,
                    "message.created",
                    self.message_payload(self._message(copied)),
                    message_id=copied.id,
                )
            trigger_time = now + timedelta(microseconds=copied_count + 1)
            trigger = MessageModel(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=branch.id,
                sender_type=MessageSenderType.USER.value,
                sender_id=identity.user_id,
                content=content,
                status=MessageStatus.RECEIVED.value,
                client_message_id=client_message_id,
                created_at=trigger_time,
                updated_at=trigger_time,
                edited_from_id=source.id,
            )
            response = MessageModel(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=branch.id,
                sender_type=MessageSenderType.AGENT.value,
                sender_id=identity.agent_id,
                content="",
                status=MessageStatus.PROCESSING.value,
                client_message_id=None,
                created_at=trigger_time + timedelta(microseconds=1),
                updated_at=trigger_time + timedelta(microseconds=1),
            )
            session.add_all((trigger, response))
            await session.flush()
            run = AgentRunModel(
                id=uuid4(),
                tenant_id=identity.tenant_id,
                conversation_id=branch.id,
                agent_id=identity.agent_id,
                trigger_message_id=trigger.id,
                response_message_id=response.id,
                status=AgentRunStatus.QUEUED.value,
                configuration_version=configuration_version,
                persona_version=persona_version,
                prompt_version=prompt_version,
                policy_version=policy_version,
                model_route_version=model_route_version,
                model_profile=model_profile,
            )
            session.add(run)
            for item in (trigger, response):
                await self._emit(
                    session,
                    branch,
                    "message.created",
                    self.message_payload(self._message(item)),
                    run_id=run.id,
                    message_id=item.id,
                )
            await self._emit(
                session,
                branch,
                "run.queued",
                self.run_payload(self._run(run)),
                run_id=run.id,
                message_id=response.id,
            )
            await session.flush()
            return PendingAgentRun(
                conversation=self._conversation(branch),
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

    async def complete_run(
        self,
        run_id: UUID,
        usage: ModelUsage | None,
        *,
        suppress_response: bool = False,
    ) -> AgentRun:
        async with self._session_factory() as session, session.begin():
            run = await self._locked_running_run(session, run_id)
            conversation = await self._locked_conversation_by_id(session, run.conversation_id)
            response = await self._required_message(session, run.response_message_id)
            now = datetime.now(UTC)
            run.status = AgentRunStatus.COMPLETED.value
            run.input_tokens = usage.input_tokens if usage else None
            run.output_tokens = usage.output_tokens if usage else None
            run.completed_at = now
            response.status = (
                MessageStatus.SUPPRESSED.value
                if suppress_response
                else MessageStatus.COMPLETED.value
            )
            response.updated_at = now
            await self._emit(
                session,
                conversation,
                "message.suppressed" if suppress_response else "message.completed",
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

    async def set_message_feedback(
        self,
        *,
        message_id: UUID,
        user_id: UUID,
        rating: MessageFeedbackRating,
        comment: str | None,
    ) -> MessageFeedback:
        async with self._session_factory() as session, session.begin():
            message = await self._accessible_message(session, message_id, user_id)
            if message.sender_type != MessageSenderType.AGENT.value:
                raise ConversationConflictError("只能对 Agent 回复提交反馈")
            conversation = await self._locked_conversation(
                session, message.conversation_id, user_id
            )
            now = datetime.now(UTC)
            statement = (
                insert(MessageFeedbackModel)
                .values(
                    id=uuid4(),
                    tenant_id=message.tenant_id,
                    conversation_id=message.conversation_id,
                    message_id=message.id,
                    user_id=user_id,
                    rating=rating.value,
                    comment=comment,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_message_feedback_user",
                    set_={"rating": rating.value, "comment": comment, "updated_at": now},
                )
                .returning(MessageFeedbackModel)
            )
            feedback = (await session.scalars(statement)).one()
            await self._emit(
                session,
                conversation,
                "message.feedback.updated",
                self.feedback_payload(self._feedback(feedback)),
                message_id=message.id,
            )
            await session.flush()
            return self._feedback(feedback)

    async def delete_message_feedback(self, *, message_id: UUID, user_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            message = await self._accessible_message(session, message_id, user_id)
            feedback = await session.scalar(
                select(MessageFeedbackModel).where(
                    MessageFeedbackModel.message_id == message.id,
                    MessageFeedbackModel.user_id == user_id,
                )
            )
            if feedback is None:
                return
            conversation = await self._locked_conversation(
                session, message.conversation_id, user_id
            )
            await session.delete(feedback)
            await self._emit(
                session,
                conversation,
                "message.feedback.deleted",
                {"message_id": str(message.id), "user_id": str(user_id)},
                message_id=message.id,
            )

    async def list_message_feedback(
        self, *, conversation_id: UUID, user_id: UUID
    ) -> tuple[MessageFeedback, ...]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(MessageFeedbackModel)
                .join(
                    ConversationMember,
                    ConversationMember.conversation_id == MessageFeedbackModel.conversation_id,
                )
                .join(
                    ConversationModel,
                    ConversationModel.id == MessageFeedbackModel.conversation_id,
                )
                .where(
                    MessageFeedbackModel.conversation_id == conversation_id,
                    MessageFeedbackModel.user_id == user_id,
                    ConversationMember.user_id == user_id,
                    ConversationModel.deleted_at.is_(None),
                )
                .order_by(MessageFeedbackModel.created_at, MessageFeedbackModel.id)
            )
            return tuple(self._feedback(row) for row in rows)

    async def search_messages(
        self,
        *,
        user_id: UUID,
        query: str,
        conversation_id: UUID | None,
        limit: int,
    ) -> tuple[MessageSearchResult, ...]:
        full_text_match = func.to_tsvector("simple", MessageModel.content).op("@@")(
            func.plainto_tsquery("simple", query)
        )
        statement = (
            select(MessageModel, ConversationModel)
            .join(ConversationModel, ConversationModel.id == MessageModel.conversation_id)
            .join(
                ConversationMember,
                ConversationMember.conversation_id == MessageModel.conversation_id,
            )
            .where(
                ConversationMember.user_id == user_id,
                ConversationModel.deleted_at.is_(None),
                or_(full_text_match, MessageModel.content.ilike(f"%{query}%")),
            )
            .order_by(MessageModel.created_at.desc(), MessageModel.id.desc())
            .limit(limit)
        )
        if conversation_id is not None:
            statement = statement.where(MessageModel.conversation_id == conversation_id)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
            return tuple(
                MessageSearchResult(
                    conversation=self._conversation(conversation),
                    message=self._message(message),
                )
                for message, conversation in rows
            )

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
                ConversationModel.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if row is None:
            raise ConversationConflictError("当前用户无权访问该会话")
        return row

    @staticmethod
    async def _accessible_message(
        session: AsyncSession, message_id: UUID, user_id: UUID
    ) -> MessageModel:
        row = await session.scalar(
            select(MessageModel)
            .join(
                ConversationMember,
                ConversationMember.conversation_id == MessageModel.conversation_id,
            )
            .join(ConversationModel, ConversationModel.id == MessageModel.conversation_id)
            .where(
                MessageModel.id == message_id,
                ConversationMember.user_id == user_id,
                ConversationModel.deleted_at.is_(None),
            )
        )
        if row is None:
            raise ConversationConflictError("要反馈的消息不存在")
        return row

    @staticmethod
    async def _next_persisted_message_time(
        session: AsyncSession, conversation_id: UUID
    ) -> datetime:
        """在数据库时钟精度不足时仍保证同一会话内新消息严格后置。"""
        now = datetime.now(UTC)
        latest = await session.scalar(
            select(func.max(MessageModel.created_at)).where(
                MessageModel.conversation_id == conversation_id
            )
        )
        return now if latest is None else max(now, latest + timedelta(microseconds=1))

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
            pinned_at=row.pinned_at,
            archived_at=row.archived_at,
            deleted_at=row.deleted_at,
            branched_from_conversation_id=row.branched_from_conversation_id,
            branched_from_message_id=row.branched_from_message_id,
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
            edited_from_id=row.edited_from_id,
        )

    @staticmethod
    def _feedback(row: MessageFeedbackModel) -> MessageFeedback:
        return MessageFeedback(
            id=row.id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            message_id=row.message_id,
            user_id=row.user_id,
            rating=MessageFeedbackRating(row.rating),
            comment=row.comment,
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
            policy_version=row.policy_version,
            model_route_version=row.model_route_version,
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
    conversation_payload = staticmethod(MemoryConversationRepository.conversation_payload)
    feedback_payload = staticmethod(MemoryConversationRepository.feedback_payload)
    run_payload = staticmethod(MemoryConversationRepository.run_payload)
