"""多模态能力协商和渠道控制平面测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cnb_adapters import (
    ChannelAdapter,
    ChannelCapabilities,
    ChannelCapabilityError,
    ChannelDeliveryCommand,
    ChannelNotConfiguredError,
    ChannelRateLimitError,
    PlaceholderAdapter,
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
        ChannelPlatform.TELEGRAM,
    ),
)
async def test_placeholder_adapters_follow_contract_without_external_side_effects(
    platform: ChannelPlatform,
) -> None:
    """三个占位包均完整实现契约，并在所有外部边界明确停止。"""
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
    tenant_id, actor_id = uuid4(), uuid4()
    repository = MemoryChannelRepository()
    service = ChannelService(
        repository,
        build_default_channel_registry(),
        MemorySecretStore(),
    )
    instance = await service.create(
        tenant_id=tenant_id,
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
    tenant_id, actor_id = uuid4(), uuid4()
    repository = MemoryChannelRepository()
    secrets = MemorySecretStore()
    service = ChannelService(repository, build_default_channel_registry(), secrets)
    instance = await service.create(
        tenant_id=tenant_id,
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
        channel_id=instance.instance.id,
        actor_id=actor_id,
    )
    assert tested.credential_configured is True
    assert tested.instance.health_status.value == "not_configured"
    assert "测试凭证" not in (tested.instance.health_detail or "")
    with pytest.raises(ChannelNotConfiguredError, match="未发送任何外部消息"):
        await service.deliver(
            tenant_id=tenant_id,
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
    tenant_id, other_tenant_id, actor_id = uuid4(), uuid4(), uuid4()
    secrets = MetadataOnlySecretStore()
    service = ChannelService(
        MemoryChannelRepository(),
        build_default_channel_registry(),
        secrets,
    )

    created = await service.create(
        tenant_id=tenant_id,
        name="飞书元数据边界",
        platform=ChannelPlatform.FEISHU,
        status=ChannelInstanceStatus.DISABLED,
        rate_limit_per_minute=60,
        settings={},
        credential="只写不回显",
        actor_id=actor_id,
    )

    assert created.credential_configured is True
    assert (await service.list(tenant_id=tenant_id))[0].credential_configured is True
    assert await service.list(tenant_id=other_tenant_id) == ()
    with pytest.raises(ChannelNotFoundError, match="渠道实例不存在"):
        await service.get(
            tenant_id=other_tenant_id,
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
            name="不安全设置",
            platform=ChannelPlatform.FEISHU,
            status=ChannelInstanceStatus.DISABLED,
            rate_limit_per_minute=60,
            settings={"connection": {"bot_token": "不应保存在公开设置中"}},
            credential=None,
            actor_id=uuid4(),
        )
