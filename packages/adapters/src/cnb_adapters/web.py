"""内部 Web 渠道的正式、无外部网络 Adapter。"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from cnb_adapters.channel import (
    AdapterDeliveryResult,
    AdapterHealth,
    CapabilityNegotiation,
    ChannelCapabilityError,
    ChannelDeliveryCommand,
    ChannelInboundEvent,
)
from cnb_domain import (
    ChannelCapabilities,
    ChannelEventStatus,
    ChannelHealthStatus,
    ChannelPlatform,
    ContentBlockKind,
    ExternalConversationKind,
    JsonValue,
    MultimodalContentBlock,
)

_WEB_CONTENT_TYPES = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)


class WebChannelAdapter:
    """对内部 Web 事件执行完整能力承诺，发送结果可直接交给 WebSocket 层。"""

    platform = ChannelPlatform.WEB
    display_name = "内部 Web"
    implementation_status: Literal["ready"] = "ready"
    capabilities = ChannelCapabilities(
        markdown=True,
        images=True,
        files=True,
        streaming=True,
        reactions=True,
        threads=True,
        message_edit=True,
        proactive_messages=True,
        max_text_chars=20_000,
        max_blocks=20,
        max_attachment_bytes=250 * 1024 * 1024,
        accepted_content_types=_WEB_CONTENT_TYPES,
    )

    def validate_credential(self, credential: str) -> None:
        """内部 Web 不使用凭证，由应用服务拒绝写入操作。"""
        del credential

    async def test_connection(self, *, credential: str | None) -> AdapterHealth:
        del credential
        return AdapterHealth(
            status=ChannelHealthStatus.HEALTHY,
            detail="内部 Web Adapter 已就绪，无需外部凭证。",
            checked_at=datetime.now(UTC),
        )

    async def deliver(
        self,
        *,
        command: ChannelDeliveryCommand,
        negotiation: CapabilityNegotiation,
        credential: str | None,
    ) -> AdapterDeliveryResult:
        del credential
        delivery_identity = f"{command.channel_id}:{command.idempotency_key}"
        return AdapterDeliveryResult(
            status=(
                ChannelEventStatus.DEGRADED
                if negotiation.degradations
                else ChannelEventStatus.DELIVERED
            ),
            external_message_id=f"web_{uuid5(NAMESPACE_URL, delivery_identity).hex}",
            degradations=negotiation.degradations,
            delivered_at=datetime.now(UTC),
        )

    async def normalize_inbound(
        self,
        payload: Mapping[str, JsonValue],
    ) -> ChannelInboundEvent:
        required = (
            "external_event_id",
            "sender_external_id",
            "conversation_external_id",
            "text",
            "occurred_at",
        )
        if any(not isinstance(payload.get(key), str) for key in required):
            raise ChannelCapabilityError("Web 入站事件缺少有效标准字段")
        try:
            occurred_at = datetime.fromisoformat(str(payload["occurred_at"]))
        except ValueError as error:
            raise ChannelCapabilityError("Web 入站事件时间格式无效") from error
        if occurred_at.tzinfo is None:
            raise ChannelCapabilityError("Web 入站事件时间必须包含时区")
        text = str(payload["text"]).strip()
        if not text:
            raise ChannelCapabilityError("Web 入站消息文本不能为空")
        return ChannelInboundEvent(
            external_event_id=str(payload["external_event_id"]),
            event_type=str(payload.get("event_type", "message.created")),
            sender_external_id=str(payload["sender_external_id"]),
            conversation_external_id=str(payload["conversation_external_id"]),
            blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text=text),),
            occurred_at=occurred_at,
            thread_external_id=(
                str(payload["thread_external_id"])
                if isinstance(payload.get("thread_external_id"), str)
                else None
            ),
            message_external_id=(
                str(payload["message_external_id"])
                if isinstance(payload.get("message_external_id"), str)
                else str(payload["external_event_id"])
            ),
            conversation_kind=(
                ExternalConversationKind.GROUP
                if payload.get("conversation_kind") == ExternalConversationKind.GROUP.value
                else ExternalConversationKind.DIRECT
            ),
        )

    async def aclose(self) -> None:
        """内部 Web 实现没有需要释放的外部资源。"""
