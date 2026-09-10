"""入站 Envelope 到 Conversation 与 Agent Run 的幂等纵向闭环测试。"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_application import (
    AttachmentService,
    BackgroundTaskService,
    CognitionService,
    ConfigurationService,
    ConversationService,
    InboundConversationProcessor,
    InboundConversationServiceFactory,
    InboundMessageTaskHandler,
    PermanentTaskError,
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
    Attachment,
    AttachmentStatus,
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    ChannelPlatform,
    ContentBlockKind,
    DevelopmentIdentity,
    ExternalConversationKind,
    InboundEnvelope,
    JsonValue,
    MessageStatus,
    MultimodalContentBlock,
)
from cnb_infrastructure import (
    InMemoryTaskRepository,
    MemoryAttachmentRepository,
    MemoryCognitionRepository,
    MemoryConfigurationRepository,
    MemoryConversationRepository,
    MemoryObjectStorage,
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
AGENT_ID = UUID("33333333-3333-4333-8333-333333333333")
CHANNEL_ID = UUID("44444444-4444-4444-8444-444444444444")


class CountingModelProvider:
    """记录真正发生的模型调用，便于证明重复投递不会重复输出。"""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "counting"

    @property
    def model(self) -> str:
        return "counting-v1"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(True, False, False, False)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        self.calls += 1
        yield ModelStreamEvent(delta="收到")
        yield ModelStreamEvent(usage=ModelUsage(input_tokens=2, output_tokens=1))


class FailAfterProcessingHandler:
    """模拟 Agent 已完成、任务提交结果前进程失败。"""

    def __init__(self, wrapped: InboundMessageTaskHandler) -> None:
        self._wrapped = wrapped

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        await self._wrapped.handle(job)
        raise RuntimeError("模拟任务结果提交前崩溃")


@dataclass(slots=True)
class ProcessingHarness:
    identity: DevelopmentIdentity
    conversation_id: UUID
    conversations: MemoryConversationRepository
    attachments: MemoryAttachmentRepository
    provider: CountingModelProvider
    handler: InboundMessageTaskHandler
    conversation_services: InboundConversationServiceFactory


async def _harness() -> ProcessingHarness:
    identity = DevelopmentIdentity(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        agent_id=AGENT_ID,
        user_name="入站测试用户",
        agent_name="入站测试 Agent",
    )
    conversations = MemoryConversationRepository()
    attachments = MemoryAttachmentRepository()
    storage = MemoryObjectStorage()
    configuration = ConfigurationService(
        build_default_registry(),
        MemoryConfigurationRepository(),
    )
    cognition = MemoryCognitionRepository()
    provider = CountingModelProvider()
    await conversations.ensure_development_identity(identity)
    conversation = await conversations.create_conversation(identity=identity, title="入站会话")

    def conversation_services(
        *, identity: DevelopmentIdentity, channel_id: UUID
    ) -> ConversationService:
        return ConversationService(
            repository=conversations,
            runtime=MinimalCognitiveRuntime(),
            model_provider_resolver=StaticModelProviderResolver(provider),
            configuration_service=configuration,
            cognition_service=CognitionService(cognition, agent_id=identity.agent_id),
            identity=identity,
            channel_id=channel_id,
        )

    def attachment_services(*, identity: DevelopmentIdentity) -> AttachmentService:
        return AttachmentService(
            repository=attachments,
            object_storage=storage,
            conversation_repository=conversations,
            configuration_service=configuration,
            identity=identity,
        )

    processor = InboundConversationProcessor(
        conversation_services=conversation_services,
        attachment_services=attachment_services,
    )
    return ProcessingHarness(
        identity=identity,
        conversation_id=conversation.id,
        conversations=conversations,
        attachments=attachments,
        provider=provider,
        handler=InboundMessageTaskHandler(processor),
        conversation_services=conversation_services,
    )


def _envelope(
    harness: ProcessingHarness,
    *,
    external_message_id: str = "message-1",
    blocks: tuple[MultimodalContentBlock, ...] | None = None,
) -> InboundEnvelope:
    now = datetime.now(UTC)
    return InboundEnvelope(
        schema_version="1",
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        channel_id=CHANNEL_ID,
        platform=ChannelPlatform.WEB,
        external_event_id=f"event-{uuid4()}",
        external_subject_id="subject-1",
        user_id=USER_ID,
        conversation_kind=ExternalConversationKind.DIRECT,
        external_conversation_id="conversation-1",
        external_thread_id=None,
        conversation_id=harness.conversation_id,
        external_message_id=external_message_id,
        blocks=blocks or (MultimodalContentBlock(kind=ContentBlockKind.TEXT, text="你好\n第二行"),),
        occurred_at=now,
        received_at=now,
    )


def _job(envelope: InboundEnvelope) -> BackgroundJob:
    now = datetime.now(UTC)
    payload: dict[str, JsonValue] = {
        "schema_version": envelope.schema_version,
        "agent_id": str(envelope.agent_id),
        "channel_id": str(envelope.channel_id),
        "platform": envelope.platform.value,
        "external_event_id": envelope.external_event_id,
        "external_subject_id": envelope.external_subject_id,
        "user_id": str(envelope.user_id),
        "conversation_kind": envelope.conversation_kind.value,
        "external_conversation_id": envelope.external_conversation_id,
        "external_thread_id": envelope.external_thread_id,
        "conversation_id": str(envelope.conversation_id),
        "external_message_id": envelope.external_message_id,
        "blocks": [
            {
                "kind": block.kind.value,
                "text": block.text,
                "attachment_id": str(block.attachment_id) if block.attachment_id else None,
                "content_type": block.content_type,
                "file_name": block.file_name,
                "size_bytes": block.size_bytes,
                "sha256": block.sha256,
                "alt_text": block.alt_text,
            }
            for block in envelope.blocks
        ],
        "occurred_at": envelope.occurred_at.isoformat(),
        "received_at": envelope.received_at.isoformat(),
    }
    return BackgroundJob(
        id=uuid4(),
        tenant_id=envelope.tenant_id,
        kind=BackgroundJobKind.INBOUND_MESSAGE,
        queue="inbound",
        status=BackgroundJobStatus.PENDING,
        payload=payload,
        deduplication_key=f"test:{envelope.external_message_id}",
        source_inbox_id=uuid4(),
        correlation_id=None,
        attempt_count=0,
        max_attempts=5,
        lease_seconds=120,
        retry_base_seconds=5,
        available_at=now,
        lease_owner=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        last_error_code=None,
        last_error_summary=None,
        result_summary={},
        replayed_from_id=None,
        created_by=USER_ID,
        created_at=now,
        started_at=None,
        completed_at=None,
        updated_at=now,
    )


async def test_platform_retry_and_worker_redelivery_are_idempotent() -> None:
    harness = await _harness()
    envelope = _envelope(harness)

    first = await harness.handler.handle(_job(envelope))
    platform_retry = await harness.handler.handle(_job(_envelope(harness)))
    worker_redelivery = await harness.handler.handle(_job(envelope))

    assert first["execution_status"] == "agent_run_processed"
    assert first["idempotent_replay"] is False
    assert platform_retry["message_id"] == first["message_id"]
    assert platform_retry["run_id"] == first["run_id"]
    assert platform_retry["idempotent_replay"] is True
    assert worker_redelivery["run_id"] == first["run_id"]
    assert harness.provider.calls == 1
    page = await harness.conversations.list_messages(
        conversation_id=harness.conversation_id,
        user_id=USER_ID,
        limit=20,
        cursor=None,
    )
    assert len(page) == 2
    assert page[0].status is MessageStatus.COMPLETED
    assert page[1].content == "你好\n第二行"


async def test_manual_task_replay_after_post_processing_failure_is_idempotent() -> None:
    harness = await _harness()
    envelope = _envelope(harness, external_message_id="manual-replay-message")
    tasks = BackgroundTaskService(InMemoryTaskRepository())
    queued = await tasks.enqueue(
        tenant_id=TENANT_ID,
        kind=BackgroundJobKind.INBOUND_MESSAGE,
        payload=_job(envelope).payload,
        deduplication_key="inbound:manual-replay-message",
        created_by=USER_ID,
        max_attempts=1,
    )
    failed = await tasks.execute(
        job_id=queued.job.id,
        worker_id="worker-before-crash",
        handlers={BackgroundJobKind.INBOUND_MESSAGE: FailAfterProcessingHandler(harness.handler)},
    )
    assert failed is not None and failed.status is BackgroundJobStatus.DEAD_LETTER

    replayed = await tasks.replay(
        tenant_id=TENANT_ID,
        job_id=failed.id,
        actor_id=USER_ID,
        confirmed=True,
        reason="确认恢复入站任务",
    )
    completed = await tasks.execute(
        job_id=replayed.id,
        worker_id="worker-after-replay",
        handlers={BackgroundJobKind.INBOUND_MESSAGE: harness.handler},
    )

    assert completed is not None and completed.status is BackgroundJobStatus.SUCCEEDED
    assert completed.result_summary["idempotent_replay"] is True
    assert harness.provider.calls == 1
    messages = await harness.conversations.list_messages(
        conversation_id=harness.conversation_id,
        user_id=USER_ID,
        limit=20,
        cursor=None,
    )
    assert len(messages) == 2


async def test_queued_run_resumes_but_interrupted_running_run_does_not_restart_model() -> None:
    harness = await _harness()
    queued_envelope = _envelope(harness, external_message_id="queued-message")
    service = harness.conversation_services(identity=harness.identity, channel_id=CHANNEL_ID)
    queued = await service.send_message(
        harness.conversation_id,
        client_message_id=InboundConversationProcessor.client_message_id(queued_envelope),
        content="等待恢复",
    )

    resumed = await harness.handler.handle(_job(queued_envelope))
    assert resumed["run_id"] == str(queued.run.id)
    assert resumed["run_status"] == AgentRunStatus.COMPLETED.value
    assert harness.provider.calls == 1

    running_envelope = _envelope(harness, external_message_id="running-message")
    running = await service.send_message(
        harness.conversation_id,
        client_message_id=InboundConversationProcessor.client_message_id(running_envelope),
        content="已开始但未完成",
    )
    await harness.conversations.mark_run_started(running.run.id)

    with pytest.raises(PermanentTaskError, match="中断"):
        await harness.handler.handle(_job(running_envelope))
    replay = await service.send_message(
        harness.conversation_id,
        client_message_id=InboundConversationProcessor.client_message_id(running_envelope),
        content="不会覆盖",
    )
    assert replay.run.status is AgentRunStatus.FAILED
    assert replay.run.error_code == "InterruptedInboundRun"
    assert harness.provider.calls == 1


async def test_attachment_order_uses_stored_metadata_and_rejects_tampering() -> None:
    harness = await _harness()
    envelope_seed = _envelope(harness, external_message_id="attachment-message")
    client_message_id = InboundConversationProcessor.client_message_id(envelope_seed)
    now = datetime.now(UTC)
    attachment = await harness.attachments.create_attachment(
        Attachment(
            id=uuid4(),
            tenant_id=TENANT_ID,
            owner_id=USER_ID,
            conversation_id=harness.conversation_id,
            client_message_id=client_message_id,
            message_id=None,
            original_name="photo.png",
            content_type="image/png",
            size_bytes=128,
            sha256="a" * 64,
            object_key="private/test/photo.png",
            status=AttachmentStatus.READY,
            validation_error=None,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            uploaded_at=now,
            attached_at=None,
            deleted_at=None,
        )
    )
    valid = _envelope(
        harness,
        external_message_id="attachment-message",
        blocks=(
            MultimodalContentBlock(kind=ContentBlockKind.TEXT, text="第一段"),
            MultimodalContentBlock(
                kind=ContentBlockKind.IMAGE,
                attachment_id=attachment.id,
                content_type="image/png",
                file_name="photo.png",
                size_bytes=128,
                sha256="a" * 64,
                alt_text="图片说明",
            ),
            MultimodalContentBlock(kind=ContentBlockKind.MARKDOWN, text="**第二段**"),
        ),
    )
    await harness.handler.handle(_job(valid))
    messages = await harness.conversations.list_messages(
        conversation_id=harness.conversation_id,
        user_id=USER_ID,
        limit=20,
        cursor=None,
    )
    trigger = messages[-1]
    assert [part.kind for part in trigger.parts] == [
        ContentBlockKind.TEXT,
        ContentBlockKind.IMAGE,
        ContentBlockKind.MARKDOWN,
    ]
    assert trigger.parts[1].file_name == attachment.original_name
    assert trigger.parts[1].sha256 == attachment.sha256

    tampered_seed = _envelope(harness, external_message_id="tampered-message")
    tampered_attachment = await harness.attachments.create_attachment(
        replace(
            attachment,
            id=uuid4(),
            client_message_id=InboundConversationProcessor.client_message_id(tampered_seed),
            message_id=None,
            status=AttachmentStatus.READY,
            attached_at=None,
        )
    )
    tampered = _envelope(
        harness,
        external_message_id="tampered-message",
        blocks=(
            MultimodalContentBlock(
                kind=ContentBlockKind.IMAGE,
                attachment_id=tampered_attachment.id,
                content_type="image/jpeg",
            ),
        ),
    )
    with pytest.raises(PermanentTaskError, match="附件"):
        await harness.handler.handle(_job(tampered))

    cross_user_seed = _envelope(harness, external_message_id="cross-user-message")
    cross_user_attachment = await harness.attachments.create_attachment(
        replace(
            attachment,
            id=uuid4(),
            owner_id=uuid4(),
            client_message_id=InboundConversationProcessor.client_message_id(cross_user_seed),
            message_id=None,
            status=AttachmentStatus.READY,
            attached_at=None,
        )
    )
    cross_user = _envelope(
        harness,
        external_message_id="cross-user-message",
        blocks=(
            MultimodalContentBlock(
                kind=ContentBlockKind.IMAGE,
                attachment_id=cross_user_attachment.id,
                content_type="image/png",
                file_name="photo.png",
                size_bytes=128,
                sha256="a" * 64,
            ),
        ),
    )
    with pytest.raises(PermanentTaskError, match="附件"):
        await harness.handler.handle(_job(cross_user))
