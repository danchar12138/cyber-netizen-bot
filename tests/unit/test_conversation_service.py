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
from cnb_domain import AgentRunStatus, DevelopmentIdentity, MessageStatus
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
        assert request.messages[-1].content == "你好"
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
