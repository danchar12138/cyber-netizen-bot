"""最小对话应用服务的幂等、流式与恢复测试。"""

from collections.abc import AsyncIterator
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from cnb_application import (
    BackgroundTaskService,
    CognitionService,
    ConfigurationService,
    ConversationService,
    ModelProviderResolver,
    ModelReliabilityGuard,
    StaticModelProviderResolver,
    build_default_registry,
)
from cnb_cognition import (
    UNTRUSTED_CONTEXT_POLICY,
    MinimalCognitiveRuntime,
    ModelCapabilities,
    ModelProvider,
    ModelRequest,
    ModelStreamEvent,
    ModelTextInput,
    ModelUsage,
    read_untrusted_content,
)
from cnb_domain import (
    AgentRunStatus,
    BackgroundJobKind,
    CognitionResourceKind,
    ConfigEntry,
    ConfigScope,
    ConversationStatus,
    DevelopmentIdentity,
    InvocationStatus,
    JsonValue,
    MessageFeedbackRating,
    MessageStatus,
)
from cnb_infrastructure import (
    InMemoryTaskRepository,
    MemoryCognitionRepository,
    MemoryConfigurationRepository,
    MemoryConversationRepository,
)


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
        text = request.messages[-1].content[0]
        assert isinstance(text, ModelTextInput)
        assert text.text.startswith("<untrusted_context>")
        assert read_untrusted_content(text.text).startswith("你好")
        assert request.instructions.startswith(UNTRUSTED_CONTEXT_POLICY)
        yield ModelStreamEvent(delta="你")
        yield ModelStreamEvent(delta="好呀")
        yield ModelStreamEvent(usage=ModelUsage(input_tokens=2, output_tokens=3))


class ScriptedModelProvider:
    """按脚本成功、超时或在流式增量前后失败。"""

    def __init__(self, name: str, model: str, outcome: str) -> None:
        self._name = name
        self._model = model
        self._outcome = outcome
        self.stream_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(True, False, False, False)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        self.stream_calls += 1
        if self._outcome == "fail_before_output":
            raise RuntimeError("模型在输出前失败")
        if self._outcome == "timeout":
            raise TimeoutError("模型调用超时")
        yield ModelStreamEvent(delta=f"{self._name} 回复")
        if self._outcome == "fail_after_output":
            raise RuntimeError("模型在输出后失败")
        yield ModelStreamEvent(usage=ModelUsage(input_tokens=4, output_tokens=2))


class RoutingModelProviderResolver:
    """按发布路由返回脚本 Provider，并记录解析顺序。"""

    def __init__(self, providers: tuple[ScriptedModelProvider, ...]) -> None:
        self._providers = {(item.name, item.model): item for item in providers}
        self.calls: list[tuple[str, str]] = []

    async def resolve(
        self,
        *,
        provider: str,
        model: str,
        tenant_id: object,
        agent_id: object | None = None,
        channel_id: object | None = None,
        user_id: object | None = None,
        run_id: object | None = None,
    ) -> ModelProvider:
        del tenant_id, agent_id, channel_id, user_id, run_id
        self.calls.append((provider, model))
        return self._providers[(provider, model)]


class ChannelRecordingResolver:
    """记录模型凭证解析收到的渠道作用域。"""

    def __init__(self) -> None:
        self.provider = StubModelProvider()
        self.calls: list[tuple[str, str, object | None]] = []

    async def resolve(
        self,
        *,
        provider: str,
        model: str,
        tenant_id: object,
        agent_id: object | None = None,
        channel_id: object | None = None,
        user_id: object | None = None,
        run_id: object | None = None,
    ) -> ModelProvider:
        del tenant_id, agent_id, user_id, run_id
        self.calls.append((provider, model, channel_id))
        return self.provider


def _identity() -> DevelopmentIdentity:
    return DevelopmentIdentity(
        tenant_id=uuid5(NAMESPACE_DNS, "test.tenant"),
        user_id=uuid5(NAMESPACE_DNS, "test.user"),
        agent_id=uuid5(NAMESPACE_DNS, "test.agent"),
        user_name="测试用户",
        agent_name="测试 Agent",
    )


def _service(
    repository: MemoryConversationRepository,
    *,
    resolver: ModelProviderResolver | None = None,
    cognition_repository: MemoryCognitionRepository | None = None,
    configuration_service: ConfigurationService | None = None,
    reliability_guard: ModelReliabilityGuard | None = None,
    channel_id: UUID | None = None,
    task_service: BackgroundTaskService | None = None,
) -> ConversationService:
    identity = _identity()
    return ConversationService(
        repository=repository,
        runtime=MinimalCognitiveRuntime(),
        model_provider_resolver=(resolver or StaticModelProviderResolver(StubModelProvider())),
        configuration_service=(
            configuration_service
            or ConfigurationService(build_default_registry(), MemoryConfigurationRepository())
        ),
        cognition_service=CognitionService(
            cognition_repository or MemoryCognitionRepository(), agent_id=identity.agent_id
        ),
        identity=identity,
        channel_id=channel_id,
        task_service=task_service,
        reliability_guard=reliability_guard,
    )


async def _publish_model_route(
    service: CognitionService,
    *,
    profiles: tuple[tuple[str, str, str], ...],
    max_attempts: int,
) -> None:
    identity = _identity()
    references: list[JsonValue] = []
    for key, provider, model in profiles:
        profile_payload: dict[str, JsonValue] = {
            "provider": provider,
            "model": model,
            "purposes": ["chat.realizer"],
        }
        draft = await service.create_draft(
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            kind=CognitionResourceKind.MODEL_PROFILE,
            key=key,
            name=f"{key} 模型档案",
            payload=profile_payload,
            note=None,
            actor_id=identity.user_id,
        )
        await service.publish(
            resource_id=draft.id,
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            actor_id=identity.user_id,
        )
        references.append({"key": key, "version": draft.version})
    route_payload: dict[str, JsonValue] = {
        "purpose": "chat.realizer",
        "primary_profile": references[0],
        "fallback_profiles": references[1:],
        "timeout_seconds": 1,
        "max_attempts": max_attempts,
    }
    route = await service.create_draft(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        kind=CognitionResourceKind.MODEL_ROUTE,
        key="chat.realizer",
        name="对话模型路由",
        payload=route_payload,
        note=None,
        actor_id=identity.user_id,
    )
    await service.publish(
        resource_id=route.id,
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        actor_id=identity.user_id,
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


async def test_channel_scope_reaches_run_snapshot_runtime_and_provider_resolution() -> None:
    repository = MemoryConversationRepository()
    configuration = ConfigurationService(
        build_default_registry(),
        MemoryConfigurationRepository(),
    )
    channel_id = uuid4()
    draft = await configuration.create_draft(
        note="渠道模型覆盖",
        values=(
            ConfigEntry(
                key="model.chat.provider",
                scope_type=ConfigScope.CHANNEL,
                scope_id=channel_id,
                value="openai",
            ),
            ConfigEntry(
                key="model.openai.model",
                scope_type=ConfigScope.CHANNEL,
                scope_id=channel_id,
                value="channel-model-v1",
            ),
        ),
    )
    await configuration.publish(draft.id)
    resolver = ChannelRecordingResolver()
    service = _service(
        repository,
        resolver=resolver,
        configuration_service=configuration,
        channel_id=channel_id,
    )
    conversation = await service.create_conversation(title="渠道作用域")

    pending = await service.send_message(
        conversation.id,
        client_message_id=uuid4(),
        content="你好，验证渠道配置",
    )
    completed = await service.execute_run(pending)

    assert pending.run.model_profile == "openai/channel-model-v1"
    assert completed.status is AgentRunStatus.COMPLETED
    assert resolver.calls == [("openai", "channel-model-v1", channel_id)]


async def test_reflection_job_only_contains_stable_resource_ids() -> None:
    repository = MemoryConversationRepository()
    task_repository = InMemoryTaskRepository()
    task_service = BackgroundTaskService(task_repository)
    service = _service(repository, task_service=task_service)
    conversation = await service.create_conversation(title="反思任务数据最小化")
    pending = await service.send_message(
        conversation.id,
        client_message_id=uuid4(),
        content="你好，请记住我喜欢清晨散步。",
    )

    await service.execute_run(pending)

    jobs = await task_service.list_jobs(
        tenant_id=_identity().tenant_id,
        status=None,
        kind=BackgroundJobKind.REFLECTION,
        limit=20,
    )
    assert len(jobs) == 1
    assert jobs[0].payload == {
        "run_id": str(pending.run.id),
        "agent_id": str(_identity().agent_id),
        "user_id": str(_identity().user_id),
        "conversation_id": str(conversation.id),
        "trigger_message_id": str(pending.trigger_message.id),
        "response_message_id": str(pending.response_message.id),
    }
    assert pending.trigger_message.content not in str(jobs[0].payload)


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
    assert messages.items[0].parts[0].kind.value == "markdown"
    assert messages.items[0].parts[0].text == "你好"
    assert messages.items[-1].parts[0].text == "你好呀"
    events = await service.list_events(conversation.id, after_sequence=0)
    assert [item.sequence for item in events] == list(range(1, len(events) + 1))
    assert [item.event_type for item in events][-2:] == [
        "message.completed",
        "run.completed",
    ]
    completed_payload = events[-1].payload
    assert completed_payload["input_tokens"] == 2
    assert completed_payload["output_tokens"] == 3
    parts_payload = events[-2].payload["parts"]
    assert isinstance(parts_payload, list)
    assert isinstance(parts_payload[0], dict)
    assert parts_payload[0]["text"] == "你好呀"


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
    preface = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.branch-preface"),
        content="先聊一句",
    )
    await service.execute_run(preface)
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
    assert len(original_messages.items) == 4
    assert len(branch_messages.items) == 4
    assert branch_messages.items[0].parts[0].text == "先聊一句"
    assert branch_messages.items[0].parts[0].id != original_messages.items[0].parts[0].id
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


async def test_model_failure_before_output_falls_back_and_records_each_attempt() -> None:
    conversation_repository = MemoryConversationRepository()
    cognition_repository = MemoryCognitionRepository()
    cognition = CognitionService(cognition_repository, agent_id=_identity().agent_id)
    await _publish_model_route(
        cognition,
        profiles=(
            ("primary", "primary-provider", "primary-model"),
            ("fallback", "fallback-provider", "fallback-model"),
        ),
        max_attempts=2,
    )
    primary = ScriptedModelProvider("primary-provider", "primary-model", "fail_before_output")
    fallback = ScriptedModelProvider("fallback-provider", "fallback-model", "success")
    resolver = RoutingModelProviderResolver((primary, fallback))
    service = _service(
        conversation_repository,
        resolver=resolver,
        cognition_repository=cognition_repository,
    )
    conversation = await service.create_conversation(title="模型降级测试")
    pending = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.model-fallback"),
        content="你好",
    )

    await service.execute_run(pending)

    messages = await service.list_messages(conversation.id, limit=20, cursor=None)
    trace = await cognition.get_run_trace(run_id=pending.run.id, tenant_id=_identity().tenant_id)
    assert messages.items[-1].content == "fallback-provider 回复"
    assert messages.items[-1].status is MessageStatus.COMPLETED
    assert resolver.calls == [
        ("primary-provider", "primary-model"),
        ("fallback-provider", "fallback-model"),
    ]
    assert [item.status for item in trace.model_invocations] == [
        InvocationStatus.FAILED,
        InvocationStatus.COMPLETED,
    ]


async def test_model_failure_after_delta_is_not_retried() -> None:
    conversation_repository = MemoryConversationRepository()
    cognition_repository = MemoryCognitionRepository()
    cognition = CognitionService(cognition_repository, agent_id=_identity().agent_id)
    await _publish_model_route(
        cognition,
        profiles=(
            ("primary", "primary-provider", "primary-model"),
            ("fallback", "fallback-provider", "fallback-model"),
        ),
        max_attempts=2,
    )
    primary = ScriptedModelProvider("primary-provider", "primary-model", "fail_after_output")
    fallback = ScriptedModelProvider("fallback-provider", "fallback-model", "success")
    resolver = RoutingModelProviderResolver((primary, fallback))
    service = _service(
        conversation_repository,
        resolver=resolver,
        cognition_repository=cognition_repository,
    )
    conversation = await service.create_conversation(title="流式失败测试")
    pending = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.model-stream-failure"),
        content="你好",
    )

    await service.execute_run(pending)

    messages = await service.list_messages(conversation.id, limit=20, cursor=None)
    trace = await cognition.get_run_trace(run_id=pending.run.id, tenant_id=_identity().tenant_id)
    assert messages.items[-1].status is MessageStatus.FAILED
    assert resolver.calls == [("primary-provider", "primary-model")]
    assert fallback.stream_calls == 0
    assert [item.status for item in trace.model_invocations] == [InvocationStatus.FAILED]


async def test_model_timeout_is_recorded_and_route_attempt_limit_is_global() -> None:
    conversation_repository = MemoryConversationRepository()
    cognition_repository = MemoryCognitionRepository()
    cognition = CognitionService(cognition_repository, agent_id=_identity().agent_id)
    await _publish_model_route(
        cognition,
        profiles=(
            ("primary", "primary-provider", "primary-model"),
            ("fallback", "fallback-provider", "fallback-model"),
            ("unused", "unused-provider", "unused-model"),
        ),
        max_attempts=2,
    )
    primary = ScriptedModelProvider("primary-provider", "primary-model", "timeout")
    fallback = ScriptedModelProvider("fallback-provider", "fallback-model", "fail_before_output")
    unused = ScriptedModelProvider("unused-provider", "unused-model", "success")
    resolver = RoutingModelProviderResolver((primary, fallback, unused))
    service = _service(
        conversation_repository,
        resolver=resolver,
        cognition_repository=cognition_repository,
    )
    conversation = await service.create_conversation(title="超时与总尝试预算测试")
    pending = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.model-timeout"),
        content="你好",
    )

    await service.execute_run(pending)

    trace = await cognition.get_run_trace(run_id=pending.run.id, tenant_id=_identity().tenant_id)
    assert resolver.calls == [
        ("primary-provider", "primary-model"),
        ("fallback-provider", "fallback-model"),
    ]
    assert unused.stream_calls == 0
    assert [item.status for item in trace.model_invocations] == [
        InvocationStatus.TIMED_OUT,
        InvocationStatus.FAILED,
    ]


async def test_open_circuit_skips_provider_until_cooldown() -> None:
    conversation_repository = MemoryConversationRepository()
    cognition_repository = MemoryCognitionRepository()
    cognition = CognitionService(cognition_repository, agent_id=_identity().agent_id)
    await _publish_model_route(
        cognition,
        profiles=(("primary", "primary-provider", "primary-model"),),
        max_attempts=1,
    )
    configuration = ConfigurationService(build_default_registry(), MemoryConfigurationRepository())
    draft = await configuration.create_draft(
        note="测试一次失败立即熔断",
        values=(
            ConfigEntry(
                key="model.chat.circuit_breaker_failures",
                scope_type=ConfigScope.SYSTEM,
                value=1,
            ),
        ),
    )
    await configuration.publish(draft.id)
    primary = ScriptedModelProvider("primary-provider", "primary-model", "fail_before_output")
    resolver = RoutingModelProviderResolver((primary,))
    service = _service(
        conversation_repository,
        resolver=resolver,
        cognition_repository=cognition_repository,
        configuration_service=configuration,
    )
    conversation = await service.create_conversation(title="熔断测试")
    first = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.circuit-first"),
        content="你好",
    )
    await service.execute_run(first)
    second = await service.send_message(
        conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "test.circuit-second"),
        content="你好",
    )

    await service.execute_run(second)

    second_trace = await cognition.get_run_trace(
        run_id=second.run.id, tenant_id=_identity().tenant_id
    )
    assert primary.stream_calls == 1
    assert resolver.calls == [("primary-provider", "primary-model")]
    assert second_trace.model_invocations[0].error_code == "CircuitOpen"


async def test_conversation_repository_isolates_same_user_by_agent() -> None:
    repository = MemoryConversationRepository()
    first = _identity()
    second = DevelopmentIdentity(
        tenant_id=first.tenant_id,
        user_id=first.user_id,
        agent_id=uuid4(),
        user_name=first.user_name,
        agent_name="第二个 Agent",
    )
    await repository.ensure_development_identity(first)
    await repository.ensure_development_identity(second)
    first_conversation = await repository.create_conversation(
        identity=first,
        title="第一个 Agent 的会话",
    )
    second_conversation = await repository.create_conversation(
        identity=second,
        title="第二个 Agent 的会话",
    )

    first_rows = await repository.list_conversations(
        user_id=first.user_id,
        agent_id=first.agent_id,
        limit=20,
        cursor=None,
        search=None,
        status=None,
    )
    second_rows = await repository.list_conversations(
        user_id=second.user_id,
        agent_id=second.agent_id,
        limit=20,
        cursor=None,
        search=None,
        status=None,
    )

    assert first_rows == (first_conversation,)
    assert second_rows == (second_conversation,)
    assert (
        await repository.get_conversation_for_user(
            first_conversation.id,
            first.user_id,
            second.agent_id,
        )
        is None
    )
