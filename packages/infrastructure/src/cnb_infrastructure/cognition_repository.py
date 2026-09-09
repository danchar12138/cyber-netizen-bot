"""认知资源、人格状态与安全运行回放的内存及 PostgreSQL 仓储。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import CognitionResourceNotFoundError, CognitionValidationError
from cnb_domain import (
    ActionCandidateRecord,
    CognitionResourceKind,
    CognitionResourceVersion,
    CognitionVersionStatus,
    CognitiveRunTrace,
    InvocationStatus,
    JsonValue,
    ModelInvocationRecord,
    PersonaStateSnapshot,
    RunStepRecord,
)
from cnb_infrastructure.models import (
    ActionCandidateModel,
    AgentRunModel,
    AuditLog,
    CognitionResourceVersionModel,
    ModelInvocationModel,
    PersonaStateSnapshotModel,
    RunStepModel,
)


class MemoryCognitionRepository:
    """供测试和无数据库联调使用的并发安全认知仓储。"""

    def __init__(self) -> None:
        self._resources: dict[UUID, CognitionResourceVersion] = {}
        self._persona_states: dict[UUID, PersonaStateSnapshot] = {}
        self._steps: dict[UUID, tuple[RunStepRecord, ...]] = {}
        self._candidates: dict[UUID, tuple[ActionCandidateRecord, ...]] = {}
        self._invocations: dict[UUID, list[ModelInvocationRecord]] = {}
        self._lock = asyncio.Lock()

    async def list_resource_versions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        kind: CognitionResourceKind | None,
    ) -> tuple[CognitionResourceVersion, ...]:
        async with self._lock:
            rows = (
                item
                for item in self._resources.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (kind is None or item.kind is kind)
            )
            return tuple(
                sorted(
                    rows, key=lambda item: (item.kind.value, item.key, item.version), reverse=True
                )
            )

    async def create_resource_draft(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        kind: CognitionResourceKind,
        key: str,
        name: str,
        payload: dict[str, JsonValue],
        note: str | None,
        actor_id: UUID,
    ) -> CognitionResourceVersion:
        async with self._lock:
            version = 1 + max(
                (
                    item.version
                    for item in self._resources.values()
                    if item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.kind is kind
                    and item.key == key
                ),
                default=0,
            )
            resource = CognitionResourceVersion(
                id=uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                kind=kind,
                key=key,
                name=name,
                version=version,
                status=CognitionVersionStatus.DRAFT,
                payload=payload,
                note=note,
                created_by=actor_id,
                created_at=datetime.now(UTC),
                published_at=None,
            )
            self._resources[resource.id] = resource
            return resource

    async def publish_resource(
        self,
        *,
        resource_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
    ) -> CognitionResourceVersion:
        del actor_id
        async with self._lock:
            resource = self._owned_resource(resource_id, tenant_id, agent_id)
            if resource.status is not CognitionVersionStatus.DRAFT:
                raise CognitionValidationError("只有草稿版本可以直接发布")
            for item_id, item in tuple(self._resources.items()):
                if (
                    item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.kind is resource.kind
                    and item.key == resource.key
                    and item.status is CognitionVersionStatus.PUBLISHED
                ):
                    self._resources[item_id] = replace(
                        item, status=CognitionVersionStatus.SUPERSEDED
                    )
            published = replace(
                resource,
                status=CognitionVersionStatus.PUBLISHED,
                published_at=datetime.now(UTC),
            )
            self._resources[published.id] = published
            return published

    async def rollback_resource(
        self,
        *,
        resource_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
    ) -> CognitionResourceVersion:
        async with self._lock:
            source = self._owned_resource(resource_id, tenant_id, agent_id)
            next_version = 1 + max(
                item.version
                for item in self._resources.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and item.kind is source.kind
                and item.key == source.key
            )
            for item_id, item in tuple(self._resources.items()):
                if (
                    item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.kind is source.kind
                    and item.key == source.key
                    and item.status is CognitionVersionStatus.PUBLISHED
                ):
                    self._resources[item_id] = replace(
                        item, status=CognitionVersionStatus.SUPERSEDED
                    )
            rolled_back = replace(
                source,
                id=uuid4(),
                version=next_version,
                status=CognitionVersionStatus.PUBLISHED,
                note=f"回滚自 v{source.version}",
                created_by=actor_id,
                created_at=datetime.now(UTC),
                published_at=datetime.now(UTC),
            )
            self._resources[rolled_back.id] = rolled_back
            return rolled_back

    async def get_published_resource(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        kind: CognitionResourceKind,
        key: str,
    ) -> CognitionResourceVersion | None:
        async with self._lock:
            return next(
                (
                    item
                    for item in self._resources.values()
                    if item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.kind is kind
                    and item.key == key
                    and item.status is CognitionVersionStatus.PUBLISHED
                ),
                None,
            )

    async def get_resource_version(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        kind: CognitionResourceKind,
        key: str,
        version: int,
    ) -> CognitionResourceVersion | None:
        async with self._lock:
            return next(
                (
                    item
                    for item in self._resources.values()
                    if item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.kind is kind
                    and item.key == key
                    and item.version == version
                ),
                None,
            )

    async def get_latest_persona_state(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        conversation_id: UUID,
    ) -> PersonaStateSnapshot | None:
        async with self._lock:
            matches = [
                item
                for item in self._persona_states.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and item.conversation_id == conversation_id
            ]
            return max(matches, key=lambda item: item.created_at, default=None)

    async def save_cognitive_run(
        self,
        *,
        persona_state: PersonaStateSnapshot,
        steps: tuple[RunStepRecord, ...],
        candidates: tuple[ActionCandidateRecord, ...],
    ) -> None:
        async with self._lock:
            self._persona_states[persona_state.run_id] = persona_state
            self._steps[persona_state.run_id] = steps
            self._candidates[persona_state.run_id] = candidates

    async def save_model_invocation(self, invocation: ModelInvocationRecord) -> None:
        async with self._lock:
            self._invocations.setdefault(invocation.run_id, []).append(invocation)

    async def get_run_trace(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
    ) -> CognitiveRunTrace | None:
        async with self._lock:
            state = self._persona_states.get(run_id)
            if state is not None and state.tenant_id != tenant_id:
                state = None
            steps = tuple(
                item for item in self._steps.get(run_id, ()) if item.tenant_id == tenant_id
            )
            candidates = tuple(
                item for item in self._candidates.get(run_id, ()) if item.tenant_id == tenant_id
            )
            invocations = tuple(
                item for item in self._invocations.get(run_id, ()) if item.tenant_id == tenant_id
            )
            if state is None and not steps and not candidates and not invocations:
                return None
            return CognitiveRunTrace(
                run_id=run_id,
                persona_state=state,
                steps=steps,
                candidates=candidates,
                model_invocations=invocations,
            )

    def _owned_resource(
        self, resource_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> CognitionResourceVersion:
        resource = self._resources.get(resource_id)
        if resource is None or resource.tenant_id != tenant_id or resource.agent_id != agent_id:
            raise CognitionResourceNotFoundError(f"认知资源版本不存在：{resource_id}")
        return resource


class SqlAlchemyCognitionRepository:
    """使用 PostgreSQL 提供租户隔离、不可变发布与认知回放。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_resource_versions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        kind: CognitionResourceKind | None,
    ) -> tuple[CognitionResourceVersion, ...]:
        statement = select(CognitionResourceVersionModel).where(
            CognitionResourceVersionModel.tenant_id == tenant_id,
            CognitionResourceVersionModel.agent_id == agent_id,
        )
        if kind is not None:
            statement = statement.where(CognitionResourceVersionModel.kind == kind.value)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(
                        CognitionResourceVersionModel.kind,
                        CognitionResourceVersionModel.key,
                        CognitionResourceVersionModel.version.desc(),
                    )
                )
            ).all()
            return tuple(self._resource(row) for row in rows)

    async def create_resource_draft(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        kind: CognitionResourceKind,
        key: str,
        name: str,
        payload: dict[str, JsonValue],
        note: str | None,
        actor_id: UUID,
    ) -> CognitionResourceVersion:
        async with self._session_factory() as session, session.begin():
            latest = await session.scalar(
                select(func.max(CognitionResourceVersionModel.version)).where(
                    CognitionResourceVersionModel.tenant_id == tenant_id,
                    CognitionResourceVersionModel.agent_id == agent_id,
                    CognitionResourceVersionModel.kind == kind.value,
                    CognitionResourceVersionModel.key == key,
                )
            )
            row = CognitionResourceVersionModel(
                id=uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                kind=kind.value,
                key=key,
                name=name,
                version=(latest or 0) + 1,
                status=CognitionVersionStatus.DRAFT.value,
                payload=payload,
                note=note,
                created_by=actor_id,
            )
            session.add(row)
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="cognition.resource_draft_created",
                row=row,
            )
            await session.flush()
            return self._resource(row)

    async def publish_resource(
        self,
        *,
        resource_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
    ) -> CognitionResourceVersion:
        async with self._session_factory() as session, session.begin():
            row = await self._owned_resource(
                session, resource_id=resource_id, tenant_id=tenant_id, agent_id=agent_id
            )
            if row.status != CognitionVersionStatus.DRAFT.value:
                raise CognitionValidationError("只有草稿版本可以直接发布")
            await session.execute(
                update(CognitionResourceVersionModel)
                .where(
                    CognitionResourceVersionModel.tenant_id == tenant_id,
                    CognitionResourceVersionModel.agent_id == agent_id,
                    CognitionResourceVersionModel.kind == row.kind,
                    CognitionResourceVersionModel.key == row.key,
                    CognitionResourceVersionModel.status == CognitionVersionStatus.PUBLISHED.value,
                )
                .values(status=CognitionVersionStatus.SUPERSEDED.value)
            )
            row.status = CognitionVersionStatus.PUBLISHED.value
            row.published_at = datetime.now(UTC)
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="cognition.resource_published",
                row=row,
            )
            await session.flush()
            return self._resource(row)

    async def rollback_resource(
        self,
        *,
        resource_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
    ) -> CognitionResourceVersion:
        async with self._session_factory() as session, session.begin():
            source = await self._owned_resource(
                session, resource_id=resource_id, tenant_id=tenant_id, agent_id=agent_id
            )
            latest = await session.scalar(
                select(func.max(CognitionResourceVersionModel.version)).where(
                    CognitionResourceVersionModel.tenant_id == tenant_id,
                    CognitionResourceVersionModel.agent_id == agent_id,
                    CognitionResourceVersionModel.kind == source.kind,
                    CognitionResourceVersionModel.key == source.key,
                )
            )
            await session.execute(
                update(CognitionResourceVersionModel)
                .where(
                    CognitionResourceVersionModel.tenant_id == tenant_id,
                    CognitionResourceVersionModel.agent_id == agent_id,
                    CognitionResourceVersionModel.kind == source.kind,
                    CognitionResourceVersionModel.key == source.key,
                    CognitionResourceVersionModel.status == CognitionVersionStatus.PUBLISHED.value,
                )
                .values(status=CognitionVersionStatus.SUPERSEDED.value)
            )
            row = CognitionResourceVersionModel(
                id=uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                kind=source.kind,
                key=source.key,
                name=source.name,
                version=(latest or 0) + 1,
                status=CognitionVersionStatus.PUBLISHED.value,
                payload=source.payload,
                note=f"回滚自 v{source.version}",
                created_by=actor_id,
                published_at=datetime.now(UTC),
            )
            session.add(row)
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="cognition.resource_rolled_back",
                row=row,
            )
            await session.flush()
            return self._resource(row)

    async def get_published_resource(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        kind: CognitionResourceKind,
        key: str,
    ) -> CognitionResourceVersion | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(CognitionResourceVersionModel).where(
                    CognitionResourceVersionModel.tenant_id == tenant_id,
                    CognitionResourceVersionModel.agent_id == agent_id,
                    CognitionResourceVersionModel.kind == kind.value,
                    CognitionResourceVersionModel.key == key,
                    CognitionResourceVersionModel.status == CognitionVersionStatus.PUBLISHED.value,
                )
            )
            return self._resource(row) if row else None

    async def get_resource_version(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        kind: CognitionResourceKind,
        key: str,
        version: int,
    ) -> CognitionResourceVersion | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(CognitionResourceVersionModel).where(
                    CognitionResourceVersionModel.tenant_id == tenant_id,
                    CognitionResourceVersionModel.agent_id == agent_id,
                    CognitionResourceVersionModel.kind == kind.value,
                    CognitionResourceVersionModel.key == key,
                    CognitionResourceVersionModel.version == version,
                )
            )
            return self._resource(row) if row else None

    async def get_latest_persona_state(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        conversation_id: UUID,
    ) -> PersonaStateSnapshot | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(PersonaStateSnapshotModel)
                .where(
                    PersonaStateSnapshotModel.tenant_id == tenant_id,
                    PersonaStateSnapshotModel.agent_id == agent_id,
                    PersonaStateSnapshotModel.conversation_id == conversation_id,
                )
                .order_by(PersonaStateSnapshotModel.created_at.desc())
                .limit(1)
            )
            return self._persona_state(row) if row else None

    async def save_cognitive_run(
        self,
        *,
        persona_state: PersonaStateSnapshot,
        steps: tuple[RunStepRecord, ...],
        candidates: tuple[ActionCandidateRecord, ...],
    ) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                PersonaStateSnapshotModel(
                    id=persona_state.id,
                    tenant_id=persona_state.tenant_id,
                    agent_id=persona_state.agent_id,
                    conversation_id=persona_state.conversation_id,
                    run_id=persona_state.run_id,
                    persona_version=persona_state.persona_version,
                    valence=persona_state.valence,
                    arousal=persona_state.arousal,
                    social_energy=persona_state.social_energy,
                    created_at=persona_state.created_at,
                )
            )
            session.add_all(
                RunStepModel(
                    id=item.id,
                    tenant_id=item.tenant_id,
                    run_id=item.run_id,
                    sequence=item.sequence,
                    stage=item.stage,
                    summary=item.summary,
                    detail=item.detail,
                    created_at=item.created_at,
                )
                for item in steps
            )
            session.add_all(
                ActionCandidateModel(
                    id=item.id,
                    tenant_id=item.tenant_id,
                    run_id=item.run_id,
                    sequence=item.sequence,
                    action=item.action,
                    confidence=item.confidence,
                    reason_summary=item.reason_summary,
                    parameters=item.parameters,
                    tool_name=item.tool_name,
                    risk_level=item.risk_level,
                    selected=item.selected,
                    rejection_reason=item.rejection_reason,
                    created_at=item.created_at,
                )
                for item in candidates
            )

    async def save_model_invocation(self, invocation: ModelInvocationRecord) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                ModelInvocationModel(
                    id=invocation.id,
                    tenant_id=invocation.tenant_id,
                    run_id=invocation.run_id,
                    purpose=invocation.purpose,
                    provider=invocation.provider,
                    model=invocation.model,
                    attempt=invocation.attempt,
                    status=invocation.status.value,
                    input_tokens=invocation.input_tokens,
                    output_tokens=invocation.output_tokens,
                    latency_ms=invocation.latency_ms,
                    error_code=invocation.error_code,
                    created_at=invocation.created_at,
                    completed_at=invocation.completed_at,
                )
            )

    async def get_run_trace(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
    ) -> CognitiveRunTrace | None:
        async with self._session_factory() as session:
            run_exists = await session.scalar(
                select(AgentRunModel.id).where(
                    AgentRunModel.id == run_id,
                    AgentRunModel.tenant_id == tenant_id,
                )
            )
            if run_exists is None:
                return None
            state = await session.scalar(
                select(PersonaStateSnapshotModel).where(
                    PersonaStateSnapshotModel.run_id == run_id,
                    PersonaStateSnapshotModel.tenant_id == tenant_id,
                )
            )
            steps = (
                await session.scalars(
                    select(RunStepModel)
                    .where(RunStepModel.run_id == run_id, RunStepModel.tenant_id == tenant_id)
                    .order_by(RunStepModel.sequence)
                )
            ).all()
            candidates = (
                await session.scalars(
                    select(ActionCandidateModel)
                    .where(
                        ActionCandidateModel.run_id == run_id,
                        ActionCandidateModel.tenant_id == tenant_id,
                    )
                    .order_by(ActionCandidateModel.sequence)
                )
            ).all()
            invocations = (
                await session.scalars(
                    select(ModelInvocationModel)
                    .where(
                        ModelInvocationModel.run_id == run_id,
                        ModelInvocationModel.tenant_id == tenant_id,
                    )
                    .order_by(ModelInvocationModel.attempt)
                )
            ).all()
            return CognitiveRunTrace(
                run_id=run_id,
                persona_state=self._persona_state(state) if state else None,
                steps=tuple(self._step(item) for item in steps),
                candidates=tuple(self._candidate(item) for item in candidates),
                model_invocations=tuple(self._invocation(item) for item in invocations),
            )

    @staticmethod
    async def _owned_resource(
        session: AsyncSession,
        *,
        resource_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> CognitionResourceVersionModel:
        row = await session.scalar(
            select(CognitionResourceVersionModel)
            .where(
                CognitionResourceVersionModel.id == resource_id,
                CognitionResourceVersionModel.tenant_id == tenant_id,
                CognitionResourceVersionModel.agent_id == agent_id,
            )
            .with_for_update()
        )
        if row is None:
            raise CognitionResourceNotFoundError(f"认知资源版本不存在：{resource_id}")
        return row

    @staticmethod
    def _audit(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        row: CognitionResourceVersionModel,
    ) -> None:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type=row.kind,
                resource_id=str(row.id),
                detail={"key": row.key, "version": row.version},
            )
        )

    @staticmethod
    def _resource(row: CognitionResourceVersionModel) -> CognitionResourceVersion:
        return CognitionResourceVersion(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            kind=CognitionResourceKind(row.kind),
            key=row.key,
            name=row.name,
            version=row.version,
            status=CognitionVersionStatus(row.status),
            payload=cast(dict[str, JsonValue], row.payload),
            note=row.note,
            created_by=row.created_by,
            created_at=row.created_at,
            published_at=row.published_at,
        )

    @staticmethod
    def _persona_state(row: PersonaStateSnapshotModel) -> PersonaStateSnapshot:
        return PersonaStateSnapshot(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            conversation_id=row.conversation_id,
            run_id=row.run_id,
            persona_version=row.persona_version,
            valence=row.valence,
            arousal=row.arousal,
            social_energy=row.social_energy,
            created_at=row.created_at,
        )

    @staticmethod
    def _step(row: RunStepModel) -> RunStepRecord:
        return RunStepRecord(
            id=row.id,
            tenant_id=row.tenant_id,
            run_id=row.run_id,
            sequence=row.sequence,
            stage=row.stage,
            summary=row.summary,
            detail=cast(dict[str, JsonValue], row.detail),
            created_at=row.created_at,
        )

    @staticmethod
    def _candidate(row: ActionCandidateModel) -> ActionCandidateRecord:
        return ActionCandidateRecord(
            id=row.id,
            tenant_id=row.tenant_id,
            run_id=row.run_id,
            sequence=row.sequence,
            action=row.action,
            confidence=row.confidence,
            reason_summary=row.reason_summary,
            parameters=cast(dict[str, JsonValue], row.parameters),
            tool_name=row.tool_name,
            risk_level=row.risk_level,
            selected=row.selected,
            rejection_reason=row.rejection_reason,
            created_at=row.created_at,
        )

    @staticmethod
    def _invocation(row: ModelInvocationModel) -> ModelInvocationRecord:
        return ModelInvocationRecord(
            id=row.id,
            tenant_id=row.tenant_id,
            run_id=row.run_id,
            purpose=row.purpose,
            provider=row.provider,
            model=row.model,
            attempt=row.attempt,
            status=InvocationStatus(row.status),
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            latency_ms=row.latency_ms,
            error_code=row.error_code,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )
