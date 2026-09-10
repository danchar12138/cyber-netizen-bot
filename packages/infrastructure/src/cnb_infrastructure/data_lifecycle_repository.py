"""数据生命周期的内存测试仓储与 PostgreSQL 真相源实现。"""

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from cnb_application import (
    AgentRetentionCandidate,
    DataLifecycleNotFoundError,
    DataLifecycleValidationError,
    ExportSnapshot,
    ForgetResult,
    RetentionCandidate,
)
from cnb_domain import (
    DevelopmentIdentity,
    JsonValue,
    LifecycleRun,
    LifecycleRunKind,
    LifecycleRunStatus,
)
from cnb_infrastructure.models import (
    ActionCandidateModel,
    AdminSession,
    Agent,
    AgentRunModel,
    AttachmentModel,
    AuditLog,
    BackgroundJobModel,
    ChannelInstanceModel,
    ConfigurationValue,
    ConversationEventModel,
    ConversationMember,
    ConversationModel,
    DataLifecycleRunModel,
    EpisodeModel,
    ExternalIdentity,
    InboxEventModel,
    MemoryEmbeddingModel,
    MemoryLinkModel,
    MemoryModel,
    MemorySourceModel,
    MessageFeedbackModel,
    MessageModel,
    MessagePartModel,
    OutboxEventModel,
    RelationshipEventModel,
    RelationshipModel,
    RoleAssignment,
    RunStepModel,
    ScheduledActionModel,
    SecretReference,
    User,
)


class MemoryDataLifecycleRepository:
    """为无基础设施测试提供可控的生命周期运行与候选数据。"""

    def __init__(self, identity: DevelopmentIdentity) -> None:
        self._identity = identity
        self._runs: dict[UUID, LifecycleRun] = {}
        self._exports: dict[UUID, ExportSnapshot] = {
            identity.user_id: ExportSnapshot(
                data={
                    "profile": {
                        "id": str(identity.user_id),
                        "display_name": identity.user_name,
                        "status": "active",
                    },
                    "conversations": [],
                    "memories": [],
                    "relationships": [],
                },
                record_count=1,
            )
        }
        self._forget_objects: dict[UUID, tuple[str, ...]] = {}
        self._retention_candidates: dict[UUID, RetentionCandidate] = {}
        self._agent_retention_candidates: dict[UUID, AgentRetentionCandidate] = {}
        self._known_object_keys: set[str] = set()
        self._lock = asyncio.Lock()

    async def start_run(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        subject_user_id: UUID | None,
        kind: LifecycleRunKind,
    ) -> LifecycleRun:
        async with self._lock:
            run = LifecycleRun(
                id=uuid4(),
                tenant_id=tenant_id,
                actor_id=actor_id,
                subject_user_id=subject_user_id,
                kind=kind,
                status=LifecycleRunStatus.RUNNING,
                counters={},
                evidence={},
                error_code=None,
                started_at=datetime.now(UTC),
                completed_at=None,
            )
            self._runs[run.id] = run
            return run

    async def finish_run(
        self,
        run_id: UUID,
        *,
        tenant_id: UUID,
        status: LifecycleRunStatus,
        counters: dict[str, int],
        evidence: dict[str, JsonValue],
        error_code: str | None = None,
    ) -> LifecycleRun:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.tenant_id != tenant_id:
                raise DataLifecycleNotFoundError("生命周期运行记录不存在")
            completed = replace(
                run,
                status=status,
                counters=dict(counters),
                evidence=dict(evidence),
                error_code=error_code,
                completed_at=datetime.now(UTC),
            )
            self._runs[run_id] = completed
            return completed

    async def list_runs(self, *, tenant_id: UUID, limit: int) -> tuple[LifecycleRun, ...]:
        async with self._lock:
            rows = (item for item in self._runs.values() if item.tenant_id == tenant_id)
            return tuple(sorted(rows, key=lambda item: item.started_at, reverse=True)[:limit])

    async def collect_user_export(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        max_records: int,
    ) -> ExportSnapshot:
        async with self._lock:
            snapshot = self._exports.get(user_id)
            if tenant_id != self._identity.tenant_id or snapshot is None:
                raise DataLifecycleNotFoundError("用户不存在")
            if snapshot.record_count > max_records:
                raise DataLifecycleValidationError("用户数据超过已配置的导出记录上限")
            return snapshot

    async def forget_user_data(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        actor_id: UUID,
    ) -> ForgetResult:
        del actor_id
        async with self._lock:
            snapshot = self._exports.get(user_id)
            if tenant_id != self._identity.tenant_id or snapshot is None:
                raise DataLifecycleNotFoundError("用户不存在")
            self._exports[user_id] = ExportSnapshot(
                data={
                    "profile": {
                        "id": str(user_id),
                        "display_name": "已遗忘用户",
                        "status": "disabled",
                    },
                    "conversations": [],
                    "memories": [],
                    "relationships": [],
                },
                record_count=1,
            )
            object_keys = self._forget_objects.get(user_id, ())
            self._known_object_keys.difference_update(object_keys)
            return ForgetResult(
                object_keys=object_keys,
                counters={"users_redacted": 1, "objects_scheduled": len(object_keys)},
            )

    async def list_retention_candidates(
        self,
        *,
        tenant_id: UUID,
        deleted_before: datetime,
        limit: int,
    ) -> tuple[RetentionCandidate, ...]:
        del deleted_before
        if tenant_id != self._identity.tenant_id:
            return ()
        async with self._lock:
            return tuple(self._retention_candidates.values())[:limit]

    async def purge_conversation(
        self,
        conversation_id: UUID,
        *,
        tenant_id: UUID,
        deleted_before: datetime,
    ) -> bool:
        del deleted_before
        async with self._lock:
            if tenant_id != self._identity.tenant_id:
                return False
            candidate = self._retention_candidates.pop(conversation_id, None)
            if candidate is None:
                return False
            self._known_object_keys.difference_update(candidate.object_keys)
            return True

    async def purge_expired_attachment_metadata(
        self,
        *,
        tenant_id: UUID,
        deleted_before: datetime,
        limit: int,
    ) -> int:
        del tenant_id, deleted_before, limit
        return 0

    async def list_agent_retention_candidates(
        self,
        *,
        tenant_id: UUID,
        purge_before: datetime,
        limit: int,
    ) -> tuple[AgentRetentionCandidate, ...]:
        del purge_before
        if tenant_id != self._identity.tenant_id:
            return ()
        async with self._lock:
            return tuple(self._agent_retention_candidates.values())[:limit]

    async def purge_agent(
        self,
        agent_id: UUID,
        *,
        tenant_id: UUID,
        purge_before: datetime,
    ) -> bool:
        del purge_before
        async with self._lock:
            if tenant_id != self._identity.tenant_id:
                return False
            candidate = self._agent_retention_candidates.pop(agent_id, None)
            if candidate is None:
                return False
            self._known_object_keys.difference_update(candidate.object_keys)
            return True

    async def list_known_object_keys(self, *, tenant_id: UUID) -> frozenset[str]:
        if tenant_id != self._identity.tenant_id:
            return frozenset()
        async with self._lock:
            return frozenset(self._known_object_keys)

    def seed_user_export(
        self,
        user_id: UUID,
        *,
        data: dict[str, JsonValue],
        record_count: int,
        object_keys: tuple[str, ...] = (),
    ) -> None:
        self._exports[user_id] = ExportSnapshot(data=data, record_count=record_count)
        self._forget_objects[user_id] = object_keys
        self._known_object_keys.update(object_keys)

    def seed_retention_candidate(self, candidate: RetentionCandidate) -> None:
        self._retention_candidates[candidate.conversation_id] = candidate
        self._known_object_keys.update(candidate.object_keys)

    def seed_agent_retention_candidate(self, candidate: AgentRetentionCandidate) -> None:
        self._agent_retention_candidates[candidate.agent_id] = candidate
        self._known_object_keys.update(candidate.object_keys)

    def set_known_object_keys(self, object_keys: set[str]) -> None:
        self._known_object_keys = set(object_keys)


class SqlAlchemyDataLifecycleRepository:
    """PostgreSQL 数据导出白名单、批量脱敏和保留期删除实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def start_run(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        subject_user_id: UUID | None,
        kind: LifecycleRunKind,
    ) -> LifecycleRun:
        row = DataLifecycleRunModel(
            id=uuid4(),
            tenant_id=tenant_id,
            actor_id=actor_id,
            subject_user_id=subject_user_id,
            kind=kind.value,
            status=LifecycleRunStatus.RUNNING.value,
            counters={},
            evidence={},
            error_code=None,
            started_at=datetime.now(UTC),
            completed_at=None,
        )
        async with self._session_factory() as session, session.begin():
            session.add(row)
        return self._run(row)

    async def finish_run(
        self,
        run_id: UUID,
        *,
        tenant_id: UUID,
        status: LifecycleRunStatus,
        counters: dict[str, int],
        evidence: dict[str, JsonValue],
        error_code: str | None = None,
    ) -> LifecycleRun:
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(DataLifecycleRunModel)
                .where(
                    DataLifecycleRunModel.id == run_id,
                    DataLifecycleRunModel.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                raise DataLifecycleNotFoundError("生命周期运行记录不存在")
            row.status = status.value
            row.counters = counters
            row.evidence = evidence
            row.error_code = error_code
            row.completed_at = datetime.now(UTC)
            await session.flush()
            return self._run(row)

    async def list_runs(self, *, tenant_id: UUID, limit: int) -> tuple[LifecycleRun, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(DataLifecycleRunModel)
                    .where(DataLifecycleRunModel.tenant_id == tenant_id)
                    .order_by(DataLifecycleRunModel.started_at.desc())
                    .limit(limit)
                )
            ).all()
            return tuple(self._run(row) for row in rows)

    async def collect_user_export(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        max_records: int,
    ) -> ExportSnapshot:
        async with self._session_factory() as session:
            user = await session.scalar(
                select(User).where(User.tenant_id == tenant_id, User.id == user_id)
            )
            if user is None:
                raise DataLifecycleNotFoundError("用户不存在")
            record_count = 1

            async def rows[T](statement: Select[tuple[T]]) -> list[T]:
                nonlocal record_count
                remaining = max_records - record_count
                result = list((await session.scalars(statement.limit(remaining + 1))).all())
                record_count += len(result)
                if record_count > max_records:
                    raise DataLifecycleValidationError("用户数据超过已配置的导出记录上限")
                return result

            identities = await rows(
                select(ExternalIdentity).where(
                    ExternalIdentity.tenant_id == tenant_id,
                    ExternalIdentity.user_id == user_id,
                )
            )
            roles = await rows(
                select(RoleAssignment).where(
                    RoleAssignment.tenant_id == tenant_id,
                    RoleAssignment.user_id == user_id,
                )
            )
            membership = select(ConversationMember.conversation_id).where(
                ConversationMember.user_id == user_id
            )
            conversations = await rows(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == tenant_id,
                    or_(
                        ConversationModel.created_by == user_id,
                        ConversationModel.id.in_(membership),
                    ),
                )
                .order_by(ConversationModel.created_at)
            )
            conversation_ids = [item.id for item in conversations]
            messages = (
                await rows(
                    select(MessageModel)
                    .where(
                        MessageModel.tenant_id == tenant_id,
                        MessageModel.conversation_id.in_(conversation_ids),
                    )
                    .order_by(MessageModel.created_at)
                )
                if conversation_ids
                else []
            )
            message_ids = [item.id for item in messages]
            message_parts = (
                await rows(
                    select(MessagePartModel)
                    .where(
                        MessagePartModel.tenant_id == tenant_id,
                        MessagePartModel.message_id.in_(message_ids),
                    )
                    .order_by(MessagePartModel.message_id, MessagePartModel.position)
                )
                if message_ids
                else []
            )
            attachments = (
                await rows(
                    select(AttachmentModel)
                    .where(
                        AttachmentModel.tenant_id == tenant_id,
                        AttachmentModel.conversation_id.in_(conversation_ids),
                    )
                    .order_by(AttachmentModel.created_at)
                )
                if conversation_ids
                else []
            )
            feedback = (
                await rows(
                    select(MessageFeedbackModel)
                    .where(
                        MessageFeedbackModel.tenant_id == tenant_id,
                        MessageFeedbackModel.user_id == user_id,
                        MessageFeedbackModel.conversation_id.in_(conversation_ids),
                    )
                    .order_by(MessageFeedbackModel.created_at)
                )
                if conversation_ids
                else []
            )
            episodes = await rows(
                select(EpisodeModel)
                .where(EpisodeModel.tenant_id == tenant_id, EpisodeModel.user_id == user_id)
                .order_by(EpisodeModel.created_at)
            )
            memories = await rows(
                select(MemoryModel)
                .where(MemoryModel.tenant_id == tenant_id, MemoryModel.user_id == user_id)
                .order_by(MemoryModel.created_at)
            )
            memory_ids = [item.id for item in memories]
            sources = (
                await rows(
                    select(MemorySourceModel)
                    .where(
                        MemorySourceModel.tenant_id == tenant_id,
                        MemorySourceModel.memory_id.in_(memory_ids),
                    )
                    .order_by(MemorySourceModel.created_at)
                )
                if memory_ids
                else []
            )
            links = (
                await rows(
                    select(MemoryLinkModel)
                    .where(
                        MemoryLinkModel.tenant_id == tenant_id,
                        or_(
                            MemoryLinkModel.source_memory_id.in_(memory_ids),
                            MemoryLinkModel.target_memory_id.in_(memory_ids),
                        ),
                    )
                    .order_by(MemoryLinkModel.created_at)
                )
                if memory_ids
                else []
            )
            relationships = await rows(
                select(RelationshipModel)
                .where(
                    RelationshipModel.tenant_id == tenant_id,
                    RelationshipModel.user_id == user_id,
                )
                .order_by(RelationshipModel.created_at)
            )
            relationship_ids = [item.id for item in relationships]
            relationship_events = (
                await rows(
                    select(RelationshipEventModel)
                    .where(
                        RelationshipEventModel.tenant_id == tenant_id,
                        RelationshipEventModel.relationship_id.in_(relationship_ids),
                    )
                    .order_by(RelationshipEventModel.created_at)
                )
                if relationship_ids
                else []
            )
            scheduled_actions = await rows(
                select(ScheduledActionModel)
                .where(
                    ScheduledActionModel.tenant_id == tenant_id,
                    ScheduledActionModel.user_id == user_id,
                )
                .order_by(ScheduledActionModel.created_at)
            )
            data = cast(
                dict[str, JsonValue],
                {
                    "profile": {
                        "id": str(user.id),
                        "display_name": user.display_name,
                        "status": user.status,
                        "created_at": user.created_at.isoformat(),
                    },
                    "external_identities": [
                        {
                            "issuer": item.issuer,
                            "subject": item.subject,
                            "created_at": item.created_at.isoformat(),
                            "last_authenticated_at": item.last_authenticated_at.isoformat(),
                        }
                        for item in identities
                    ],
                    "roles": [
                        {
                            "role": item.role,
                            "source": item.source,
                            "created_at": item.created_at.isoformat(),
                        }
                        for item in roles
                    ],
                    "conversations": [
                        {
                            "id": str(item.id),
                            "agent_id": str(item.agent_id),
                            "title": item.title,
                            "status": item.status,
                            "created_at": item.created_at.isoformat(),
                            "updated_at": item.updated_at.isoformat(),
                            "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
                        }
                        for item in conversations
                    ],
                    "messages": [
                        {
                            "id": str(item.id),
                            "conversation_id": str(item.conversation_id),
                            "sender_type": item.sender_type,
                            "content": item.content,
                            "status": item.status,
                            "created_at": item.created_at.isoformat(),
                            "updated_at": item.updated_at.isoformat(),
                        }
                        for item in messages
                    ],
                    "message_parts": [
                        {
                            "id": str(item.id),
                            "message_id": str(item.message_id),
                            "position": item.position,
                            "kind": item.kind,
                            "text": item.text,
                            "attachment_id": (
                                str(item.attachment_id) if item.attachment_id else None
                            ),
                            "content_type": item.content_type,
                            "file_name": item.file_name,
                            "size_bytes": item.size_bytes,
                            "sha256": item.sha256,
                            "alt_text": item.alt_text,
                            "created_at": item.created_at.isoformat(),
                            "updated_at": item.updated_at.isoformat(),
                        }
                        for item in message_parts
                    ],
                    "attachments": [
                        {
                            "id": str(item.id),
                            "conversation_id": str(item.conversation_id),
                            "original_name": item.original_name,
                            "content_type": item.content_type,
                            "size_bytes": item.size_bytes,
                            "sha256": item.sha256,
                            "status": item.status,
                            "created_at": item.created_at.isoformat(),
                            "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
                        }
                        for item in attachments
                    ],
                    "feedback": [
                        {
                            "id": str(item.id),
                            "message_id": str(item.message_id),
                            "rating": item.rating,
                            "comment": item.comment,
                            "created_at": item.created_at.isoformat(),
                        }
                        for item in feedback
                    ],
                    "episodes": [
                        {
                            "id": str(item.id),
                            "conversation_id": str(item.conversation_id),
                            "title": item.title,
                            "summary": item.summary,
                            "status": item.status,
                            "started_at": item.started_at.isoformat(),
                            "ended_at": item.ended_at.isoformat() if item.ended_at else None,
                        }
                        for item in episodes
                    ],
                    "memories": [
                        {
                            "id": str(item.id),
                            "kind": item.kind,
                            "visibility": item.visibility,
                            "content": item.content,
                            "event_at": item.event_at.isoformat(),
                            "sensitivity": item.sensitivity,
                            "confirmation": item.confirmation,
                            "status": item.status,
                            "version": item.version,
                        }
                        for item in memories
                    ],
                    "memory_sources": [
                        {
                            "id": str(item.id),
                            "memory_id": str(item.memory_id),
                            "kind": item.kind,
                            "source_id": item.source_id,
                            "excerpt": item.excerpt,
                            "is_verbatim": item.is_verbatim,
                            "occurred_at": item.occurred_at.isoformat(),
                        }
                        for item in sources
                    ],
                    "memory_links": [
                        {
                            "id": str(item.id),
                            "source_memory_id": str(item.source_memory_id),
                            "target_memory_id": str(item.target_memory_id),
                            "kind": item.kind,
                            "note": item.note,
                            "created_at": item.created_at.isoformat(),
                        }
                        for item in links
                    ],
                    "relationships": [
                        {
                            "id": str(item.id),
                            "agent_id": str(item.agent_id),
                            "stage": item.stage,
                            "affinity": item.affinity,
                            "trust": item.trust,
                            "familiarity": item.familiarity,
                            "interaction_count": item.interaction_count,
                            "summary": item.summary,
                            "boundaries": item.boundaries,
                            "updated_at": item.updated_at.isoformat(),
                        }
                        for item in relationships
                    ],
                    "relationship_events": [
                        {
                            "id": str(item.id),
                            "relationship_id": str(item.relationship_id),
                            "event_type": item.event_type,
                            "summary": item.summary,
                            "created_at": item.created_at.isoformat(),
                        }
                        for item in relationship_events
                    ],
                    "scheduled_actions": [
                        {
                            "id": str(item.id),
                            "agent_id": str(item.agent_id),
                            "kind": item.kind,
                            "status": item.status,
                            "scheduled_for": item.scheduled_for.isoformat(),
                            "reason": item.reason,
                            "decision_reasons": item.decision_reasons,
                            "created_at": item.created_at.isoformat(),
                        }
                        for item in scheduled_actions
                    ],
                },
            )
            return ExportSnapshot(data=data, record_count=record_count)

    async def forget_user_data(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        actor_id: UUID,
    ) -> ForgetResult:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            user = await session.scalar(
                select(User)
                .where(User.tenant_id == tenant_id, User.id == user_id)
                .with_for_update()
            )
            if user is None:
                raise DataLifecycleNotFoundError("用户不存在")
            membership = select(ConversationMember.conversation_id).where(
                ConversationMember.user_id == user_id
            )
            conversation_ids = list(
                (
                    await session.scalars(
                        select(ConversationModel.id).where(
                            ConversationModel.tenant_id == tenant_id,
                            or_(
                                ConversationModel.created_by == user_id,
                                ConversationModel.id.in_(membership),
                            ),
                        )
                    )
                ).all()
            )
            attachment_condition = AttachmentModel.owner_id == user_id
            if conversation_ids:
                attachment_condition = or_(
                    attachment_condition,
                    AttachmentModel.conversation_id.in_(conversation_ids),
                )
            attachment_rows = list(
                (
                    await session.scalars(
                        select(AttachmentModel).where(
                            AttachmentModel.tenant_id == tenant_id,
                            attachment_condition,
                        )
                    )
                ).all()
            )
            object_keys = tuple(item.object_key for item in attachment_rows)
            for item in attachment_rows:
                item.original_name = "已清除"
                item.sha256 = "0" * 64
                item.status = "deleted"
                item.validation_error = "用户数据遗忘"
                item.deleted_at = now

            if conversation_ids:
                run_ids = list(
                    (
                        await session.scalars(
                            select(AgentRunModel.id).where(
                                AgentRunModel.tenant_id == tenant_id,
                                AgentRunModel.conversation_id.in_(conversation_ids),
                            )
                        )
                    ).all()
                )
                await session.execute(
                    delete(MessagePartModel).where(
                        MessagePartModel.tenant_id == tenant_id,
                        MessagePartModel.message_id.in_(
                            select(MessageModel.id).where(
                                MessageModel.tenant_id == tenant_id,
                                MessageModel.conversation_id.in_(conversation_ids),
                            )
                        ),
                    )
                )
                await session.execute(
                    update(MessageModel)
                    .where(
                        MessageModel.tenant_id == tenant_id,
                        MessageModel.conversation_id.in_(conversation_ids),
                    )
                    .values(content="", sender_id=None, client_message_id=None, updated_at=now)
                )
                await session.execute(
                    update(ConversationEventModel)
                    .where(
                        ConversationEventModel.tenant_id == tenant_id,
                        ConversationEventModel.conversation_id.in_(conversation_ids),
                    )
                    .values(payload={})
                )
                await session.execute(
                    update(MessageFeedbackModel)
                    .where(
                        MessageFeedbackModel.tenant_id == tenant_id,
                        MessageFeedbackModel.conversation_id.in_(conversation_ids),
                    )
                    .values(comment=None, updated_at=now)
                )
                await session.execute(
                    update(EpisodeModel)
                    .where(
                        EpisodeModel.tenant_id == tenant_id,
                        EpisodeModel.conversation_id.in_(conversation_ids),
                    )
                    .values(title="已遗忘情景", summary="", source_message_ids=[], updated_at=now)
                )
                if run_ids:
                    await session.execute(
                        update(RunStepModel)
                        .where(
                            RunStepModel.tenant_id == tenant_id,
                            RunStepModel.run_id.in_(run_ids),
                        )
                        .values(summary="已按用户遗忘请求清除", detail={})
                    )
                    await session.execute(
                        update(ActionCandidateModel)
                        .where(
                            ActionCandidateModel.tenant_id == tenant_id,
                            ActionCandidateModel.run_id.in_(run_ids),
                        )
                        .values(
                            reason_summary="已按用户遗忘请求清除",
                            parameters={},
                            rejection_reason=None,
                        )
                    )
                await session.execute(
                    update(ConversationModel)
                    .where(
                        ConversationModel.tenant_id == tenant_id,
                        ConversationModel.id.in_(conversation_ids),
                    )
                    .values(
                        title="已遗忘会话",
                        status="archived",
                        pinned_at=None,
                        archived_at=now,
                        deleted_at=now,
                        updated_at=now,
                    )
                )

            memory_ids = list(
                (
                    await session.scalars(
                        select(MemoryModel.id).where(
                            MemoryModel.tenant_id == tenant_id,
                            MemoryModel.user_id == user_id,
                        )
                    )
                ).all()
            )
            if memory_ids:
                await session.execute(
                    delete(MemoryEmbeddingModel).where(
                        MemoryEmbeddingModel.tenant_id == tenant_id,
                        MemoryEmbeddingModel.memory_id.in_(memory_ids),
                    )
                )
                await session.execute(
                    update(MemorySourceModel)
                    .where(
                        MemorySourceModel.tenant_id == tenant_id,
                        MemorySourceModel.memory_id.in_(memory_ids),
                    )
                    .values(excerpt=None, is_verbatim=False)
                )
                await session.execute(
                    update(MemoryLinkModel)
                    .where(
                        MemoryLinkModel.tenant_id == tenant_id,
                        or_(
                            MemoryLinkModel.source_memory_id.in_(memory_ids),
                            MemoryLinkModel.target_memory_id.in_(memory_ids),
                        ),
                    )
                    .values(note=None)
                )
                await session.execute(
                    update(MemoryModel)
                    .where(
                        MemoryModel.tenant_id == tenant_id,
                        MemoryModel.id.in_(memory_ids),
                    )
                    .values(
                        content=None, status="forgotten", embedding_version=None, updated_at=now
                    )
                )

            relationship_ids = list(
                (
                    await session.scalars(
                        select(RelationshipModel.id).where(
                            RelationshipModel.tenant_id == tenant_id,
                            RelationshipModel.user_id == user_id,
                        )
                    )
                ).all()
            )
            if relationship_ids:
                await session.execute(
                    delete(RelationshipEventModel).where(
                        RelationshipEventModel.tenant_id == tenant_id,
                        RelationshipEventModel.relationship_id.in_(relationship_ids),
                    )
                )
                await session.execute(
                    delete(RelationshipModel).where(
                        RelationshipModel.tenant_id == tenant_id,
                        RelationshipModel.id.in_(relationship_ids),
                    )
                )

            scheduled_job_ids = list(
                (
                    await session.scalars(
                        select(ScheduledActionModel.job_id).where(
                            ScheduledActionModel.tenant_id == tenant_id,
                            ScheduledActionModel.user_id == user_id,
                        )
                    )
                ).all()
            )
            await session.execute(
                delete(ScheduledActionModel).where(
                    ScheduledActionModel.tenant_id == tenant_id,
                    ScheduledActionModel.user_id == user_id,
                )
            )
            created_job_ids = list(
                (
                    await session.scalars(
                        select(BackgroundJobModel.id).where(
                            BackgroundJobModel.tenant_id == tenant_id,
                            BackgroundJobModel.created_by == user_id,
                        )
                    )
                ).all()
            )
            job_ids = tuple(set(scheduled_job_ids + created_job_ids))
            if job_ids:
                await session.execute(
                    update(BackgroundJobModel)
                    .where(
                        BackgroundJobModel.tenant_id == tenant_id,
                        BackgroundJobModel.id.in_(job_ids),
                    )
                    .values(payload={}, result_summary={}, last_error_summary=None)
                )
                await session.execute(
                    update(InboxEventModel)
                    .where(
                        InboxEventModel.tenant_id == tenant_id,
                        InboxEventModel.job_id.in_(job_ids),
                    )
                    .values(payload={})
                )
                await session.execute(
                    update(OutboxEventModel)
                    .where(
                        OutboxEventModel.tenant_id == tenant_id,
                        OutboxEventModel.job_id.in_(job_ids),
                    )
                    .values(payload={})
                )

            await session.execute(
                delete(AdminSession).where(
                    AdminSession.tenant_id == tenant_id,
                    AdminSession.user_id == user_id,
                )
            )
            await session.execute(
                delete(ExternalIdentity).where(
                    ExternalIdentity.tenant_id == tenant_id,
                    ExternalIdentity.user_id == user_id,
                )
            )
            await session.execute(
                delete(RoleAssignment).where(
                    RoleAssignment.tenant_id == tenant_id,
                    RoleAssignment.user_id == user_id,
                )
            )
            user.display_name = f"已遗忘用户-{str(user.id)[:8]}"
            user.status = "disabled"
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_id=actor_id if actor_id != user_id else None,
                    action="user.data_forgotten",
                    resource_type="user",
                    resource_id=str(user_id),
                    detail={
                        "conversations": len(conversation_ids),
                        "memories": len(memory_ids),
                        "objects": len(object_keys),
                    },
                )
            )
            return ForgetResult(
                object_keys=object_keys,
                counters={
                    "users_redacted": 1,
                    "conversations_redacted": len(conversation_ids),
                    "memories_forgotten": len(memory_ids),
                    "relationships_deleted": len(relationship_ids),
                    "objects_scheduled": len(object_keys),
                },
            )

    async def list_retention_candidates(
        self,
        *,
        tenant_id: UUID,
        deleted_before: datetime,
        limit: int,
    ) -> tuple[RetentionCandidate, ...]:
        async with self._session_factory() as session:
            conversations = (
                await session.scalars(
                    select(ConversationModel)
                    .where(
                        ConversationModel.tenant_id == tenant_id,
                        ConversationModel.deleted_at.is_not(None),
                        ConversationModel.deleted_at <= deleted_before,
                    )
                    .order_by(ConversationModel.deleted_at)
                    .limit(limit)
                )
            ).all()
            if not conversations:
                return ()
            ids = [item.id for item in conversations]
            attachments = (
                await session.scalars(
                    select(AttachmentModel).where(
                        AttachmentModel.tenant_id == tenant_id,
                        AttachmentModel.conversation_id.in_(ids),
                    )
                )
            ).all()
            keys_by_conversation: dict[UUID, list[str]] = {item.id: [] for item in conversations}
            for attachment in attachments:
                keys_by_conversation[attachment.conversation_id].append(attachment.object_key)
            return tuple(
                RetentionCandidate(
                    conversation_id=item.id,
                    object_keys=tuple(keys_by_conversation[item.id]),
                )
                for item in conversations
            )

    async def purge_conversation(
        self,
        conversation_id: UUID,
        *,
        tenant_id: UUID,
        deleted_before: datetime,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.deleted_at.is_not(None),
                    ConversationModel.deleted_at <= deleted_before,
                )
                .with_for_update()
            )
            if row is None:
                return False
            await session.delete(row)
            return True

    async def purge_expired_attachment_metadata(
        self,
        *,
        tenant_id: UUID,
        deleted_before: datetime,
        limit: int,
    ) -> int:
        async with self._session_factory() as session, session.begin():
            ids = list(
                (
                    await session.scalars(
                        select(AttachmentModel.id)
                        .where(
                            AttachmentModel.tenant_id == tenant_id,
                            AttachmentModel.status.in_(("deleted", "rejected")),
                            AttachmentModel.deleted_at.is_not(None),
                            AttachmentModel.deleted_at <= deleted_before,
                        )
                        .order_by(AttachmentModel.deleted_at)
                        .limit(limit)
                    )
                ).all()
            )
            if not ids:
                return 0
            await session.execute(delete(AttachmentModel).where(AttachmentModel.id.in_(ids)))
            return len(ids)

    async def list_agent_retention_candidates(
        self,
        *,
        tenant_id: UUID,
        purge_before: datetime,
        limit: int,
    ) -> tuple[AgentRetentionCandidate, ...]:
        async with self._session_factory() as session:
            agents = (
                await session.scalars(
                    select(Agent)
                    .where(
                        Agent.tenant_id == tenant_id,
                        Agent.status == "deleted",
                        Agent.purge_after.is_not(None),
                        Agent.purge_after <= purge_before,
                    )
                    .order_by(Agent.purge_after, Agent.id)
                    .limit(limit)
                )
            ).all()
            if not agents:
                return ()
            agent_ids = [item.id for item in agents]
            conversations = (
                await session.scalars(
                    select(ConversationModel).where(
                        ConversationModel.tenant_id == tenant_id,
                        ConversationModel.agent_id.in_(agent_ids),
                    )
                )
            ).all()
            conversation_ids = [item.id for item in conversations]
            attachments: Sequence[AttachmentModel] = ()
            if conversation_ids:
                attachments = (
                    await session.scalars(
                        select(AttachmentModel).where(
                            AttachmentModel.tenant_id == tenant_id,
                            AttachmentModel.conversation_id.in_(conversation_ids),
                        )
                    )
                ).all()
            conversation_agent = {item.id: item.agent_id for item in conversations}
            keys_by_agent: dict[UUID, list[str]] = {item.id: [] for item in agents}
            for attachment in attachments:
                agent_id = conversation_agent.get(attachment.conversation_id)
                if agent_id is not None:
                    keys_by_agent[agent_id].append(attachment.object_key)
            return tuple(
                AgentRetentionCandidate(
                    agent_id=item.id,
                    object_keys=tuple(keys_by_agent[item.id]),
                )
                for item in agents
            )

    async def purge_agent(
        self,
        agent_id: UUID,
        *,
        tenant_id: UUID,
        purge_before: datetime,
    ) -> bool:
        """仅清理已过保留期 Agent；管理 API 不暴露这一物理操作。"""
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(Agent)
                .where(
                    Agent.id == agent_id,
                    Agent.tenant_id == tenant_id,
                    Agent.status == "deleted",
                    Agent.purge_after.is_not(None),
                    Agent.purge_after <= purge_before,
                )
                .with_for_update()
            )
            if row is None:
                return False
            channel_ids = list(
                (
                    await session.scalars(
                        select(ChannelInstanceModel.id).where(
                            ChannelInstanceModel.tenant_id == tenant_id,
                            ChannelInstanceModel.agent_id == agent_id,
                        )
                    )
                ).all()
            )
            secret_ids = list(
                (
                    await session.scalars(
                        select(SecretReference.id).where(
                            or_(
                                and_(
                                    SecretReference.scope_type == "agent",
                                    SecretReference.scope_id == agent_id,
                                ),
                                and_(
                                    SecretReference.scope_type == "channel",
                                    SecretReference.scope_id.in_(channel_ids),
                                ),
                            )
                            if channel_ids
                            else and_(
                                SecretReference.scope_type == "agent",
                                SecretReference.scope_id == agent_id,
                            )
                        )
                    )
                ).all()
            )
            if secret_ids:
                await session.execute(
                    delete(ConfigurationValue).where(
                        ConfigurationValue.secret_reference_id.in_(secret_ids)
                    )
                )
                await session.execute(
                    delete(SecretReference).where(SecretReference.id.in_(secret_ids))
                )
            await session.execute(
                delete(ConversationModel).where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.agent_id == agent_id,
                )
            )
            await session.flush()
            await session.delete(row)
            return True

    async def list_known_object_keys(self, *, tenant_id: UUID) -> frozenset[str]:
        async with self._session_factory() as session:
            keys = (
                await session.scalars(
                    select(AttachmentModel.object_key).where(
                        AttachmentModel.tenant_id == tenant_id,
                        AttachmentModel.status.not_in(("deleted", "rejected")),
                    )
                )
            ).all()
            return frozenset(keys)

    @staticmethod
    def _run(row: DataLifecycleRunModel) -> LifecycleRun:
        return LifecycleRun(
            id=row.id,
            tenant_id=row.tenant_id,
            actor_id=row.actor_id,
            subject_user_id=row.subject_user_id,
            kind=LifecycleRunKind(row.kind),
            status=LifecycleRunStatus(row.status),
            counters=cast(dict[str, int], row.counters),
            evidence=cast(dict[str, JsonValue], row.evidence),
            error_code=row.error_code,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )


__all__ = ["MemoryDataLifecycleRepository", "SqlAlchemyDataLifecycleRepository"]
