"""长期记忆、混合召回、遗忘和关系连续性的领域测试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_application import MemoryService, MemorySourceDraft, MemoryValidationError
from cnb_cognition import DeterministicHashEmbedding, HybridMemoryRanker, HybridRecallWeights
from cnb_domain import (
    MemoryConfirmation,
    MemoryDetail,
    MemoryKind,
    MemorySensitivity,
    MemorySourceKind,
    MemoryStatus,
    MemoryVisibility,
    RawMemoryCandidate,
    RelationshipStage,
)
from cnb_infrastructure import InMemoryMemoryRepository

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_TENANT_ID = UUID("10000000-0000-0000-0000-000000000002")
AGENT_ID = UUID("20000000-0000-0000-0000-000000000001")
USER_ID = UUID("30000000-0000-0000-0000-000000000001")
OTHER_USER_ID = UUID("30000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def source(*, excerpt: str | None = None, verbatim: bool = False) -> MemorySourceDraft:
    return MemorySourceDraft(
        kind=MemorySourceKind.USER_STATEMENT,
        source_id=f"message:{uuid4()}",
        excerpt=excerpt,
        is_verbatim=verbatim,
        occurred_at=NOW,
    )


async def create_test_memory(
    service: MemoryService,
    *,
    kind: MemoryKind = MemoryKind.SEMANTIC,
    content: str = "用户喜欢在周末徒步",
    tenant_id: UUID = TENANT_ID,
    user_id: UUID = USER_ID,
    conversation_id: UUID | None = None,
    event_at: datetime = NOW,
    importance: float = 0.6,
) -> MemoryDetail:
    return await service.create_memory(
        tenant_id=tenant_id,
        agent_id=AGENT_ID,
        user_id=user_id,
        conversation_id=conversation_id,
        episode_id=None,
        kind=kind,
        visibility=MemoryVisibility.USER,
        content=content,
        event_at=event_at,
        confidence=0.7,
        importance=importance,
        emotional_weight=0.1,
        sensitivity=MemorySensitivity.NORMAL,
        confirmation=MemoryConfirmation.UNCONFIRMED,
        sources=(source(),),
        actor_id=USER_ID,
    )


def test_hash_embedding_is_stable_normalized_and_versioned() -> None:
    encoder = DeterministicHashEmbedding()

    first = encoder.encode("Hello 世界")
    second = encoder.encode(" hello世界 ")

    assert first == second
    assert encoder.version == "local-hash-v1"
    assert len(first) == 256
    assert sum(value * value for value in first) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_all_six_memory_kinds_require_traceable_source() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)

    for kind in MemoryKind:
        await create_test_memory(service, kind=kind, content=f"{kind.value} 类型内容")

    listed = await service.list_memories(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        user_id=USER_ID,
        status=MemoryStatus.ACTIVE,
        kind=None,
        query=None,
        limit=20,
    )
    assert {item.kind for item in listed} == set(MemoryKind)

    with pytest.raises(MemoryValidationError, match="至少需要一个可追溯来源"):
        await service.create_memory(
            tenant_id=TENANT_ID,
            agent_id=AGENT_ID,
            user_id=USER_ID,
            conversation_id=None,
            episode_id=None,
            kind=MemoryKind.SEMANTIC,
            visibility=MemoryVisibility.USER,
            content="没有来源的内容",
            event_at=NOW,
            confidence=0.5,
            importance=0.5,
            emotional_weight=0,
            sensitivity=MemorySensitivity.NORMAL,
            confirmation=MemoryConfirmation.UNCONFIRMED,
            sources=(),
            actor_id=USER_ID,
        )


@pytest.mark.asyncio
async def test_inference_source_never_claims_verbatim_and_verbatim_requires_excerpt() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    detail = await service.create_memory(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        user_id=USER_ID,
        conversation_id=None,
        episode_id=None,
        kind=MemoryKind.SEMANTIC,
        visibility=MemoryVisibility.USER,
        content="用户可能偏好安静的交流",
        event_at=NOW,
        confidence=0.45,
        importance=0.4,
        emotional_weight=0,
        sensitivity=MemorySensitivity.PERSONAL,
        confirmation=MemoryConfirmation.UNCONFIRMED,
        sources=(source(excerpt="模型根据多轮交流形成的摘要", verbatim=False),),
        actor_id=USER_ID,
    )
    assert detail.sources[0].is_verbatim is False

    with pytest.raises(MemoryValidationError, match="逐字来源必须提供来源摘录"):
        await service.create_memory(
            tenant_id=TENANT_ID,
            agent_id=AGENT_ID,
            user_id=USER_ID,
            conversation_id=None,
            episode_id=None,
            kind=MemoryKind.EPISODIC,
            visibility=MemoryVisibility.USER,
            content="无摘录原话",
            event_at=NOW,
            confidence=0.8,
            importance=0.7,
            emotional_weight=0,
            sensitivity=MemorySensitivity.NORMAL,
            confirmation=MemoryConfirmation.UNCONFIRMED,
            sources=(source(verbatim=True),),
            actor_id=USER_ID,
        )


@pytest.mark.asyncio
async def test_correction_preserves_lineage_and_forget_removes_plaintext_and_embedding() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    original = await service.create_memory(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        user_id=USER_ID,
        conversation_id=None,
        episode_id=None,
        kind=MemoryKind.SEMANTIC,
        visibility=MemoryVisibility.USER,
        content="用户喜欢咖啡",
        event_at=NOW,
        confidence=0.7,
        importance=0.6,
        emotional_weight=0,
        sensitivity=MemorySensitivity.NORMAL,
        confirmation=MemoryConfirmation.UNCONFIRMED,
        sources=(source(excerpt="我喜欢咖啡", verbatim=True),),
        actor_id=USER_ID,
    )

    corrected = await service.correct_memory(
        memory_id=original.memory.id,
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        content="用户更喜欢茶，不喜欢咖啡",
        event_at=NOW + timedelta(days=1),
        actor_id=USER_ID,
        note="用户纠正",
    )
    assert corrected.memory.lineage_id == original.memory.lineage_id
    assert corrected.memory.version == 2
    assert repository.memories[original.memory.id].status is MemoryStatus.SUPERSEDED
    assert corrected.links[0].target_memory_id == original.memory.id

    forgotten = await service.forget_memory(
        memory_id=corrected.memory.id,
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        actor_id=USER_ID,
    )
    forgotten_detail = await service.get_memory_detail(
        memory_id=forgotten.id,
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
    )
    assert forgotten.content is None
    assert forgotten.status is MemoryStatus.FORGOTTEN
    assert forgotten.embedding_version is None
    assert all(item.excerpt is None and not item.is_verbatim for item in forgotten_detail.sources)
    assert forgotten.id not in repository.embeddings


@pytest.mark.asyncio
async def test_recall_isolates_tenant_and_user_while_crossing_conversations() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    first_conversation = uuid4()
    second_conversation = uuid4()
    own = await create_test_memory(
        service,
        content="用户的猫叫月饼",
        conversation_id=first_conversation,
    )
    await create_test_memory(
        service,
        content="另一个用户的猫叫秘密",
        user_id=OTHER_USER_ID,
        conversation_id=second_conversation,
    )
    await create_test_memory(
        service,
        content="另一个租户的猫叫泄漏",
        tenant_id=OTHER_TENANT_ID,
        conversation_id=second_conversation,
    )

    recalled = await service.recall(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        user_id=USER_ID,
        query="我的猫叫什么",
        limit=10,
        candidate_pool=20,
        maximum_sensitivity=MemorySensitivity.NORMAL,
        recency_half_life_days=30,
        weights=HybridRecallWeights(),
        now=NOW,
    )
    assert [item.memory.id for item in recalled] == [own.memory.id]
    assert recalled[0].memory.conversation_id == first_conversation


@pytest.mark.asyncio
async def test_hybrid_ranker_applies_time_importance_and_confirmation() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    old_detail = await create_test_memory(
        service,
        content="同一主题旧记忆",
        event_at=NOW - timedelta(days=180),
        importance=0.2,
    )
    new_detail = await create_test_memory(
        service,
        content="同一主题新记忆",
        event_at=NOW,
        importance=0.9,
    )
    new_memory = await service.set_confirmation(
        memory_id=new_detail.memory.id,
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        confirmation=MemoryConfirmation.CONFIRMED,
        actor_id=USER_ID,
    )
    candidates = (
        RawMemoryCandidate(old_detail.memory, 0.5, 0.5, 0.5),
        RawMemoryCandidate(new_memory, 0.5, 0.5, 0.5),
    )
    ranked = HybridMemoryRanker().rank(
        candidates,
        now=NOW,
        limit=2,
        recency_half_life_days=30,
        weights=HybridRecallWeights(),
    )
    assert ranked[0].memory.event_at == NOW
    assert ranked[0].components["recency"] == 1.0
    old_recency = ranked[1].components["recency"]
    assert isinstance(old_recency, (int, float))
    assert old_recency < 0.02


@pytest.mark.asyncio
async def test_relationship_stage_and_embedding_rebuild_are_observable() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    memory = await create_test_memory(service)

    relationship = await service.record_relationship_event(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        user_id=USER_ID,
        event_type="user_confirmed_memory",
        affinity_delta=0.8,
        trust_delta=0.8,
        familiarity_delta=0.8,
        summary="经过多次交流，双方形成稳定信任。",
        boundaries=("不主动追问敏感信息",),
        evidence_memory_id=memory.memory.id,
        actor_id=USER_ID,
    )
    assert relationship.relationship.stage is RelationshipStage.TRUSTED
    assert relationship.relationship.version == 1
    assert relationship.events[0].evidence_memory_id == memory.memory.id

    job = await service.rebuild_embeddings(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        user_id=USER_ID,
        actor_id=USER_ID,
    )
    assert job.status.value == "completed"
    assert job.processed_items == job.total_items == 1
    assert (
        await service.list_index_jobs(
            tenant_id=TENANT_ID,
            agent_id=AGENT_ID,
            limit=10,
        )
    )[0] == job
