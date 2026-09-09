"""长期记忆、关系治理、混合召回与索引重建应用服务。"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from cnb_cognition import (
    DeterministicHashEmbedding,
    EmbeddingEncoder,
    HybridMemoryRanker,
    HybridRecallWeights,
)
from cnb_domain import (
    Episode,
    EpisodeStatus,
    Memory,
    MemoryConfirmation,
    MemoryDetail,
    MemoryEmbedding,
    MemoryIndexJob,
    MemoryIndexJobStatus,
    MemoryKind,
    MemoryLink,
    MemoryLinkKind,
    MemoryRecall,
    MemorySensitivity,
    MemorySource,
    MemorySourceKind,
    MemoryStatus,
    MemoryVisibility,
    RawMemoryCandidate,
    Relationship,
    RelationshipDetail,
    RelationshipEvent,
    RelationshipStage,
)


class MemoryNotFoundError(LookupError):
    """目标记忆、Episode 或关系不属于当前作用域时抛出。"""


class MemoryValidationError(ValueError):
    """记忆内容、来源、关系变化或召回参数不符合约束时抛出。"""


class MemoryConflictError(RuntimeError):
    """目标记忆的生命周期不允许当前操作时抛出。"""


@dataclass(frozen=True, slots=True)
class MemorySourceDraft:
    """创建记忆时必须提交的来源证据。"""

    kind: MemorySourceKind
    source_id: str
    excerpt: str | None
    is_verbatim: bool
    occurred_at: datetime


class MemoryRepository(Protocol):
    """长期记忆与关系的租户隔离持久化端口。"""

    async def list_episodes(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
        limit: int,
    ) -> tuple[Episode, ...]: ...

    async def create_episode(self, episode: Episode) -> Episode: ...

    async def set_episode_status(
        self,
        *,
        episode_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        status: EpisodeStatus,
        ended_at: datetime | None,
        actor_id: UUID,
    ) -> Episode | None: ...

    async def list_memories(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
        status: MemoryStatus | None,
        kind: MemoryKind | None,
        query: str | None,
        limit: int,
    ) -> tuple[Memory, ...]: ...

    async def get_memory_detail(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> MemoryDetail | None: ...

    async def create_memory(
        self,
        *,
        memory: Memory,
        sources: tuple[MemorySource, ...],
        embedding: MemoryEmbedding,
    ) -> MemoryDetail: ...

    async def set_confirmation(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        confirmation: MemoryConfirmation,
        confidence: float,
        updated_at: datetime,
        actor_id: UUID,
    ) -> Memory | None: ...

    async def correct_memory(
        self,
        *,
        source_memory_id: UUID,
        replacement: Memory,
        correction_source: MemorySource,
        replacement_embedding: MemoryEmbedding,
        link: MemoryLink,
    ) -> MemoryDetail | None: ...

    async def forget_memory(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        updated_at: datetime,
        actor_id: UUID,
    ) -> Memory | None: ...

    async def create_memory_link(self, link: MemoryLink) -> MemoryLink: ...

    async def search_candidates(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        query: str,
        query_embedding: tuple[float, ...],
        embedding_version: str,
        maximum_sensitivity: MemorySensitivity,
        limit: int,
    ) -> tuple[RawMemoryCandidate, ...]: ...

    async def get_relationship_detail(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
    ) -> RelationshipDetail | None: ...

    async def apply_relationship_event(
        self,
        *,
        relationship: Relationship,
        event: RelationshipEvent,
    ) -> RelationshipDetail: ...

    async def list_memories_for_embedding(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
    ) -> tuple[Memory, ...]: ...

    async def create_index_job(self, job: MemoryIndexJob) -> MemoryIndexJob: ...

    async def get_index_job(
        self, *, tenant_id: UUID, agent_id: UUID, job_id: UUID
    ) -> MemoryIndexJob | None: ...

    async def update_index_job(self, job: MemoryIndexJob) -> MemoryIndexJob: ...

    async def replace_embedding(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        embedding: MemoryEmbedding,
        updated_at: datetime,
    ) -> None: ...

    async def list_index_jobs(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        limit: int,
    ) -> tuple[MemoryIndexJob, ...]: ...


class MemoryService:
    """以来源可追溯、用户隔离和不可变纠正链为核心治理长期记忆。"""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        embedding_encoder: EmbeddingEncoder | None = None,
        ranker: HybridMemoryRanker | None = None,
    ) -> None:
        self._repository = repository
        self._embedding_encoder = embedding_encoder or DeterministicHashEmbedding()
        self._ranker = ranker or HybridMemoryRanker()

    @property
    def embedding_version(self) -> str:
        return self._embedding_encoder.version

    async def create_episode(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        title: str,
        summary: str,
        started_at: datetime,
        ended_at: datetime | None,
        source_message_ids: tuple[UUID, ...],
        actor_id: UUID,
        entity_id: UUID | None = None,
    ) -> Episode:
        normalized_title = self._required_text(title, "Episode 标题", maximum=200)
        normalized_summary = self._required_text(summary, "Episode 摘要", maximum=4000)
        self._aware(started_at, "Episode 开始时间")
        if ended_at is not None:
            self._aware(ended_at, "Episode 结束时间")
            if ended_at < started_at:
                raise MemoryValidationError("Episode 结束时间不能早于开始时间")
        if not source_message_ids:
            raise MemoryValidationError("Episode 至少需要一个来源消息")
        now = datetime.now(UTC)
        return await self._repository.create_episode(
            Episode(
                id=entity_id or uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                conversation_id=conversation_id,
                title=normalized_title,
                summary=normalized_summary,
                status=EpisodeStatus.CLOSED if ended_at else EpisodeStatus.OPEN,
                started_at=started_at,
                ended_at=ended_at,
                source_message_ids=source_message_ids,
                created_by=actor_id,
                created_at=now,
                updated_at=now,
            )
        )

    async def list_episodes(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
        limit: int,
    ) -> tuple[Episode, ...]:
        return await self._repository.list_episodes(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            limit=self._limit(limit),
        )

    async def close_episode(
        self,
        *,
        episode_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        consolidate: bool,
        actor_id: UUID,
    ) -> Episode:
        result = await self._repository.set_episode_status(
            episode_id=episode_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=(EpisodeStatus.CONSOLIDATED if consolidate else EpisodeStatus.CLOSED),
            ended_at=datetime.now(UTC),
            actor_id=actor_id,
        )
        if result is None:
            raise MemoryNotFoundError(f"Episode 不存在：{episode_id}")
        return result

    async def create_memory(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
        conversation_id: UUID | None,
        episode_id: UUID | None,
        kind: MemoryKind,
        visibility: MemoryVisibility,
        content: str,
        event_at: datetime,
        confidence: float,
        importance: float,
        emotional_weight: float,
        sensitivity: MemorySensitivity,
        confirmation: MemoryConfirmation,
        sources: tuple[MemorySourceDraft, ...],
        actor_id: UUID,
        entity_id: UUID | None = None,
    ) -> MemoryDetail:
        normalized = self._required_text(content, "记忆内容", maximum=8000)
        self._validate_memory_values(
            visibility=visibility,
            user_id=user_id,
            event_at=event_at,
            confidence=confidence,
            importance=importance,
            emotional_weight=emotional_weight,
            sources=sources,
        )
        now = datetime.now(UTC)
        memory_id = entity_id or uuid4()
        memory = Memory(
            id=memory_id,
            lineage_id=memory_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            conversation_id=conversation_id,
            episode_id=episode_id,
            kind=kind,
            visibility=visibility,
            content=normalized,
            event_at=event_at,
            confidence=confidence,
            importance=importance,
            emotional_weight=emotional_weight,
            sensitivity=sensitivity,
            confirmation=confirmation,
            status=MemoryStatus.ACTIVE,
            version=1,
            embedding_version=self._embedding_encoder.version,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        source_records = tuple(
            self._source_record(
                draft,
                tenant_id=tenant_id,
                memory_id=memory_id,
                created_at=now,
                record_id=uuid5(memory_id, f"source:{index}"),
            )
            for index, draft in enumerate(sources)
        )
        embedding = self._embedding(memory, normalized, now=now)
        return await self._repository.create_memory(
            memory=memory,
            sources=source_records,
            embedding=embedding,
        )

    async def list_memories(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
        status: MemoryStatus | None,
        kind: MemoryKind | None,
        query: str | None,
        limit: int,
    ) -> tuple[Memory, ...]:
        return await self._repository.list_memories(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            status=status,
            kind=kind,
            query=query.strip() if query and query.strip() else None,
            limit=self._limit(limit),
        )

    async def get_memory_detail(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> MemoryDetail:
        detail = await self._repository.get_memory_detail(
            memory_id=memory_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        if detail is None:
            raise MemoryNotFoundError(f"记忆不存在：{memory_id}")
        return detail

    async def set_confirmation(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        confirmation: MemoryConfirmation,
        actor_id: UUID,
    ) -> Memory:
        detail = await self.get_memory_detail(
            memory_id=memory_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        if detail.memory.status is not MemoryStatus.ACTIVE:
            raise MemoryConflictError("只有生效记忆可以确认或标记争议")
        confidence = (
            max(0.85, detail.memory.confidence)
            if confirmation is MemoryConfirmation.CONFIRMED
            else min(0.35, detail.memory.confidence)
            if confirmation is MemoryConfirmation.DISPUTED
            else detail.memory.confidence
        )
        updated = await self._repository.set_confirmation(
            memory_id=memory_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            confirmation=confirmation,
            confidence=confidence,
            updated_at=datetime.now(UTC),
            actor_id=actor_id,
        )
        if updated is None:
            raise MemoryNotFoundError(f"记忆不存在：{memory_id}")
        return updated

    async def correct_memory(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        content: str,
        event_at: datetime,
        actor_id: UUID,
        note: str | None,
    ) -> MemoryDetail:
        source = await self.get_memory_detail(
            memory_id=memory_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        if source.memory.status is not MemoryStatus.ACTIVE:
            raise MemoryConflictError("只有生效记忆可以创建纠正版本")
        normalized = self._required_text(content, "纠正后的记忆内容", maximum=8000)
        self._aware(event_at, "记忆事件时间")
        now = datetime.now(UTC)
        replacement_id = uuid4()
        replacement = replace(
            source.memory,
            id=replacement_id,
            content=normalized,
            event_at=event_at,
            confirmation=MemoryConfirmation.CONFIRMED,
            status=MemoryStatus.ACTIVE,
            version=source.memory.version + 1,
            embedding_version=self._embedding_encoder.version,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        correction_source = MemorySource(
            id=uuid4(),
            tenant_id=tenant_id,
            memory_id=replacement_id,
            kind=MemorySourceKind.ADMIN_CORRECTION,
            source_id=str(memory_id),
            excerpt=None,
            is_verbatim=False,
            occurred_at=now,
            created_at=now,
        )
        link = MemoryLink(
            id=uuid4(),
            tenant_id=tenant_id,
            source_memory_id=replacement_id,
            target_memory_id=memory_id,
            kind=MemoryLinkKind.SUPERSEDES,
            note=self._optional_text(note, maximum=1000),
            created_by=actor_id,
            created_at=now,
        )
        result = await self._repository.correct_memory(
            source_memory_id=memory_id,
            replacement=replacement,
            correction_source=correction_source,
            replacement_embedding=self._embedding(replacement, normalized, now=now),
            link=link,
        )
        if result is None:
            raise MemoryConflictError("记忆已被其他操作修改，请刷新后重试")
        return result

    async def forget_memory(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
    ) -> Memory:
        forgotten = await self._repository.forget_memory(
            memory_id=memory_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            updated_at=datetime.now(UTC),
            actor_id=actor_id,
        )
        if forgotten is None:
            raise MemoryNotFoundError(f"记忆不存在：{memory_id}")
        return forgotten

    async def link_conflict(
        self,
        *,
        source_memory_id: UUID,
        target_memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        note: str | None,
    ) -> MemoryLink:
        if source_memory_id == target_memory_id:
            raise MemoryValidationError("记忆不能与自身建立冲突")
        for memory_id in (source_memory_id, target_memory_id):
            await self.get_memory_detail(
                memory_id=memory_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
        return await self._repository.create_memory_link(
            MemoryLink(
                id=uuid4(),
                tenant_id=tenant_id,
                source_memory_id=source_memory_id,
                target_memory_id=target_memory_id,
                kind=MemoryLinkKind.CONFLICTS_WITH,
                note=self._optional_text(note, maximum=1000),
                created_by=actor_id,
                created_at=datetime.now(UTC),
            )
        )

    async def recall(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        query: str,
        limit: int,
        candidate_pool: int,
        maximum_sensitivity: MemorySensitivity,
        recency_half_life_days: float,
        weights: HybridRecallWeights,
        now: datetime | None = None,
    ) -> tuple[MemoryRecall, ...]:
        normalized = self._required_text(query, "召回查询", maximum=8000)
        maximum = self._limit(limit)
        if candidate_pool < maximum or candidate_pool > 500:
            raise MemoryValidationError("候选池必须不小于召回数量且不能超过 500")
        candidates = await self._repository.search_candidates(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            query=normalized,
            query_embedding=self._embedding_encoder.encode(normalized),
            embedding_version=self._embedding_encoder.version,
            maximum_sensitivity=maximum_sensitivity,
            limit=candidate_pool,
        )
        return self._ranker.rank(
            candidates,
            now=now,
            limit=maximum,
            recency_half_life_days=recency_half_life_days,
            weights=weights,
        )

    async def get_relationship(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
    ) -> RelationshipDetail | None:
        return await self._repository.get_relationship_detail(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
        )

    async def record_relationship_event(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        event_type: str,
        affinity_delta: float,
        trust_delta: float,
        familiarity_delta: float,
        summary: str,
        boundaries: tuple[str, ...] | None,
        evidence_memory_id: UUID | None,
        actor_id: UUID,
        event_id: UUID | None = None,
    ) -> RelationshipDetail:
        for name, value in (
            ("亲和度变化", affinity_delta),
            ("信任度变化", trust_delta),
            ("熟悉度变化", familiarity_delta),
        ):
            if not -1 <= value <= 1:
                raise MemoryValidationError(f"{name}必须位于 -1 到 1 之间")
        normalized_type = self._required_text(event_type, "关系事件类型", maximum=80)
        normalized_summary = self._required_text(summary, "关系事件摘要", maximum=1000)
        if evidence_memory_id is not None:
            await self.get_memory_detail(
                memory_id=evidence_memory_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
        existing = await self.get_relationship(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
        )
        now = datetime.now(UTC)
        current = (
            existing.relationship
            if existing
            else Relationship(
                id=uuid5(agent_id, str(user_id)),
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                stage=RelationshipStage.STRANGER,
                affinity=0.0,
                trust=0.0,
                familiarity=0.0,
                interaction_count=0,
                summary="尚未形成稳定关系摘要。",
                boundaries=(),
                version=0,
                created_at=now,
                updated_at=now,
            )
        )
        affinity = self._clamp(current.affinity + affinity_delta)
        trust = self._clamp(current.trust + trust_delta)
        familiarity = self._clamp(current.familiarity + familiarity_delta)
        updated = replace(
            current,
            stage=self._relationship_stage(trust, familiarity),
            affinity=affinity,
            trust=trust,
            familiarity=familiarity,
            interaction_count=current.interaction_count + 1,
            summary=normalized_summary,
            boundaries=(
                tuple(self._required_text(item, "关系边界", maximum=300) for item in boundaries)
                if boundaries is not None
                else current.boundaries
            ),
            version=current.version + 1,
            updated_at=now,
        )
        event = RelationshipEvent(
            id=event_id or uuid4(),
            tenant_id=tenant_id,
            relationship_id=updated.id,
            event_type=normalized_type,
            affinity_delta=affinity_delta,
            trust_delta=trust_delta,
            familiarity_delta=familiarity_delta,
            evidence_memory_id=evidence_memory_id,
            summary=normalized_summary,
            created_by=actor_id,
            created_at=now,
        )
        return await self._repository.apply_relationship_event(
            relationship=updated,
            event=event,
        )

    async def rebuild_embeddings(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
        actor_id: UUID,
    ) -> MemoryIndexJob:
        job = await self.request_embedding_rebuild(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            actor_id=actor_id,
        )
        return await self.run_embedding_rebuild(
            tenant_id=tenant_id,
            agent_id=agent_id,
            job_id=job.id,
        )

    async def request_embedding_rebuild(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
        actor_id: UUID,
    ) -> MemoryIndexJob:
        """只建立持久化进度记录，实际向量计算由 Worker 执行。"""
        memories = await self._repository.list_memories_for_embedding(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
        )
        now = datetime.now(UTC)
        return await self._repository.create_index_job(
            MemoryIndexJob(
                id=uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                target_embedding_version=self._embedding_encoder.version,
                status=MemoryIndexJobStatus.PENDING,
                total_items=len(memories),
                processed_items=0,
                error_code=None,
                created_by=actor_id,
                created_at=now,
                started_at=None,
                completed_at=None,
            )
        )

    async def run_embedding_rebuild(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        job_id: UUID,
    ) -> MemoryIndexJob:
        """恢复或执行一个既有索引任务；已完成任务重复投递时直接返回。"""
        job = await self._repository.get_index_job(
            tenant_id=tenant_id,
            agent_id=agent_id,
            job_id=job_id,
        )
        if job is None:
            raise MemoryNotFoundError(f"记忆索引任务不存在：{job_id}")
        if job.status is MemoryIndexJobStatus.COMPLETED:
            return job
        memories = await self._repository.list_memories_for_embedding(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=job.user_id,
        )
        running = replace(
            job,
            status=MemoryIndexJobStatus.RUNNING,
            total_items=len(memories),
            processed_items=0,
            error_code=None,
            started_at=datetime.now(UTC),
            completed_at=None,
        )
        await self._repository.update_index_job(running)
        try:
            processed = 0
            for memory in memories:
                if memory.content is None:
                    continue
                rebuilt_at = datetime.now(UTC)
                await self._repository.replace_embedding(
                    memory_id=memory.id,
                    tenant_id=tenant_id,
                    embedding=self._embedding(memory, memory.content, now=rebuilt_at),
                    updated_at=rebuilt_at,
                )
                processed += 1
                running = replace(running, processed_items=processed)
                await self._repository.update_index_job(running)
        except Exception as error:
            failed = replace(
                running,
                status=MemoryIndexJobStatus.FAILED,
                error_code=type(error).__name__,
                completed_at=datetime.now(UTC),
            )
            await self._repository.update_index_job(failed)
            raise
        completed = replace(
            running,
            status=MemoryIndexJobStatus.COMPLETED,
            completed_at=datetime.now(UTC),
        )
        return await self._repository.update_index_job(completed)

    async def list_index_jobs(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        limit: int,
    ) -> tuple[MemoryIndexJob, ...]:
        return await self._repository.list_index_jobs(
            tenant_id=tenant_id,
            agent_id=agent_id,
            limit=self._limit(limit),
        )

    def _embedding(self, memory: Memory, content: str, *, now: datetime) -> MemoryEmbedding:
        vector = self._embedding_encoder.encode(content)
        return MemoryEmbedding(
            id=uuid5(memory.id, f"embedding:{self._embedding_encoder.version}"),
            tenant_id=memory.tenant_id,
            memory_id=memory.id,
            embedding_version=self._embedding_encoder.version,
            dimensions=self._embedding_encoder.dimensions,
            vector=vector,
            active=True,
            created_at=now,
        )

    @staticmethod
    def _source_record(
        draft: MemorySourceDraft,
        *,
        tenant_id: UUID,
        memory_id: UUID,
        created_at: datetime,
        record_id: UUID | None = None,
    ) -> MemorySource:
        source_id = MemoryService._required_text(draft.source_id, "来源 ID", maximum=255)
        MemoryService._aware(draft.occurred_at, "来源发生时间")
        excerpt = MemoryService._optional_text(draft.excerpt, maximum=2000)
        if draft.is_verbatim and excerpt is None:
            raise MemoryValidationError("逐字来源必须提供来源摘录")
        return MemorySource(
            id=record_id or uuid4(),
            tenant_id=tenant_id,
            memory_id=memory_id,
            kind=draft.kind,
            source_id=source_id,
            excerpt=excerpt,
            is_verbatim=draft.is_verbatim,
            occurred_at=draft.occurred_at,
            created_at=created_at,
        )

    @staticmethod
    def _validate_memory_values(
        *,
        visibility: MemoryVisibility,
        user_id: UUID | None,
        event_at: datetime,
        confidence: float,
        importance: float,
        emotional_weight: float,
        sources: tuple[MemorySourceDraft, ...],
    ) -> None:
        if visibility is MemoryVisibility.USER and user_id is None:
            raise MemoryValidationError("用户私有记忆必须绑定用户")
        MemoryService._aware(event_at, "记忆事件时间")
        for name, value in (("置信度", confidence), ("重要性", importance)):
            if not 0 <= value <= 1:
                raise MemoryValidationError(f"{name}必须位于 0 到 1 之间")
        if not -1 <= emotional_weight <= 1:
            raise MemoryValidationError("情绪权重必须位于 -1 到 1 之间")
        if not sources:
            raise MemoryValidationError("每条长期记忆至少需要一个可追溯来源")

    @staticmethod
    def _relationship_stage(trust: float, familiarity: float) -> RelationshipStage:
        if trust >= 0.75 and familiarity >= 0.75:
            return RelationshipStage.TRUSTED
        if familiarity >= 0.5:
            return RelationshipStage.FAMILIAR
        if familiarity >= 0.15:
            return RelationshipStage.ACQUAINTANCE
        return RelationshipStage.STRANGER

    @staticmethod
    def _required_text(value: str, label: str, *, maximum: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise MemoryValidationError(f"{label}不能为空")
        if len(normalized) > maximum:
            raise MemoryValidationError(f"{label}不能超过 {maximum} 个字符")
        return normalized

    @staticmethod
    def _optional_text(value: str | None, *, maximum: int) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if len(normalized) > maximum:
            raise MemoryValidationError(f"文本不能超过 {maximum} 个字符")
        return normalized

    @staticmethod
    def _aware(value: datetime, label: str) -> None:
        if value.tzinfo is None:
            raise MemoryValidationError(f"{label}必须包含时区")

    @staticmethod
    def _limit(value: int) -> int:
        if not 1 <= value <= 200:
            raise MemoryValidationError("分页数量必须位于 1 到 200 之间")
        return value

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, value))
