"""外部映射、版本化 Envelope、幂等 Inbox 与安全路由测试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_adapters import ChannelInboundEvent
from cnb_application import (
    BackgroundTaskService,
    ConfigurationService,
    InboundConflictError,
    InboundGatewayService,
    InboundMessageTaskHandler,
    InboundNotFoundError,
    InboundValidationError,
    build_default_registry,
)
from cnb_domain import (
    BackgroundJobKind,
    ChannelHealthStatus,
    ChannelInstance,
    ChannelInstanceStatus,
    ChannelPlatform,
    ContentBlockKind,
    DevelopmentIdentity,
    ExternalConversationKind,
    ExternalMappingStatus,
    InboundVerification,
    MultimodalContentBlock,
)
from cnb_infrastructure import (
    InMemoryTaskRepository,
    MemoryChannelRepository,
    MemoryConfigurationRepository,
    MemoryConversationRepository,
    MemoryInboundGatewayRepository,
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
AGENT_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTOR_ID = USER_ID


async def _service() -> tuple[
    InboundGatewayService,
    MemoryInboundGatewayRepository,
    InMemoryTaskRepository,
    UUID,
    UUID,
]:
    identity = DevelopmentIdentity(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        agent_id=AGENT_ID,
        user_name="测试用户",
        agent_name="测试 Agent",
    )
    now = datetime.now(UTC)
    channel_id = uuid4()
    channel_repository = MemoryChannelRepository()
    await channel_repository.create_instance(
        ChannelInstance(
            id=channel_id,
            tenant_id=TENANT_ID,
            agent_id=AGENT_ID,
            name="内部 Web",
            platform=ChannelPlatform.WEB,
            status=ChannelInstanceStatus.ENABLED,
            rate_limit_per_minute=60,
            settings={},
            health_status=ChannelHealthStatus.HEALTHY,
            health_detail=None,
            last_checked_at=now,
            created_by=ACTOR_ID,
            created_at=now,
            updated_at=now,
        )
    )
    conversation_repository = MemoryConversationRepository()
    await conversation_repository.ensure_development_identity(identity)
    conversation = await conversation_repository.create_conversation(
        identity=identity,
        title="外部会话",
    )
    inbound_repository = MemoryInboundGatewayRepository(identity)
    task_repository = InMemoryTaskRepository()
    service = InboundGatewayService(
        repository=inbound_repository,
        channel_repository=channel_repository,
        conversation_repository=conversation_repository,
        task_service=BackgroundTaskService(task_repository),
        configuration_service=ConfigurationService(
            build_default_registry(),
            MemoryConfigurationRepository(),
        ),
    )
    await service.bind_identity(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        channel_id=channel_id,
        external_subject_id="subject-1",
        user_id=USER_ID,
        actor_id=ACTOR_ID,
    )
    await service.bind_conversation(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        channel_id=channel_id,
        user_id=USER_ID,
        kind=ExternalConversationKind.DIRECT,
        external_conversation_id="conversation-1",
        external_thread_id=None,
        conversation_id=conversation.id,
        actor_id=ACTOR_ID,
    )
    return service, inbound_repository, task_repository, channel_id, conversation.id


def _event(now: datetime, *, external_event_id: str = "event-1") -> ChannelInboundEvent:
    return ChannelInboundEvent(
        external_event_id=external_event_id,
        event_type="message.created",
        sender_external_id="subject-1",
        conversation_external_id="conversation-1",
        blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text="  你好  "),),
        occurred_at=now,
        message_external_id="message-1",
        conversation_kind=ExternalConversationKind.DIRECT,
    )


@pytest.mark.asyncio
async def test_mapping_conflicts_and_agent_scope_are_explicit() -> None:
    service, _, _, channel_id, _ = await _service()
    with pytest.raises(InboundConflictError, match="相同外部主体"):
        await service.bind_identity(
            tenant_id=TENANT_ID,
            agent_id=AGENT_ID,
            channel_id=channel_id,
            external_subject_id="subject-1",
            user_id=USER_ID,
            actor_id=ACTOR_ID,
        )
    assert (
        await service.list_identities(
            tenant_id=TENANT_ID,
            agent_id=uuid4(),
            channel_id=None,
            status=None,
            limit=100,
        )
        == ()
    )


@pytest.mark.asyncio
async def test_disabled_mapping_cannot_route_new_events() -> None:
    service, _, _, _, _ = await _service()
    mapping = (
        await service.list_identities(
            tenant_id=TENANT_ID,
            agent_id=AGENT_ID,
            channel_id=None,
            status=None,
            limit=100,
        )
    )[0]
    await service.set_identity_status(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        mapping_id=mapping.id,
        status=ExternalMappingStatus.DISABLED,
        actor_id=ACTOR_ID,
        confirmed=True,
    )
    now = datetime.now(UTC)
    with pytest.raises(InboundNotFoundError, match="身份映射"):
        await service.accept_normalized(
            tenant_id=TENANT_ID,
            agent_id=AGENT_ID,
            channel_id=mapping.channel_id,
            event=_event(now),
            verification=InboundVerification(True, 100, now),
            created_by=ACTOR_ID,
        )


@pytest.mark.asyncio
async def test_normalized_envelope_is_idempotent_and_diagnostics_only_expose_digests() -> None:
    service, _, task_repository, channel_id, conversation_id = await _service()
    now = datetime.now(UTC)
    first = await service.accept_normalized(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        channel_id=channel_id,
        event=_event(now),
        verification=InboundVerification(True, 100, now),
        created_by=ACTOR_ID,
    )
    second = await service.accept_normalized(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        channel_id=channel_id,
        event=_event(now, external_event_id="event-retry"),
        verification=InboundVerification(True, 100, now),
        created_by=ACTOR_ID,
    )
    assert first.created is True
    assert second.created is False
    assert second.job.id == first.job.id
    assert len(task_repository.inbox_events) == 1
    items = await service.list_inbox(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        status=None,
        channel_id=channel_id,
        limit=100,
    )
    assert len(items) == 1
    assert items[0].conversation_id == conversation_id
    assert items[0].external_subject_digest != "subject-1"
    assert items[0].external_message_digest != "message-1"
    assert items[0].content_kinds == ("text",)
    assert items[0].payload["blocks"] == [
        {
            "kind": "text",
            "text": "你好",
            "attachment_id": None,
            "content_type": None,
            "file_name": None,
            "size_bytes": None,
            "sha256": None,
            "alt_text": None,
        }
    ]


@pytest.mark.asyncio
async def test_signature_age_and_payload_size_are_rejected_before_enqueue() -> None:
    service, _, task_repository, channel_id, _ = await _service()
    now = datetime.now(UTC)
    invalid = (
        InboundVerification(False, 100, now),
        InboundVerification(True, 2 * 1024 * 1024, now),
        InboundVerification(True, 100, now + timedelta(hours=1)),
    )
    for verification in invalid:
        with pytest.raises(InboundValidationError):
            await service.accept_normalized(
                tenant_id=TENANT_ID,
                agent_id=AGENT_ID,
                channel_id=channel_id,
                event=_event(now),
                verification=verification,
                created_by=ACTOR_ID,
            )
    assert task_repository.inbox_events == {}


@pytest.mark.asyncio
async def test_inbound_worker_handler_validates_schema_and_returns_safe_route() -> None:
    service, _, _, channel_id, conversation_id = await _service()
    now = datetime.now(UTC)
    accepted = await service.accept_normalized(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        channel_id=channel_id,
        event=_event(now),
        verification=InboundVerification(True, 100, now),
        created_by=ACTOR_ID,
    )
    assert accepted.job.kind is BackgroundJobKind.INBOUND_MESSAGE
    result = await InboundMessageTaskHandler().handle(accepted.job)
    assert result == {
        "schema_version": "1",
        "routing_status": "ready_for_agent",
        "agent_id": str(AGENT_ID),
        "channel_id": str(channel_id),
        "user_id": str(USER_ID),
        "conversation_id": str(conversation_id),
        "content_block_count": 1,
    }
