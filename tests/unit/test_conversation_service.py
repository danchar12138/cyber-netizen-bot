"""最小对话应用服务的幂等、流式与恢复测试。"""

from collections.abc import AsyncIterator
from uuid import NAMESPACE_DNS, uuid5

from cnb_application import (
    ConfigurationService,
    ConversationService,
    StaticModelProviderResolver,
    build_default_registry,
)
from cnb_cognition import (
    MinimalCognitiveRuntime,
    ModelCapabilities,
    ModelRequest,
    ModelStreamEvent,
    ModelUsage,
)
from cnb_domain import (
    AgentRunStatus,
    ConversationStatus,
    DevelopmentIdentity,
    MessageFeedbackRating,
    MessageStatus,
)
from cnb_infrastructure import MemoryConfigurationRepository, MemoryConversationRepository


class StubModelProvider:
    """产生两个确定性增量的测试 Provider。"""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def model(self) -> str:
        return "stub-v1"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(True, False, False, False)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        assert request.messages[-1].content.startswith("你好")
        yield ModelStreamEvent(delta="你")
        yield ModelStreamEvent(delta="好呀")
        yield ModelStreamEvent(usage=ModelUsage(input_tokens=2, output_tokens=3))


def _identity() -> DevelopmentIdentity:
    return DevelopmentIdentity(
        tenant_id=uuid5(NAMESPACE_DNS, "test.tenant"),
        user_id=uuid5(NAMESPACE_DNS, "test.user"),
        agent_id=uuid5(NAMESPACE_DNS, "test.agent"),
        user_name="测试用户",
        agent_name="测试 Agent",
    )


def _service(repository: MemoryConversationRepository) -> ConversationService:
    return ConversationService(
        repository=repository,
        runtime=MinimalCognitiveRuntime(),
        model_provider_resolver=StaticModelProviderResolver(StubModelProvider()),
        configuration_service=ConfigurationService(
            build_default_registry(), MemoryConfigurationRepository()
        ),
        identity=_identity(),
    )


async def test_message_idempotency_does_not_create_duplicate_runs() -> None:
    repository = MemoryConversationRepository()
    service = _service(repository)
    conversation = await service.create_conversation(title="幂等测试")
    client_message_id = uuid5(NAMESPACE_DNS, "test.client-message")

    first = await service.send_message(
        conversation.id, client_message_id=client_message_id, content="你好"
    )
    replay = await service.send_message(
        conversation.id, client_message_id=client_message_id, content="这段内容应被忽略"
    )

    assert first.created is True
    assert replay.created is False
    assert replay.run.id == first.run.id
    messages = await service.list_messages(conversation.id, limit=20, cursor=None)
    assert len(messages.items) == 2


async def test_streamed_run_persists_message_usage_and_ordered_events() -> None:
    repository = MemoryConversationRepository()
    service = _service(repository)
    conversation = await service.create_conversation(title="流式测试")
    pending = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.streaming-message"),
        content=" 你好 ",
    )

    await service.execute_run(pending)

    messages = await service.list_messages(conversation.id, limit=20, cursor=None)
    assert messages.items[-1].content == "你好呀"
    assert messages.items[-1].status is MessageStatus.COMPLETED
    events = await service.list_events(conversation.id, after_sequence=0)
    assert [item.sequence for item in events] == list(range(1, len(events) + 1))
    assert [item.event_type for item in events][-2:] == [
        "message.completed",
        "run.completed",
    ]
    completed_payload = events[-1].payload
    assert completed_payload["input_tokens"] == 2
    assert completed_payload["output_tokens"] == 3


async def test_event_replay_starts_strictly_after_requested_sequence() -> None:
    repository = MemoryConversationRepository()
    service = _service(repository)
    conversation = await service.create_conversation(title="重放测试")
    pending = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.replay-message"),
        content="你好",
    )
    await service.execute_run(pending)

    all_events = await service.list_events(conversation.id, after_sequence=0)
    resumed = await service.list_events(conversation.id, after_sequence=4)

    assert resumed == tuple(item for item in all_events if item.sequence > 4)


async def test_cancel_is_idempotent_and_prevents_execution() -> None:
    repository = MemoryConversationRepository()
    service = _service(repository)
    conversation = await service.create_conversation(title="取消测试")
    pending = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.cancel-message"),
        content="你好",
    )

    first = await service.cancel_run(pending.run.id)
    replay = await service.cancel_run(pending.run.id)
    await service.execute_run(pending)

    assert first.status is AgentRunStatus.CANCELLED
    assert replay.status is AgentRunStatus.CANCELLED
    messages = await service.list_messages(conversation.id, limit=20, cursor=None)
    assert messages.items[-1].status is MessageStatus.CANCELLED


async def test_conversation_cursor_pagination_has_no_overlap() -> None:
    repository = MemoryConversationRepository()
    service = _service(repository)
    for title in ("第一段", "第二段", "第三段"):
        await service.create_conversation(title=title)

    first = await service.list_conversations(limit=2, cursor=None)
    second = await service.list_conversations(limit=2, cursor=first.next_cursor)

    assert len(first.items) == 2
    assert first.next_cursor is not None
    assert len(second.items) == 1
    assert {item.id for item in first.items}.isdisjoint(item.id for item in second.items)


async def test_conversation_can_be_searched_updated_archived_and_soft_deleted() -> None:
    repository = MemoryConversationRepository()
    service = _service(repository)
    conversation = await service.create_conversation(title="周末读书计划")
    await service.create_conversation(title="工作记录")

    searched = await service.list_conversations(
        limit=20, cursor=None, search="读书", status=ConversationStatus.ACTIVE
    )
    updated = await service.update_conversation(
        conversation.id,
        title="周末阅读计划",
        status=ConversationStatus.ARCHIVED,
        pinned=True,
    )
    archived = await service.list_conversations(
        limit=20, cursor=None, status=ConversationStatus.ARCHIVED
    )
    deleted = await service.soft_delete_conversation(conversation.id)
    remaining = await service.list_conversations(limit=20, cursor=None)

    assert [item.id for item in searched.items] == [conversation.id]
    assert updated.title == "周末阅读计划"
    assert updated.status is ConversationStatus.ARCHIVED
    assert updated.archived_at is not None
    assert updated.pinned_at is not None
    assert archived.items[0].id == conversation.id
    assert deleted.deleted_at is not None
    assert all(item.id != conversation.id for item in remaining.items)


async def test_regeneration_is_idempotent_and_keeps_the_original_response() -> None:
    repository = MemoryConversationRepository()
    service = _service(repository)
    conversation = await service.create_conversation(title="重新生成测试")
    original = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.regeneration-message"),
        content="你好",
    )
    await service.execute_run(original)
    request_id = uuid5(NAMESPACE_DNS, "test.regeneration-request")

    regenerated = await service.regenerate_response(
        original.response_message.id, client_request_id=request_id
    )
    replay = await service.regenerate_response(
        original.response_message.id, client_request_id=request_id
    )
    await service.execute_run(regenerated)
    messages = await service.list_messages(conversation.id, limit=20, cursor=None)

    assert regenerated.created is True
    assert replay.created is False
    assert replay.run.id == regenerated.run.id
    assert original.response_message.id != regenerated.response_message.id
    assert [item.sender_type.value for item in messages.items] == ["user", "agent", "agent"]
    assert messages.items[-2].content == "你好呀"
    assert messages.items[-1].content == "你好呀"


async def test_editing_user_message_creates_an_independent_branch() -> None:
    repository = MemoryConversationRepository()
    service = _service(repository)
    conversation = await service.create_conversation(title="分支测试")
    original = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.branch-source"),
        content="你好",
    )
    await service.execute_run(original)
    branch = await service.edit_message_as_branch(
        original.trigger_message.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.branch-edit"),
        content="你好",
    )
    await service.execute_run(branch)

    original_messages = await service.list_messages(conversation.id, limit=20, cursor=None)
    branch_messages = await service.list_messages(branch.conversation.id, limit=20, cursor=None)

    assert branch.conversation.id != conversation.id
    assert branch.conversation.branched_from_conversation_id == conversation.id
    assert branch.trigger_message.edited_from_id == original.trigger_message.id
    assert len(original_messages.items) == 2
    assert len(branch_messages.items) == 2
    assert branch_messages.items[-1].status is MessageStatus.COMPLETED


async def test_feedback_and_full_text_search_stay_inside_accessible_conversations() -> None:
    repository = MemoryConversationRepository()
    service = _service(repository)
    conversation = await service.create_conversation(title="反馈搜索测试")
    pending = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.feedback-message"),
        content="你好",
    )
    await service.execute_run(pending)

    feedback = await service.set_message_feedback(
        pending.response_message.id,
        rating=MessageFeedbackRating.POSITIVE,
        comment=" 很自然 ",
    )
    listed = await service.list_message_feedback(conversation.id)
    search_results = await service.search_messages(query="好呀", conversation_id=None, limit=20)
    await service.delete_message_feedback(pending.response_message.id)

    assert feedback.comment == "很自然"
    assert listed == (feedback,)
    assert search_results[0].conversation.id == conversation.id
    assert search_results[0].message.id == pending.response_message.id
    assert await service.list_message_feedback(conversation.id) == ()
