"""Telegram Bot API 的正式、安全出站 Adapter。"""

import json as json_module
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, Literal, Protocol, cast

import httpx

from cnb_adapters.channel import (
    AdapterDeliveryResult,
    AdapterHealth,
    CapabilityNegotiation,
    ChannelAdapterError,
    ChannelCapabilityError,
    ChannelDeliveryCommand,
    ChannelInboundEvent,
    ChannelNotConfiguredError,
)
from cnb_domain import (
    ChannelCapabilities,
    ChannelEventStatus,
    ChannelHealthStatus,
    ChannelPlatform,
    ContentBlockKind,
    JsonValue,
)

_TELEGRAM_API_ORIGIN: Final = "https://api.telegram.org"
_TOKEN_PATTERN: Final = re.compile(r"^[1-9][0-9]{5,15}:[A-Za-z0-9_-]{20,128}$")
_INTEGER_ID_PATTERN: Final = re.compile(r"^-?[1-9][0-9]{0,19}$")
_USERNAME_PATTERN: Final = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")


class TelegramTransport(Protocol):
    """Adapter 使用的最小 HTTP 端口，便于无公网契约测试。"""

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, object] | None = None,
    ) -> httpx.Response: ...

    async def aclose(self) -> None: ...


class _TelegramHttpTransport:
    """绕过会记录完整 URL 的高级客户端，防止路径中的 Bot Token 进入日志。"""

    def __init__(self, timeout_seconds: float) -> None:
        self._transport = httpx.AsyncHTTPTransport(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            retries=0,
            trust_env=False,
        )
        self._timeout = {
            "connect": timeout_seconds,
            "read": timeout_seconds,
            "write": timeout_seconds,
            "pool": timeout_seconds,
        }

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        content = b"" if json is None else json_module.dumps(json).encode("utf-8")
        request = httpx.Request(
            "POST",
            url,
            headers={"content-type": "application/json"},
            content=content,
            extensions={"timeout": self._timeout},
        )
        response = await self._transport.handle_async_request(request)
        response.request = request
        await response.aread()
        return response

    async def aclose(self) -> None:
        await self._transport.aclose()


class TelegramChannelAdapter:
    """使用固定官方端点发送纯文本，不暴露 Token、正文或远端响应。"""

    platform = ChannelPlatform.TELEGRAM
    display_name = "Telegram"
    implementation_status: Literal["ready"] = "ready"
    capabilities = ChannelCapabilities(
        markdown=False,
        images=False,
        files=False,
        streaming=False,
        reactions=False,
        threads=True,
        message_edit=True,
        proactive_messages=True,
        max_text_chars=4_096,
        max_blocks=1,
        max_attachment_bytes=50 * 1024 * 1024,
        accepted_content_types=(),
    )

    def __init__(
        self,
        *,
        transport: TelegramTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("Telegram 请求超时必须位于 0 到 60 秒之间")
        self._owns_transport = transport is None
        self._transport = transport or _TelegramHttpTransport(timeout_seconds)

    async def test_connection(self, *, credential: str | None) -> AdapterHealth:
        token = self._token(credential)
        payload = await self._call(token=token, method="getMe")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ChannelAdapterError("telegram_invalid_response", "Telegram 返回了无效连接结果")
        bot_id = cast(dict[str, object], result).get("id")
        if type(bot_id) is not int or bot_id <= 0:
            raise ChannelAdapterError("telegram_invalid_response", "Telegram 返回了无效连接结果")
        return AdapterHealth(
            status=ChannelHealthStatus.HEALTHY,
            detail="Telegram Bot API 连接正常，凭证已验证。",
            checked_at=datetime.now(UTC),
        )

    def validate_credential(self, credential: str) -> None:
        """在持久化和发起网络请求前验证 Bot Token 的公开格式。"""
        self._token(credential)

    async def deliver(
        self,
        *,
        command: ChannelDeliveryCommand,
        negotiation: CapabilityNegotiation,
        credential: str | None,
    ) -> AdapterDeliveryResult:
        token = self._token(credential)
        if len(negotiation.blocks) != 1:
            raise ChannelCapabilityError("Telegram 单次发送只接受一个文本内容块")
        block = negotiation.blocks[0]
        if block.kind is not ContentBlockKind.TEXT or block.text is None:
            raise ChannelCapabilityError("Telegram 出站当前只支持纯文本")

        request: dict[str, object] = {
            "chat_id": self._chat_id(command.recipient_id),
            "text": block.text,
        }
        method = "sendMessage"
        if negotiation.thread_id is not None:
            request["message_thread_id"] = self._positive_id(
                negotiation.thread_id, label="Telegram 话题 ID"
            )
        if negotiation.edit_message_id is not None:
            method = "editMessageText"
            request["message_id"] = self._positive_id(
                negotiation.edit_message_id, label="Telegram 消息 ID"
            )
            request.pop("message_thread_id", None)

        payload = await self._call(token=token, method=method, request=request)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ChannelAdapterError("telegram_invalid_response", "Telegram 返回了无效发送结果")
        message_id = cast(dict[str, object], result).get("message_id")
        if type(message_id) is not int or message_id <= 0:
            raise ChannelAdapterError("telegram_invalid_response", "Telegram 返回了无效发送结果")
        return AdapterDeliveryResult(
            status=(
                ChannelEventStatus.DEGRADED
                if negotiation.degradations
                else ChannelEventStatus.DELIVERED
            ),
            external_message_id=str(message_id),
            degradations=negotiation.degradations,
            delivered_at=datetime.now(UTC),
        )

    async def normalize_inbound(
        self,
        payload: Mapping[str, JsonValue],
    ) -> ChannelInboundEvent:
        del payload
        raise ChannelNotConfiguredError("Telegram 入站接收尚未启用，未消费任何外部事件")

    async def aclose(self) -> None:
        """释放默认连接池；测试注入的共享 Transport 由调用方管理。"""
        if self._owns_transport:
            await self._transport.aclose()

    async def _call(
        self,
        *,
        token: str,
        method: str,
        request: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            response = await self._transport.post(
                f"{_TELEGRAM_API_ORIGIN}/bot{token}/{method}",
                json=request,
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise ChannelAdapterError(
                "telegram_unavailable", "Telegram 暂时无法连接", retryable=True
            ) from None
        except httpx.HTTPError:
            raise ChannelAdapterError(
                "telegram_transport_error", "Telegram 请求未能安全完成", retryable=True
            ) from None

        if response.status_code == 429:
            raise ChannelAdapterError(
                "telegram_rate_limited",
                "Telegram 已限制发送频率",
                retryable=True,
                retry_after_seconds=self._retry_after(response),
            )
        if response.status_code >= 500:
            raise ChannelAdapterError(
                "telegram_unavailable", "Telegram 服务暂时不可用", retryable=True
            )
        if response.status_code >= 400:
            raise ChannelAdapterError("telegram_rejected", "Telegram 拒绝了请求")
        try:
            payload = cast(object, response.json())
        except ValueError:
            raise ChannelAdapterError(
                "telegram_invalid_response", "Telegram 返回了无效响应"
            ) from None
        if not isinstance(payload, dict):
            raise ChannelAdapterError("telegram_invalid_response", "Telegram 返回了无效响应")
        typed_payload = cast(dict[str, object], payload)
        if typed_payload.get("ok") is not True:
            raise ChannelAdapterError("telegram_rejected", "Telegram 拒绝了请求")
        return typed_payload

    @staticmethod
    def _retry_after(response: httpx.Response) -> int:
        """只提取 Telegram 的有界重试秒数，不保留其他远端响应字段。"""
        try:
            payload = cast(object, response.json())
        except ValueError:
            return 1
        if not isinstance(payload, dict):
            return 1
        parameters = cast(dict[str, object], payload).get("parameters")
        if not isinstance(parameters, dict):
            return 1
        retry_after = cast(dict[str, object], parameters).get("retry_after")
        if type(retry_after) is int and 1 <= retry_after <= 86_400:
            return retry_after
        return 1

    @staticmethod
    def _token(credential: str | None) -> str:
        if credential is None:
            raise ChannelNotConfiguredError("Telegram Bot Token 尚未配置")
        token = credential.strip()
        if not _TOKEN_PATTERN.fullmatch(token):
            raise ChannelNotConfiguredError("Telegram Bot Token 格式无效")
        return token

    @staticmethod
    def _chat_id(value: str) -> int | str:
        normalized = value.strip()
        if _INTEGER_ID_PATTERN.fullmatch(normalized):
            return int(normalized)
        if _USERNAME_PATTERN.fullmatch(normalized):
            return normalized
        raise ChannelCapabilityError("Telegram chat ID 必须是整数或合法的 @channel_username")

    @staticmethod
    def _positive_id(value: str, *, label: str) -> int:
        normalized = value.strip()
        if not normalized.isascii() or not normalized.isdecimal():
            raise ChannelCapabilityError(f"{label}必须是正整数")
        identifier = int(normalized)
        if not 1 <= identifier <= 9_223_372_036_854_775_807:
            raise ChannelCapabilityError(f"{label}超出有效范围")
        return identifier


__all__ = ["TelegramChannelAdapter", "TelegramTransport"]
