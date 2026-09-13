"""不会访问外部网络的 IM Adapter 占位基类。"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from cnb_adapters.channel import (
    AdapterDeliveryResult,
    AdapterHealth,
    CapabilityNegotiation,
    ChannelDeliveryCommand,
    ChannelInboundEvent,
    ChannelNotConfiguredError,
    WebhookInfo,
)
from cnb_domain import ChannelCapabilities, ChannelHealthStatus, ChannelPlatform, JsonValue


@dataclass(frozen=True, slots=True)
class PlaceholderAdapter:
    """明确报告未配置，任何方法都不会向第三方平台发起请求。"""

    key: str
    display_name: str
    capabilities: ChannelCapabilities

    @property
    def platform(self) -> ChannelPlatform:
        return ChannelPlatform(self.key)

    @property
    def status(self) -> str:
        return ChannelHealthStatus.NOT_CONFIGURED.value

    @property
    def implementation_status(self) -> Literal["placeholder"]:
        return "placeholder"

    def validate_credential(self, credential: str) -> None:
        """占位平台尚无可校验的厂商凭证格式。"""
        del credential

    async def test_connection(self, *, credential: str | None) -> AdapterHealth:
        del credential
        return AdapterHealth(
            status=ChannelHealthStatus.NOT_CONFIGURED,
            detail=f"{self.display_name} 适配器当前为占位实现，未访问外部 API。",
            checked_at=datetime.now(UTC),
        )

    async def get_webhook_info(self, *, credential: str | None) -> WebhookInfo:
        del credential
        raise ChannelNotConfiguredError(f"{self.display_name} 适配器当前为占位实现，未管理 Webhook")

    async def set_webhook(
        self,
        *,
        credential: str | None,
        webhook_url: str,
        secret_token: str,
        drop_pending_updates: bool,
    ) -> WebhookInfo:
        del credential, webhook_url, secret_token, drop_pending_updates
        raise ChannelNotConfiguredError(f"{self.display_name} 适配器当前为占位实现，未管理 Webhook")

    async def delete_webhook(
        self,
        *,
        credential: str | None,
        drop_pending_updates: bool,
    ) -> WebhookInfo:
        del credential, drop_pending_updates
        raise ChannelNotConfiguredError(f"{self.display_name} 适配器当前为占位实现，未管理 Webhook")

    async def deliver(
        self,
        *,
        command: ChannelDeliveryCommand,
        negotiation: CapabilityNegotiation,
        credential: str | None,
    ) -> AdapterDeliveryResult:
        del command, negotiation, credential
        raise ChannelNotConfiguredError(
            f"{self.display_name} 适配器当前为占位实现，未发送任何外部消息"
        )

    async def normalize_inbound(
        self,
        payload: Mapping[str, JsonValue],
    ) -> ChannelInboundEvent:
        del payload
        raise ChannelNotConfiguredError(
            f"{self.display_name} 适配器当前为占位实现，未消费任何外部事件"
        )

    async def aclose(self) -> None:
        """占位实现没有需要释放的外部资源。"""
