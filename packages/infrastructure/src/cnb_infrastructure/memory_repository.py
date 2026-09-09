"""长期记忆与关系的内存及 PostgreSQL/pgvector 仓储。"""

import asyncio
from dataclasses import replace
from datetime import datetime
from math import sqrt
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from cnb_infrastructure.models import (
    AuditLog,
    EpisodeModel,
    MemoryEmbeddingModel,
    MemoryIndexJobModel,
    MemoryLinkModel,
    MemoryModel,
    MemorySourceModel,
    RelationshipEventModel,
    RelationshipModel,
)

_SENSITIVITY_ORDER = {
    MemorySensitivity.NORMAL: 0,
    MemorySensitivity.PERSONAL: 1,
    MemorySensitivity.SENSITIVE: 2,
    MemorySensitivity.RESTRICTED: 3,
}


class InMemoryMemoryRepository:
    """供单元测试与无数据库联调使用的并发安全记忆仓储。"""

    def __init__(self) -> None:
        self.episodes: dict[UUID, Episode] = {}
        self.memories: dict[UUID, Memory] = {}
        self.sources: dict[UUID, list[MemorySource]] = {}
        self.links: dict[UUID, MemoryLink] = {}
        self.embeddings: dict[UUID, list[MemoryEmbedding]] = {}
        self.relationships: dict[tuple[UUID, UUID, UUID], Relationship] = {}
        self.relationship_events: dict[UUID, list[RelationshipEvent]] = {}
        self.index_jobs: dict[UUID, MemoryIndexJob] = {}
        self._lock = asyncio.Lock()

    async def list_episodes(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
        limit: int,
    ) -> tuple[Episode, ...]:
        async with self._lock:
            rows = [
                item
                for item in self.episodes.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (user_id is None or item.user_id == user_id)
            ]
            rows.sort(key=lambda item: (item.started_at, str(item.id)), reverse=True)
            return tuple(rows[:limit])

    async def create_episode(self, episode: Episode) -> Episode:
        async with self._lock:
            self.episodes[episode.id] = episode
            return episode

    async def set_episode_status(
        self,
        *,
        episode_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        status: EpisodeStatus,
        ended_at: datetime | None,
        actor_id: UUID,
    ) -> Episode | None:
        del actor_id
        async with self._lock:
            item = self.episodes.get(episode_id)
            if item is None or item.tenant_id != tenant_id or item.agent_id != agent_id:
                return None
            updated = replace(
                item,
                status=status,
                ended_at=ended_at or item.ended_at,
                updated_at=ended_at or item.updated_at,
            )
            self.episodes[episode_id] = updated
            return updated

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
        async with self._lock:
            needle = query.casefold() if query else None
            rows = [
                item
                for item in self.memories.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (user_id is None or item.user_id in {None, user_id})
                and (status is None or item.status is status)
                and (kind is None or item.kind is kind)
                and (needle is None or needle in (item.content or "").casefold())
            ]
            rows.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
            return tuple(rows[:limit])

    async def get_memory_detail(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> MemoryDetail | None:
        async with self._lock:
            memory = self.memories.get(memory_id)
            if memory is None or memory.tenant_id != tenant_id or memory.agent_id != agent_id:
                return None
            links = tuple(
                item
                for item in self.links.values()
                if item.tenant_id == tenant_id
                and (item.source_memory_id == memory_id or item.target_memory_id == memory_id)
            )
            return MemoryDetail(memory, tuple(self.sources.get(memory_id, ())), links)

    async def create_memory(
        self,
        *,
        memory: Memory,
        sources: tuple[MemorySource, ...],
        embedding: MemoryEmbedding,
    ) -> MemoryDetail:
        async with self._lock:
            self.memories[memory.id] = memory
            self.sources[memory.id] = list(sources)
            self.embeddings[memory.id] = [embedding]
            return MemoryDetail(memory, sources, ())

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
    ) -> Memory | None:
        del actor_id
        async with self._lock:
            memory = self.memories.get(memory_id)
            if (
                memory is None
                or memory.tenant_id != tenant_id
                or memory.agent_id != agent_id
                or memory.status is not MemoryStatus.ACTIVE
            ):
                return None
            updated = replace(
                memory,
                confirmation=confirmation,
                confidence=confidence,
                updated_at=updated_at,
            )
            self.memories[memory_id] = updated
            return updated

    async def correct_memory(
        self,
        *,
        source_memory_id: UUID,
        replacement: Memory,
        correction_source: MemorySource,
        replacement_embedding: MemoryEmbedding,
        link: MemoryLink,
    ) -> MemoryDetail | None:
        async with self._lock:
            source = self.memories.get(source_memory_id)
            if source is None or source.status is not MemoryStatus.ACTIVE:
                return None
            self.memories[source_memory_id] = replace(
                source,
                status=MemoryStatus.SUPERSEDED,
                updated_at=replacement.updated_at,
            )
            self.memories[replacement.id] = replacement
            self.sources[replacement.id] = [correction_source]
            self.embeddings[replacement.id] = [replacement_embedding]
            self.links[link.id] = link
            return MemoryDetail(replacement, (correction_source,), (link,))

    async def forget_memory(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        updated_at: datetime,
        actor_id: UUID,
    ) -> Memory | None:
        del actor_id
        async with self._lock:
            memory = self.memories.get(memory_id)
            if memory is None or memory.tenant_id != tenant_id or memory.agent_id != agent_id:
                return None
            forgotten = replace(
                memory,
                content=None,
                status=MemoryStatus.FORGOTTEN,
                embedding_version=None,
                updated_at=updated_at,
            )
            self.memories[memory_id] = forgotten
            self.sources[memory_id] = [
                replace(item, excerpt=None, is_verbatim=False)
                for item in self.sources.get(memory_id, ())
            ]
            self.embeddings.pop(memory_id, None)
            return forgotten

    async def create_memory_link(self, link: MemoryLink) -> MemoryLink:
        async with self._lock:
            self.links[link.id] = link
            return link

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
    ) -> tuple[RawMemoryCandidate, ...]:
        async with self._lock:
            relationship = self.relationships.get((tenant_id, agent_id, user_id))
            candidates: list[RawMemoryCandidate] = []
            for memory in self.memories.values():
                if not self._visible(memory, tenant_id, agent_id, user_id):
                    continue
                if memory.status is not MemoryStatus.ACTIVE or memory.content is None:
                    continue
                if _SENSITIVITY_ORDER[memory.sensitivity] > _SENSITIVITY_ORDER[maximum_sensitivity]:
                    continue
                embedding = next(
                    (
                        item
                        for item in self.embeddings.get(memory.id, ())
                        if item.active and item.embedding_version == embedding_version
                    ),
                    None,
                )
                semantic = self._cosine(query_embedding, embedding.vector) if embedding else 0.0
                candidates.append(
                    RawMemoryCandidate(
                        memory=memory,
                        full_text_score=self._text_score(query, memory.content),
                        semantic_score=max(0.0, semantic),
                        relationship_score=(
                            relationship.familiarity
                            if relationship is not None and memory.user_id == user_id
                            else 0.0
                        ),
                    )
                )
            candidates.sort(
                key=lambda item: (
                    max(item.full_text_score, item.semantic_score),
                    item.memory.importance,
                    item.memory.event_at,
                ),
                reverse=True,
            )
            return tuple(candidates[:limit])

    async def get_relationship_detail(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
    ) -> RelationshipDetail | None:
        async with self._lock:
            relationship = self.relationships.get((tenant_id, agent_id, user_id))
            if relationship is None:
                return None
            events = tuple(self.relationship_events.get(relationship.id, ()))
            return RelationshipDetail(relationship, events)

    async def apply_relationship_event(
        self,
        *,
        relationship: Relationship,
        event: RelationshipEvent,
    ) -> RelationshipDetail:
        async with self._lock:
            for events in self.relationship_events.values():
                if any(item.id == event.id for item in events):
                    current = self.relationships.get(
                        (relationship.tenant_id, relationship.agent_id, relationship.user_id)
                    )
                    if current is not None:
                        return RelationshipDetail(
                            current,
                            tuple(self.relationship_events.get(current.id, ())),
                        )
            self.relationships[
                (relationship.tenant_id, relationship.agent_id, relationship.user_id)
            ] = relationship
            events = self.relationship_events.setdefault(relationship.id, [])
            events.append(event)
            return RelationshipDetail(relationship, tuple(events))

    async def list_memories_for_embedding(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
    ) -> tuple[Memory, ...]:
        async with self._lock:
            return tuple(
                item
                for item in self.memories.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and item.status is MemoryStatus.ACTIVE
                and item.content is not None
                and (user_id is None or item.user_id == user_id)
            )

    async def create_index_job(self, job: MemoryIndexJob) -> MemoryIndexJob:
        async with self._lock:
            self.index_jobs[job.id] = job
            return job

    async def get_index_job(
        self, *, tenant_id: UUID, agent_id: UUID, job_id: UUID
    ) -> MemoryIndexJob | None:
        async with self._lock:
            item = self.index_jobs.get(job_id)
            if item is None or item.tenant_id != tenant_id or item.agent_id != agent_id:
                return None
            return item

    async def update_index_job(self, job: MemoryIndexJob) -> MemoryIndexJob:
        async with self._lock:
            self.index_jobs[job.id] = job
            return job

    async def replace_embedding(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        embedding: MemoryEmbedding,
        updated_at: datetime,
    ) -> None:
        async with self._lock:
            memory = self.memories.get(memory_id)
            if memory is None or memory.tenant_id != tenant_id:
                return
            previous = [replace(item, active=False) for item in self.embeddings.get(memory_id, ())]
            previous = [
                item for item in previous if item.embedding_version != embedding.embedding_version
            ]
            self.embeddings[memory_id] = [*previous, embedding]
            self.memories[memory_id] = replace(
                memory,
                embedding_version=embedding.embedding_version,
                updated_at=updated_at,
            )

    async def list_index_jobs(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        limit: int,
    ) -> tuple[MemoryIndexJob, ...]:
        async with self._lock:
            rows = [
                item
                for item in self.index_jobs.values()
                if item.tenant_id == tenant_id and item.agent_id == agent_id
            ]
            rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
            return tuple(rows[:limit])

    @staticmethod
    def _visible(memory: Memory, tenant_id: UUID, agent_id: UUID, user_id: UUID) -> bool:
        if memory.tenant_id != tenant_id:
            return False
        if memory.visibility is MemoryVisibility.TENANT:
            return True
        if memory.agent_id != agent_id:
            return False
        if memory.visibility is MemoryVisibility.AGENT:
            return True
        return memory.user_id == user_id

    @staticmethod
    def _text_score(query: str, content: str) -> float:
        def grams(value: str) -> set[str]:
            normalized = "".join(value.casefold().split())
            if not normalized:
                return set()
            return {
                *normalized,
                *(normalized[index : index + 2] for index in range(len(normalized) - 1)),
            }

        query_grams = grams(query)
        if not query_grams:
            return 0.0
        return len(query_grams & grams(content)) / len(query_grams)

    @staticmethod
    def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        if len(left) != len(right):
            return 0.0
        denominator = sqrt(sum(value * value for value in left)) * sqrt(
            sum(value * value for value in right)
        )
        if denominator == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


class SqlAlchemyMemoryRepository:
    """PostgreSQL 全文、pg_trgm 与 pgvector 检索实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_episodes(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
        limit: int,
    ) -> tuple[Episode, ...]:
        statement = select(EpisodeModel).where(
            EpisodeModel.tenant_id == tenant_id,
            EpisodeModel.agent_id == agent_id,
        )
        if user_id is not None:
            statement = statement.where(EpisodeModel.user_id == user_id)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(EpisodeModel.started_at.desc(), EpisodeModel.id).limit(limit)
                )
            ).all()
        return tuple(self._episode(row) for row in rows)

    async def create_episode(self, episode: Episode) -> Episode:
        async with self._session_factory.begin() as session:
            existing = await session.get(EpisodeModel, episode.id)
            if existing is not None:
                if existing.tenant_id != episode.tenant_id or existing.agent_id != episode.agent_id:
                    raise ValueError("Episode 幂等 ID 已被其他作用域占用")
                return self._episode(existing)
            session.add(self._episode_model(episode))
            self._audit(
                session,
                tenant_id=episode.tenant_id,
                actor_id=episode.created_by,
                action="episode.created",
                resource_type="episode",
                resource_id=episode.id,
                detail={"status": episode.status.value},
            )
        return episode

    async def set_episode_status(
        self,
        *,
        episode_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        status: EpisodeStatus,
        ended_at: datetime | None,
        actor_id: UUID,
    ) -> Episode | None:
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(EpisodeModel)
                .where(
                    EpisodeModel.id == episode_id,
                    EpisodeModel.tenant_id == tenant_id,
                    EpisodeModel.agent_id == agent_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            row.status = status.value
            row.ended_at = ended_at or row.ended_at
            row.updated_at = ended_at or row.updated_at
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="episode.status_updated",
                resource_type="episode",
                resource_id=episode_id,
                detail={"status": status.value},
            )
        return self._episode(row)

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
        statement = select(MemoryModel).where(
            MemoryModel.tenant_id == tenant_id,
            MemoryModel.agent_id == agent_id,
        )
        if user_id is not None:
            statement = statement.where(
                or_(MemoryModel.user_id.is_(None), MemoryModel.user_id == user_id)
            )
        if status is not None:
            statement = statement.where(MemoryModel.status == status.value)
        if kind is not None:
            statement = statement.where(MemoryModel.kind == kind.value)
        if query:
            statement = statement.where(
                func.similarity(func.coalesce(MemoryModel.content, ""), query) > 0.05
            )
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(MemoryModel.updated_at.desc(), MemoryModel.id).limit(limit)
                )
            ).all()
        return tuple(self._memory(row) for row in rows)

    async def get_memory_detail(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> MemoryDetail | None:
        async with self._session_factory() as session:
            memory = await session.scalar(
                select(MemoryModel).where(
                    MemoryModel.id == memory_id,
                    MemoryModel.tenant_id == tenant_id,
                    MemoryModel.agent_id == agent_id,
                )
            )
            if memory is None:
                return None
            sources = (
                await session.scalars(
                    select(MemorySourceModel)
                    .where(
                        MemorySourceModel.memory_id == memory_id,
                        MemorySourceModel.tenant_id == tenant_id,
                    )
                    .order_by(MemorySourceModel.created_at, MemorySourceModel.id)
                )
            ).all()
            links = (
                await session.scalars(
                    select(MemoryLinkModel)
                    .where(
                        MemoryLinkModel.tenant_id == tenant_id,
                        or_(
                            MemoryLinkModel.source_memory_id == memory_id,
                            MemoryLinkModel.target_memory_id == memory_id,
                        ),
                    )
                    .order_by(MemoryLinkModel.created_at, MemoryLinkModel.id)
                )
            ).all()
        return MemoryDetail(
            self._memory(memory),
            tuple(self._source(row) for row in sources),
            tuple(self._link(row) for row in links),
        )

    async def create_memory(
        self,
        *,
        memory: Memory,
        sources: tuple[MemorySource, ...],
        embedding: MemoryEmbedding,
    ) -> MemoryDetail:
        async with self._session_factory.begin() as session:
            existing = await session.get(MemoryModel, memory.id)
            if existing is not None:
                if existing.tenant_id != memory.tenant_id or existing.agent_id != memory.agent_id:
                    raise ValueError("记忆幂等 ID 已被其他作用域占用")
                source_rows = (
                    await session.scalars(
                        select(MemorySourceModel).where(MemorySourceModel.memory_id == memory.id)
                    )
                ).all()
                return MemoryDetail(
                    self._memory(existing),
                    tuple(self._source(row) for row in source_rows),
                    (),
                )
            session.add(self._memory_model(memory))
            session.add_all(self._source_model(source) for source in sources)
            session.add(self._embedding_model(embedding))
            self._audit(
                session,
                tenant_id=memory.tenant_id,
                actor_id=memory.created_by,
                action="memory.created",
                resource_type="memory",
                resource_id=memory.id,
                detail={
                    "kind": memory.kind.value,
                    "visibility": memory.visibility.value,
                    "source_count": len(sources),
                },
            )
        return MemoryDetail(memory, sources, ())

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
    ) -> Memory | None:
        async with self._session_factory.begin() as session:
            row = await self._locked_memory(
                session,
                memory_id=memory_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            if row is None:
                return None
            if row.status != MemoryStatus.ACTIVE.value:
                return None
            row.confirmation = confirmation.value
            row.confidence = confidence
            row.updated_at = updated_at
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="memory.confirmation_updated",
                resource_type="memory",
                resource_id=memory_id,
                detail={"confirmation": confirmation.value},
            )
        return self._memory(row)

    async def correct_memory(
        self,
        *,
        source_memory_id: UUID,
        replacement: Memory,
        correction_source: MemorySource,
        replacement_embedding: MemoryEmbedding,
        link: MemoryLink,
    ) -> MemoryDetail | None:
        async with self._session_factory.begin() as session:
            source = await self._locked_memory(
                session,
                memory_id=source_memory_id,
                tenant_id=replacement.tenant_id,
                agent_id=replacement.agent_id,
            )
            if source is None or source.status != MemoryStatus.ACTIVE.value:
                return None
            source.status = MemoryStatus.SUPERSEDED.value
            source.updated_at = replacement.updated_at
            session.add(self._memory_model(replacement))
            session.add(self._source_model(correction_source))
            session.add(self._embedding_model(replacement_embedding))
            session.add(self._link_model(link))
            self._audit(
                session,
                tenant_id=replacement.tenant_id,
                actor_id=replacement.created_by,
                action="memory.corrected",
                resource_type="memory",
                resource_id=replacement.id,
                detail={
                    "supersedes": str(source_memory_id),
                    "version": replacement.version,
                },
            )
        return MemoryDetail(replacement, (correction_source,), (link,))

    async def forget_memory(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        updated_at: datetime,
        actor_id: UUID,
    ) -> Memory | None:
        async with self._session_factory.begin() as session:
            row = await self._locked_memory(
                session,
                memory_id=memory_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            if row is None:
                return None
            row.content = None
            row.status = MemoryStatus.FORGOTTEN.value
            row.embedding_version = None
            row.updated_at = updated_at
            await session.execute(
                update(MemorySourceModel)
                .where(
                    MemorySourceModel.memory_id == memory_id,
                    MemorySourceModel.tenant_id == tenant_id,
                )
                .values(excerpt=None, is_verbatim=False)
            )
            await session.execute(
                delete(MemoryEmbeddingModel).where(
                    MemoryEmbeddingModel.memory_id == memory_id,
                    MemoryEmbeddingModel.tenant_id == tenant_id,
                )
            )
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="memory.forgotten",
                resource_type="memory",
                resource_id=memory_id,
                detail={"content_removed": True, "embedding_removed": True},
            )
        return self._memory(row)

    async def create_memory_link(self, link: MemoryLink) -> MemoryLink:
        async with self._session_factory.begin() as session:
            owned = (
                await session.scalars(
                    select(MemoryModel.id).where(
                        MemoryModel.tenant_id == link.tenant_id,
                        MemoryModel.id.in_((link.source_memory_id, link.target_memory_id)),
                    )
                )
            ).all()
            if len(owned) != 2:
                raise ValueError("冲突记忆不属于同一租户")
            session.add(self._link_model(link))
            self._audit(
                session,
                tenant_id=link.tenant_id,
                actor_id=link.created_by,
                action="memory.conflict_linked",
                resource_type="memory_link",
                resource_id=link.id,
                detail={
                    "source_memory_id": str(link.source_memory_id),
                    "target_memory_id": str(link.target_memory_id),
                },
            )
        return link

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
    ) -> tuple[RawMemoryCandidate, ...]:
        allowed_sensitivity = [
            item.value
            for item, order in _SENSITIVITY_ORDER.items()
            if order <= _SENSITIVITY_ORDER[maximum_sensitivity]
        ]
        document = func.to_tsvector("simple", func.coalesce(MemoryModel.content, ""))
        ts_query = func.websearch_to_tsquery("simple", query)
        full_text = func.greatest(
            func.ts_rank_cd(document, ts_query),
            func.similarity(func.coalesce(MemoryModel.content, ""), query),
        ).label("full_text_score")
        semantic = func.greatest(
            0.0,
            1.0 - MemoryEmbeddingModel.embedding.cosine_distance(list(query_embedding)),
        ).label("semantic_score")
        visibility = or_(
            MemoryModel.visibility == MemoryVisibility.TENANT.value,
            and_(
                MemoryModel.agent_id == agent_id,
                MemoryModel.visibility == MemoryVisibility.AGENT.value,
            ),
            and_(
                MemoryModel.agent_id == agent_id,
                MemoryModel.visibility == MemoryVisibility.USER.value,
                MemoryModel.user_id == user_id,
            ),
        )
        async with self._session_factory() as session:
            familiarity = (
                await session.scalar(
                    select(RelationshipModel.familiarity).where(
                        RelationshipModel.tenant_id == tenant_id,
                        RelationshipModel.agent_id == agent_id,
                        RelationshipModel.user_id == user_id,
                    )
                )
                or 0.0
            )
            rows = (
                await session.execute(
                    select(MemoryModel, full_text, semantic)
                    .outerjoin(
                        MemoryEmbeddingModel,
                        and_(
                            MemoryEmbeddingModel.memory_id == MemoryModel.id,
                            MemoryEmbeddingModel.tenant_id == tenant_id,
                            MemoryEmbeddingModel.active.is_(True),
                            MemoryEmbeddingModel.embedding_version == embedding_version,
                        ),
                    )
                    .where(
                        MemoryModel.tenant_id == tenant_id,
                        MemoryModel.status == MemoryStatus.ACTIVE.value,
                        MemoryModel.content.is_not(None),
                        MemoryModel.sensitivity.in_(allowed_sensitivity),
                        visibility,
                    )
                    .order_by(
                        func.greatest(full_text, semantic).desc(),
                        MemoryModel.importance.desc(),
                        MemoryModel.event_at.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        return tuple(
            RawMemoryCandidate(
                memory=self._memory(row),
                full_text_score=float(text_score or 0),
                semantic_score=float(semantic_score or 0),
                relationship_score=(familiarity if row.user_id == user_id else 0.0),
            )
            for row, text_score, semantic_score in rows
        )

    async def get_relationship_detail(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
    ) -> RelationshipDetail | None:
        async with self._session_factory() as session:
            relationship = await session.scalar(
                select(RelationshipModel).where(
                    RelationshipModel.tenant_id == tenant_id,
                    RelationshipModel.agent_id == agent_id,
                    RelationshipModel.user_id == user_id,
                )
            )
            if relationship is None:
                return None
            events = (
                await session.scalars(
                    select(RelationshipEventModel)
                    .where(
                        RelationshipEventModel.tenant_id == tenant_id,
                        RelationshipEventModel.relationship_id == relationship.id,
                    )
                    .order_by(RelationshipEventModel.created_at, RelationshipEventModel.id)
                )
            ).all()
        return RelationshipDetail(
            self._relationship(relationship),
            tuple(self._relationship_event(row) for row in events),
        )

    async def apply_relationship_event(
        self,
        *,
        relationship: Relationship,
        event: RelationshipEvent,
    ) -> RelationshipDetail:
        async with self._session_factory() as session:
            duplicate = await session.get(RelationshipEventModel, event.id)
        if duplicate is not None:
            detail = await self.get_relationship_detail(
                tenant_id=relationship.tenant_id,
                agent_id=relationship.agent_id,
                user_id=relationship.user_id,
            )
            if detail is not None:
                return detail
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(RelationshipModel)
                .where(
                    RelationshipModel.tenant_id == relationship.tenant_id,
                    RelationshipModel.agent_id == relationship.agent_id,
                    RelationshipModel.user_id == relationship.user_id,
                )
                .with_for_update()
            )
            if row is None:
                session.add(self._relationship_model(relationship))
            else:
                row.stage = relationship.stage.value
                row.affinity = relationship.affinity
                row.trust = relationship.trust
                row.familiarity = relationship.familiarity
                row.interaction_count = relationship.interaction_count
                row.summary = relationship.summary
                row.boundaries = list(relationship.boundaries)
                row.version = relationship.version
                row.updated_at = relationship.updated_at
            session.add(self._relationship_event_model(event))
            self._audit(
                session,
                tenant_id=relationship.tenant_id,
                actor_id=event.created_by,
                action="relationship.updated",
                resource_type="relationship",
                resource_id=relationship.id,
                detail={"stage": relationship.stage.value, "version": relationship.version},
            )
        detail = await self.get_relationship_detail(
            tenant_id=relationship.tenant_id,
            agent_id=relationship.agent_id,
            user_id=relationship.user_id,
        )
        return detail or RelationshipDetail(relationship, (event,))

    async def list_memories_for_embedding(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID | None,
    ) -> tuple[Memory, ...]:
        statement = select(MemoryModel).where(
            MemoryModel.tenant_id == tenant_id,
            MemoryModel.agent_id == agent_id,
            MemoryModel.status == MemoryStatus.ACTIVE.value,
            MemoryModel.content.is_not(None),
        )
        if user_id is not None:
            statement = statement.where(MemoryModel.user_id == user_id)
        async with self._session_factory() as session:
            rows = (await session.scalars(statement.order_by(MemoryModel.id))).all()
        return tuple(self._memory(row) for row in rows)

    async def create_index_job(self, job: MemoryIndexJob) -> MemoryIndexJob:
        async with self._session_factory.begin() as session:
            session.add(self._index_job_model(job))
            self._audit(
                session,
                tenant_id=job.tenant_id,
                actor_id=job.created_by,
                action="memory.index_rebuild_created",
                resource_type="memory_index_job",
                resource_id=job.id,
                detail={
                    "target_embedding_version": job.target_embedding_version,
                    "total_items": job.total_items,
                },
            )
        return job

    async def get_index_job(
        self, *, tenant_id: UUID, agent_id: UUID, job_id: UUID
    ) -> MemoryIndexJob | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(MemoryIndexJobModel).where(
                    MemoryIndexJobModel.id == job_id,
                    MemoryIndexJobModel.tenant_id == tenant_id,
                    MemoryIndexJobModel.agent_id == agent_id,
                )
            )
        return self._index_job(row) if row is not None else None

    async def update_index_job(self, job: MemoryIndexJob) -> MemoryIndexJob:
        async with self._session_factory.begin() as session:
            row = await session.get(MemoryIndexJobModel, job.id)
            if row is None or row.tenant_id != job.tenant_id:
                raise ValueError("记忆索引任务不存在")
            row.status = job.status.value
            row.total_items = job.total_items
            row.processed_items = job.processed_items
            row.error_code = job.error_code
            row.started_at = job.started_at
            row.completed_at = job.completed_at
        return job

    async def replace_embedding(
        self,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        embedding: MemoryEmbedding,
        updated_at: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            memory = await session.scalar(
                select(MemoryModel)
                .where(
                    MemoryModel.id == memory_id,
                    MemoryModel.tenant_id == tenant_id,
                    MemoryModel.status == MemoryStatus.ACTIVE.value,
                )
                .with_for_update()
            )
            if memory is None:
                return
            await session.execute(
                update(MemoryEmbeddingModel)
                .where(
                    MemoryEmbeddingModel.memory_id == memory_id,
                    MemoryEmbeddingModel.active.is_(True),
                )
                .values(active=False)
            )
            current = await session.scalar(
                select(MemoryEmbeddingModel).where(
                    MemoryEmbeddingModel.memory_id == memory_id,
                    MemoryEmbeddingModel.embedding_version == embedding.embedding_version,
                )
            )
            if current is None:
                session.add(self._embedding_model(embedding))
            else:
                current.embedding = list(embedding.vector)
                current.dimensions = embedding.dimensions
                current.active = True
                current.created_at = embedding.created_at
            memory.embedding_version = embedding.embedding_version
            memory.updated_at = updated_at

    async def list_index_jobs(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        limit: int,
    ) -> tuple[MemoryIndexJob, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(MemoryIndexJobModel)
                    .where(
                        MemoryIndexJobModel.tenant_id == tenant_id,
                        MemoryIndexJobModel.agent_id == agent_id,
                    )
                    .order_by(MemoryIndexJobModel.created_at.desc(), MemoryIndexJobModel.id)
                    .limit(limit)
                )
            ).all()
        return tuple(self._index_job(row) for row in rows)

    @staticmethod
    async def _locked_memory(
        session: AsyncSession,
        *,
        memory_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> MemoryModel | None:
        return await session.scalar(
            select(MemoryModel)
            .where(
                MemoryModel.id == memory_id,
                MemoryModel.tenant_id == tenant_id,
                MemoryModel.agent_id == agent_id,
            )
            .with_for_update()
        )

    @staticmethod
    def _audit(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        detail: dict[str, object],
    ) -> None:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                detail=detail,
            )
        )

    @staticmethod
    def _episode_model(item: Episode) -> EpisodeModel:
        return EpisodeModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            user_id=item.user_id,
            conversation_id=item.conversation_id,
            title=item.title,
            summary=item.summary,
            status=item.status.value,
            started_at=item.started_at,
            ended_at=item.ended_at,
            source_message_ids=[str(value) for value in item.source_message_ids],
            created_by=item.created_by,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _episode(row: EpisodeModel) -> Episode:
        return Episode(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            user_id=row.user_id,
            conversation_id=row.conversation_id,
            title=row.title,
            summary=row.summary,
            status=EpisodeStatus(row.status),
            started_at=row.started_at,
            ended_at=row.ended_at,
            source_message_ids=tuple(UUID(value) for value in row.source_message_ids),
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _memory_model(item: Memory) -> MemoryModel:
        return MemoryModel(
            id=item.id,
            lineage_id=item.lineage_id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            user_id=item.user_id,
            conversation_id=item.conversation_id,
            episode_id=item.episode_id,
            kind=item.kind.value,
            visibility=item.visibility.value,
            content=item.content,
            event_at=item.event_at,
            confidence=item.confidence,
            importance=item.importance,
            emotional_weight=item.emotional_weight,
            sensitivity=item.sensitivity.value,
            confirmation=item.confirmation.value,
            status=item.status.value,
            version=item.version,
            embedding_version=item.embedding_version,
            created_by=item.created_by,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _memory(row: MemoryModel) -> Memory:
        return Memory(
            id=row.id,
            lineage_id=row.lineage_id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            user_id=row.user_id,
            conversation_id=row.conversation_id,
            episode_id=row.episode_id,
            kind=MemoryKind(row.kind),
            visibility=MemoryVisibility(row.visibility),
            content=row.content,
            event_at=row.event_at,
            confidence=row.confidence,
            importance=row.importance,
            emotional_weight=row.emotional_weight,
            sensitivity=MemorySensitivity(row.sensitivity),
            confirmation=MemoryConfirmation(row.confirmation),
            status=MemoryStatus(row.status),
            version=row.version,
            embedding_version=row.embedding_version,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _source_model(item: MemorySource) -> MemorySourceModel:
        return MemorySourceModel(
            id=item.id,
            tenant_id=item.tenant_id,
            memory_id=item.memory_id,
            kind=item.kind.value,
            source_id=item.source_id,
            excerpt=item.excerpt,
            is_verbatim=item.is_verbatim,
            occurred_at=item.occurred_at,
            created_at=item.created_at,
        )

    @staticmethod
    def _source(row: MemorySourceModel) -> MemorySource:
        return MemorySource(
            id=row.id,
            tenant_id=row.tenant_id,
            memory_id=row.memory_id,
            kind=MemorySourceKind(row.kind),
            source_id=row.source_id,
            excerpt=row.excerpt,
            is_verbatim=row.is_verbatim,
            occurred_at=row.occurred_at,
            created_at=row.created_at,
        )

    @staticmethod
    def _link_model(item: MemoryLink) -> MemoryLinkModel:
        return MemoryLinkModel(
            id=item.id,
            tenant_id=item.tenant_id,
            source_memory_id=item.source_memory_id,
            target_memory_id=item.target_memory_id,
            kind=item.kind.value,
            note=item.note,
            created_by=item.created_by,
            created_at=item.created_at,
        )

    @staticmethod
    def _link(row: MemoryLinkModel) -> MemoryLink:
        return MemoryLink(
            id=row.id,
            tenant_id=row.tenant_id,
            source_memory_id=row.source_memory_id,
            target_memory_id=row.target_memory_id,
            kind=MemoryLinkKind(row.kind),
            note=row.note,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    @staticmethod
    def _embedding_model(item: MemoryEmbedding) -> MemoryEmbeddingModel:
        return MemoryEmbeddingModel(
            id=item.id,
            tenant_id=item.tenant_id,
            memory_id=item.memory_id,
            embedding_version=item.embedding_version,
            dimensions=item.dimensions,
            embedding=list(item.vector),
            active=item.active,
            created_at=item.created_at,
        )

    @staticmethod
    def _relationship_model(item: Relationship) -> RelationshipModel:
        return RelationshipModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            user_id=item.user_id,
            stage=item.stage.value,
            affinity=item.affinity,
            trust=item.trust,
            familiarity=item.familiarity,
            interaction_count=item.interaction_count,
            summary=item.summary,
            boundaries=list(item.boundaries),
            version=item.version,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _relationship(row: RelationshipModel) -> Relationship:
        return Relationship(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            user_id=row.user_id,
            stage=RelationshipStage(row.stage),
            affinity=row.affinity,
            trust=row.trust,
            familiarity=row.familiarity,
            interaction_count=row.interaction_count,
            summary=row.summary,
            boundaries=tuple(row.boundaries),
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _relationship_event_model(item: RelationshipEvent) -> RelationshipEventModel:
        return RelationshipEventModel(
            id=item.id,
            tenant_id=item.tenant_id,
            relationship_id=item.relationship_id,
            event_type=item.event_type,
            affinity_delta=item.affinity_delta,
            trust_delta=item.trust_delta,
            familiarity_delta=item.familiarity_delta,
            evidence_memory_id=item.evidence_memory_id,
            summary=item.summary,
            created_by=item.created_by,
            created_at=item.created_at,
        )

    @staticmethod
    def _relationship_event(row: RelationshipEventModel) -> RelationshipEvent:
        return RelationshipEvent(
            id=row.id,
            tenant_id=row.tenant_id,
            relationship_id=row.relationship_id,
            event_type=row.event_type,
            affinity_delta=row.affinity_delta,
            trust_delta=row.trust_delta,
            familiarity_delta=row.familiarity_delta,
            evidence_memory_id=row.evidence_memory_id,
            summary=row.summary,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    @staticmethod
    def _index_job_model(item: MemoryIndexJob) -> MemoryIndexJobModel:
        return MemoryIndexJobModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            user_id=item.user_id,
            target_embedding_version=item.target_embedding_version,
            status=item.status.value,
            total_items=item.total_items,
            processed_items=item.processed_items,
            error_code=item.error_code,
            created_by=item.created_by,
            created_at=item.created_at,
            started_at=item.started_at,
            completed_at=item.completed_at,
        )

    @staticmethod
    def _index_job(row: MemoryIndexJobModel) -> MemoryIndexJob:
        return MemoryIndexJob(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            user_id=row.user_id,
            target_embedding_version=row.target_embedding_version,
            status=MemoryIndexJobStatus(row.status),
            total_items=row.total_items,
            processed_items=row.processed_items,
            error_code=row.error_code,
            created_by=row.created_by,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )
