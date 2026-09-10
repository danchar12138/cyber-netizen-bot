"""多模态能力协商和渠道控制平面测试。"""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from cnb_adapters import (
    ChannelAdapter,
    ChannelAdapterError,
    ChannelAdapterRegistry,
    ChannelCapabilities,
    ChannelCapabilityError,
    ChannelDeliveryCommand,
    ChannelNotConfiguredError,
    ChannelRateLimitError,
    PlaceholderAdapter,
    TelegramChannelAdapter,
    build_default_channel_registry,
    negotiate_capabilities,
    summarize_blocks,
)
from cnb_application import ChannelNotFoundError, ChannelService, ChannelValidationError
from cnb_domain import (
    ChannelInstanceStatus,
    ChannelPlatform,
    ContentBlockKind,
    MultimodalContentBlock,
)
from cnb_infrastructure import MemoryChannelRepository, MemorySecretStore

_TELEGRAM_TOKEN = "123456789:" + ("A" * 35)


class RecordingTelegramTransport:
    """记录出站形状但不访问公网的 Telegram Transport。"""

    def __init__(
        self,
        responses: list[httpx.Response | httpx.HTTPError],
    ) -> None:
        self.responses = responses
        self.requests: list[tuple[str, Mapping[str, object] | None]] = []
        self.closed = False

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        self.requests.append((url, json))
        result = self.responses.pop(0)
        if isinstance(result, httpx.HTTPError):
            raise result
        return result

    async def aclose(self) -> None:
        self.closed = True


class MetadataOnlySecretStore(MemorySecretStore):
    """管理视图测试桩：一旦为展示状态解密凭证就立即失败。"""

    async def resolve_secret(
        self,
        key: str,
        *,
        tenant_id: object,
        agent_id: object | None = None,
        channel_id: object | None = None,
        user_id: object | None = None,
    ) -> str | None:
        del key, tenant_id, agent_id, channel_id, user_id
        raise AssertionError("渠道管理视图不得解密凭证")


def test_placeholder_never_claims_configuration() -> None:
    adapter = PlaceholderAdapter(
        key="feishu",
        display_name="Feishu",
        capabilities=ChannelCapabilities(markdown=True, images=True, files=True),
    )

    assert adapter.status == "not_configured"
    assert adapter.capabilities.text is True
    assert adapter.capabilities.streaming is False


@pytest.mark.parametrize(
    "platform",
    (
        ChannelPlatform.FEISHU,
        ChannelPlatform.DISCORD,
    ),
)
async def test_placeholder_adapters_follow_contract_without_external_side_effects(
    platform: ChannelPlatform,
) -> None:
    """两个占位包均完整实现契约，并在所有外部边界明确停止。"""
    adapter: ChannelAdapter = build_default_channel_registry().get(platform)
    health = await adapter.test_connection(credential="只用于契约测试")
    command = ChannelDeliveryCommand(
        channel_id=uuid4(),
        recipient_id="external-user",
        blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text="不会发出"),),
        idempotency_key=f"contract:{platform.value}",
    )
    negotiation = negotiate_capabilities(command, adapter.capabilities)

    assert health.status.value == "not_configured"
    assert "未访问外部 API" in health.detail
    with pytest.raises(ChannelNotConfiguredError, match="未发送任何外部消息"):
        await adapter.deliver(
            command=command,
            negotiation=negotiation,
            credential="只用于契约测试",
        )
    with pytest.raises(ChannelNotConfiguredError, match="未消费任何外部事件"):
        await adapter.normalize_inbound({"token": "不会被处理"})


async def test_telegram_adapter_tests_connection_sends_threads_and_edits() -> None:
    transport = RecordingTelegramTransport(
        [
            httpx.Response(200, json={"ok": True, "result": {"id": 123456789}}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 41}}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 41}}),
        ]
    )
    adapter = TelegramChannelAdapter(transport=transport)
    health = await adapter.test_connection(credential=_TELEGRAM_TOKEN)
    command = ChannelDeliveryCommand(
        channel_id=uuid4(),
        recipient_id="-1001234567890",
        blocks=(MultimodalContentBlock(kind=ContentBlockKind.MARKDOWN, text="**你好**"),),
        idempotency_key="telegram:delivery:1",
        request_streaming=True,
        thread_id="17",
        proactive=True,
    )
    negotiation = negotiate_capabilities(command, adapter.capabilities)
    sent = await adapter.deliver(
        command=command,
        negotiation=negotiation,
        credential=_TELEGRAM_TOKEN,
    )
    edit_command = ChannelDeliveryCommand(
        channel_id=command.channel_id,
        recipient_id="@example_channel",
        blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text="修改后"),),
        idempotency_key="telegram:delivery:2",
        edit_message_id="41",
        proactive=True,
    )
    edited = await adapter.deliver(
        command=edit_command,
        negotiation=negotiate_capabilities(edit_command, adapter.capabilities),
        credential=_TELEGRAM_TOKEN,
    )

    assert health.status.value == "healthy"
    assert sent.status.value == "degraded"
    assert sent.external_message_id == edited.external_message_id == "41"
    assert set(sent.degradations) == {"markdown_to_text", "streaming_to_buffered"}
    assert transport.requests[0][0].endswith("/getMe")
    assert transport.requests[1][0].endswith("/sendMessage")
    assert transport.requests[1][1] == {
        "chat_id": -1001234567890,
        "text": "你好",
        "message_thread_id": 17,
    }
    assert transport.requests[2][0].endswith("/editMessageText")
    assert transport.requests[2][1] == {
        "chat_id": "@example_channel",
        "text": "修改后",
        "message_id": 41,
    }


@pytest.mark.parametrize(
    ("credential", "recipient_id", "thread_id", "message"),
    (
        ("invalid", "-1001234567890", None, "Token 格式无效"),
        (_TELEGRAM_TOKEN, "https://example.invalid", None, "chat ID"),
        (_TELEGRAM_TOKEN, "-1001234567890", "not-an-id", "话题 ID"),
    ),
)
async def test_telegram_adapter_rejects_invalid_identifiers_before_network(
    credential: str,
    recipient_id: str,
    thread_id: str | None,
    message: str,
) -> None:
    transport = RecordingTelegramTransport([])
    adapter = TelegramChannelAdapter(transport=transport)
    command = ChannelDeliveryCommand(
        channel_id=uuid4(),
        recipient_id=recipient_id,
        blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text="不会发出"),),
        idempotency_key="telegram:invalid",
        thread_id=thread_id,
    )

    with pytest.raises(ChannelAdapterError, match=message):
        await adapter.deliver(
            command=command,
            negotiation=negotiate_capabilities(command, adapter.capabilities),
            credential=credential,
        )
    assert transport.requests == []


@pytest.mark.parametrize(
    ("response", "code"),
    (
        (
            httpx.Response(429, json={"description": "secret remote detail"}),
            "telegram_rate_limited",
        ),
        (httpx.Response(503, text="secret remote detail"), "telegram_unavailable"),
        (httpx.Response(403, json={"description": "secret remote detail"}), "telegram_rejected"),
        (httpx.Response(200, text="secret remote detail"), "telegram_invalid_response"),
        (httpx.ReadTimeout("secret transport detail"), "telegram_unavailable"),
    ),
)
async def test_telegram_errors_are_stable_and_do_not_expose_sensitive_context(
    response: httpx.Response | httpx.HTTPError,
    code: str,
) -> None:
    transport = RecordingTelegramTransport([response])
    adapter = TelegramChannelAdapter(transport=transport)

    with pytest.raises(ChannelAdapterError) as captured:
        await adapter.test_connection(credential=_TELEGRAM_TOKEN)

    assert captured.value.code == code
    assert captured.value.__cause__ is None
    serialized = f"{captured.value!s} {captured.value!r}"
    assert _TELEGRAM_TOKEN not in serialized
    assert "secret" not in serialized
    assert "api.telegram.org" not in serialized


async def test_telegram_inbound_remains_explicitly_disabled() -> None:
    adapter = TelegramChannelAdapter(transport=RecordingTelegramTransport([]))

    with pytest.raises(ChannelNotConfiguredError, match="入站接收尚未启用"):
        await adapter.normalize_inbound({"update_id": 1})


async def test_telegram_service_reports_ready_and_replays_without_duplicate_send() -> None:
    transport = RecordingTelegramTransport(
        [
            httpx.Response(200, json={"ok": True, "result": {"id": 123456789}}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 88}}),
        ]
    )
    adapter = TelegramChannelAdapter(transport=transport)
    repository = MemoryChannelRepository()
    service = ChannelService(
        repository,
        ChannelAdapterRegistry((adapter,)),
        MemorySecretStore(),
    )
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    created = await service.create(
        tenant_id=tenant_id,
        agent_id=agent_id,
        name="Telegram 正式出站",
        platform=ChannelPlatform.TELEGRAM,
        status=ChannelInstanceStatus.ENABLED,
        rate_limit_per_minute=10,
        settings={},
        credential=_TELEGRAM_TOKEN,
        actor_id=actor_id,
    )
    tested = await service.test_connection(
        tenant_id=tenant_id,
        agent_id=agent_id,
        channel_id=created.instance.id,
        actor_id=actor_id,
    )
    first = await service.deliver(
        tenant_id=tenant_id,
        agent_id=agent_id,
        channel_id=created.instance.id,
        recipient_id="-1001234567890",
        blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text="安全发送"),),
        idempotency_key="telegram:service:1",
        request_streaming=False,
        thread_id=None,
        edit_message_id=None,
        proactive=True,
    )
    replay = await service.deliver(
        tenant_id=tenant_id,
        agent_id=agent_id,
        channel_id=created.instance.id,
        recipient_id="-1001234567890",
        blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text="安全发送"),),
        idempotency_key="telegram:service:1",
        request_streaming=False,
        thread_id=None,
        edit_message_id=None,
        proactive=True,
    )
    events = await service.events(
        tenant_id=tenant_id,
        agent_id=agent_id,
        channel_id=created.instance.id,
        limit=10,
    )

    assert service.catalog()[0].implementation_status == "ready"
    assert tested.implementation_status == "ready"
    assert tested.instance.health_status.value == "healthy"
    assert first.external_message_id == replay.external_message_id == "88"
    assert replay.idempotent_replay is True
    assert len(transport.requests) == 2
    assert all(_TELEGRAM_TOKEN not in str(event.payload_summary) for event in events)
    assert all("安全发送" not in str(event.payload_summary) for event in events)


async def test_invalid_telegram_credential_is_rejected_before_instance_creation() -> None:
    repository = MemoryChannelRepository()
    service = ChannelService(
        repository,
        ChannelAdapterRegistry((TelegramChannelAdapter(transport=RecordingTelegramTransport([])),)),
        MemorySecretStore(),
    )

    with pytest.raises(ChannelValidationError, match="Token 格式无效"):
        await service.create(
            tenant_id=uuid4(),
            agent_id=uuid4(),
            name="无效 Telegram",
            platform=ChannelPlatform.TELEGRAM,
            status=ChannelInstanceStatus.ENABLED,
            rate_limit_per_minute=10,
            settings={},
            credential="invalid",
            actor_id=uuid4(),
        )
    assert repository.instances == {}


@pytest.mark.parametrize(
    ("content_type", "size_bytes", "sha256", "message"),
    (
        ("application/x-msdownload", 1024, "a" * 64, "不支持的安全媒体类型"),
        ("image/png", 0, "a" * 64, "附件大小超过目标渠道上限"),
        ("image/png", 1024, "not-a-sha256", "有效 SHA-256"),
    ),
)
def test_attachment_validation_rejects_unsafe_metadata(
    content_type: str,
    size_bytes: int,
    sha256: str,
    message: str,
) -> None:
    command = ChannelDeliveryCommand(
        channel_id=uuid4(),
        recipient_id="browser-session",
        blocks=(
            MultimodalContentBlock(
                kind=ContentBlockKind.IMAGE,
                attachment_id=uuid4(),
                content_type=content_type,
                file_name="image.png",
                size_bytes=size_bytes,
                sha256=sha256,
            ),
        ),
        idempotency_key="unsafe-attachment",
    )

    with pytest.raises(ChannelCapabilityError, match=message):
        negotiate_capabilities(command, ChannelCapabilities(images=True))


def test_capability_negotiation_transparently_degrades_and_splits_content() -> None:
    image_id = uuid4()
    negotiation = negotiate_capabilities(
        ChannelDeliveryCommand(
            channel_id=uuid4(),
            recipient_id="user-1",
            blocks=(
                MultimodalContentBlock(
                    kind=ContentBlockKind.MARKDOWN,
                    text="**你好** [文档](https://example.invalid)",
                ),
                MultimodalContentBlock(
                    kind=ContentBlockKind.IMAGE,
                    attachment_id=image_id,
                    content_type="image/png",
                    file_name="photo.png",
                    size_bytes=1024,
                    sha256="a" * 64,
                    alt_text="山景",
                ),
            ),
            idempotency_key="delivery:1",
            request_streaming=True,
            thread_id="thread-1",
            edit_message_id="message-1",
        ),
        ChannelCapabilities(max_text_chars=4, max_blocks=20),
    )

    assert set(negotiation.degradations) == {
        "streaming_to_buffered",
        "thread_to_root_message",
        "edit_to_new_message",
        "markdown_to_text",
        "image_to_text_reference",
        "long_text_split",
    }
    assert negotiation.buffered is True
    assert negotiation.thread_id is negotiation.edit_message_id is None
    assert all(block.kind is ContentBlockKind.TEXT for block in negotiation.blocks)
    summary = summarize_blocks(negotiation.blocks)
    assert summary["block_count"] == len(negotiation.blocks)
    assert "你好" not in str(summary)
    assert "photo.png" not in str(summary)


async def test_web_adapter_delivery_is_idempotent_and_rate_limited() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    repository = MemoryChannelRepository()
    service = ChannelService(
        repository,
        build_default_channel_registry(),
        MemorySecretStore(),
    )
    instance = await service.create(
        tenant_id=tenant_id,
        agent_id=agent_id,
        name="内部 Web",
        platform=ChannelPlatform.WEB,
        status=ChannelInstanceStatus.ENABLED,
        rate_limit_per_minute=1,
        settings={"audience": "internal"},
        credential=None,
        actor_id=actor_id,
    )
    block = MultimodalContentBlock(kind=ContentBlockKind.MARKDOWN, text="你好，**世界**")
    first = await service.deliver(
        tenant_id=tenant_id,
        agent_id=agent_id,
        channel_id=instance.instance.id,
        recipient_id="browser-session",
        blocks=(block,),
        idempotency_key="web:delivery:1",
        request_streaming=True,
        thread_id=None,
        edit_message_id=None,
        proactive=False,
    )
    replay = await service.deliver(
        tenant_id=tenant_id,
        agent_id=agent_id,
        channel_id=instance.instance.id,
        recipient_id="browser-session",
        blocks=(block,),
        idempotency_key="web:delivery:1",
        request_streaming=True,
        thread_id=None,
        edit_message_id=None,
        proactive=False,
    )
    assert replay.external_message_id == first.external_message_id
    assert replay.idempotent_replay is True
    with pytest.raises(ChannelRateLimitError, match="每分钟发送上限"):
        await service.deliver(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=instance.instance.id,
            recipient_id="browser-session",
            blocks=(block,),
            idempotency_key="web:delivery:2",
            request_streaming=False,
            thread_id=None,
            edit_message_id=None,
            proactive=False,
        )


async def test_placeholder_credentials_never_turn_into_false_healthy_state() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    repository = MemoryChannelRepository()
    secrets = MemorySecretStore()
    service = ChannelService(repository, build_default_channel_registry(), secrets)
    instance = await service.create(
        tenant_id=tenant_id,
        agent_id=agent_id,
        name="飞书预留",
        platform=ChannelPlatform.FEISHU,
        status=ChannelInstanceStatus.ENABLED,
        rate_limit_per_minute=60,
        settings={"app_id_hint": "cli_test"},
        credential="只写不回显的测试凭证",
        actor_id=actor_id,
    )
    tested = await service.test_connection(
        tenant_id=tenant_id,
        agent_id=agent_id,
        channel_id=instance.instance.id,
        actor_id=actor_id,
    )
    assert tested.credential_configured is True
    assert tested.instance.health_status.value == "not_configured"
    assert "测试凭证" not in (tested.instance.health_detail or "")
    with pytest.raises(ChannelNotConfiguredError, match="未发送任何外部消息"):
        await service.deliver(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=instance.instance.id,
            recipient_id="open-id",
            blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text="不会发出"),),
            idempotency_key=f"placeholder:{datetime.now(UTC).isoformat()}",
            request_streaming=False,
            thread_id=None,
            edit_message_id=None,
            proactive=False,
        )


async def test_channel_views_use_secret_metadata_and_remain_tenant_isolated() -> None:
    tenant_id, other_tenant_id = uuid4(), uuid4()
    agent_id, other_agent_id, actor_id = uuid4(), uuid4(), uuid4()
    secrets = MetadataOnlySecretStore()
    service = ChannelService(
        MemoryChannelRepository(),
        build_default_channel_registry(),
        secrets,
    )

    created = await service.create(
        tenant_id=tenant_id,
        agent_id=agent_id,
        name="飞书元数据边界",
        platform=ChannelPlatform.FEISHU,
        status=ChannelInstanceStatus.DISABLED,
        rate_limit_per_minute=60,
        settings={},
        credential="只写不回显",
        actor_id=actor_id,
    )

    assert created.credential_configured is True
    await service.create(
        tenant_id=tenant_id,
        agent_id=other_agent_id,
        name="飞书元数据边界",
        platform=ChannelPlatform.FEISHU,
        status=ChannelInstanceStatus.DISABLED,
        rate_limit_per_minute=60,
        settings={},
        credential=None,
        actor_id=actor_id,
    )

    assert (await service.list(tenant_id=tenant_id, agent_id=agent_id))[
        0
    ].credential_configured is True
    assert len(await service.list(tenant_id=tenant_id, agent_id=other_agent_id)) == 1
    assert await service.list(tenant_id=other_tenant_id, agent_id=agent_id) == ()
    with pytest.raises(ChannelNotFoundError, match="渠道实例不存在"):
        await service.get(
            tenant_id=tenant_id,
            agent_id=other_agent_id,
            channel_id=created.instance.id,
        )


async def test_channel_public_settings_reject_nested_secret_like_fields() -> None:
    service = ChannelService(
        MemoryChannelRepository(),
        build_default_channel_registry(),
        MemorySecretStore(),
    )

    with pytest.raises(ChannelValidationError, match="禁止包含疑似密钥字段"):
        await service.create(
            tenant_id=uuid4(),
            agent_id=uuid4(),
            name="不安全设置",
            platform=ChannelPlatform.FEISHU,
            status=ChannelInstanceStatus.DISABLED,
            rate_limit_per_minute=60,
            settings={"connection": {"bot_token": "不应保存在公开设置中"}},
            credential=None,
            actor_id=uuid4(),
        )
