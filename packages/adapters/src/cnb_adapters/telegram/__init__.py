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
    ExternalConversationKind,
    JsonValue,
    MultimodalContentBlock,
)

_TELEGRAM_API_ORIGIN: Final = "https://api.telegram.org"
_TOKEN_PATTERN: Final = re.compile(r"^[1-9][0-9]{5,15}:[A-Za-z0-9_-]{20,128}$")
_INTEGER_ID_PATTERN: Final = re.compile(r"^-?[1-9][0-9]{0,19}$")
_USERNAME_PATTERN: Final = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")
_MAX_TELEGRAM_INTEGER: Final = 9_223_372_036_854_775_807
_MAX_MESSAGE_TEXT_CHARS: Final = 4_096
_MIN_MESSAGE_TIMESTAMP: Final = 946_684_800  # 2000-01-01T00:00:00Z
_MAX_MESSAGE_TIMESTAMP: Final = 4_102_444_800  # 2100-01-01T00:00:00Z
_SUPPORTED_UPDATE_KEYS: Final = frozenset({"update_id", "message"})


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
        """将受支持的 Telegram 文本 ``message`` Update 净化为通用入站事件。

        Webhook 的签名、请求体大小和事件时效由 API 边界及 Inbox 网关负责；此处只
        接受身份和会话语义完整的普通文本消息，避免原始 Telegram 载荷进入应用层。
        """
        if frozenset(payload) != _SUPPORTED_UPDATE_KEYS:
            raise ChannelCapabilityError("Telegram 入站事件类型不受支持")

        update_id = self._required_positive_integer(payload, "update_id", "Update ID")
        message = self._required_mapping(payload, "message", "消息")
        message_id = self._required_positive_integer(message, "message_id", "消息 ID")
        occurred_at = self._message_time(message)
        sender = self._required_mapping(message, "from", "发送者")
        if sender.get("is_bot") is not False:
            raise ChannelCapabilityError("Telegram 入站消息发送者必须是普通用户")
        sender_id = self._required_positive_integer(sender, "id", "发送者 ID")
        chat = self._required_mapping(message, "chat", "会话")
        chat_id = self._required_nonzero_integer(chat, "id", "会话 ID")
        chat_type = chat.get("type")
        if chat_type not in {"private", "group", "supergroup"}:
            raise ChannelCapabilityError("Telegram 入站消息会话类型不受支持")

        text = self._message_text(message)
        thread_id = self._thread_id(message, chat_type=chat_type)
        return ChannelInboundEvent(
            external_event_id=f"telegram:update:{update_id}",
            event_type="message.created",
            sender_external_id=str(sender_id),
            conversation_external_id=str(chat_id),
            blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text=text),),
            occurred_at=occurred_at,
            thread_external_id=str(thread_id) if thread_id is not None else None,
            message_external_id=f"telegram:message:{chat_id}:{message_id}",
            conversation_kind=(
                ExternalConversationKind.DIRECT
                if chat_type == "private"
                else ExternalConversationKind.GROUP
            ),
        )

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

    @staticmethod
    def _required_mapping(
        values: Mapping[str, JsonValue],
        key: str,
        label: str,
    ) -> Mapping[str, JsonValue]:
        value = values.get(key)
        if not isinstance(value, Mapping):
            raise ChannelCapabilityError(f"Telegram 入站消息缺少有效{label}")
        return value

    @staticmethod
    def _required_positive_integer(
        values: Mapping[str, JsonValue],
        key: str,
        label: str,
    ) -> int:
        value = values.get(key)
        if type(value) is not int or not 1 <= value <= _MAX_TELEGRAM_INTEGER:
            raise ChannelCapabilityError(f"Telegram {label}无效")
        return value

    @staticmethod
    def _required_nonzero_integer(
        values: Mapping[str, JsonValue],
        key: str,
        label: str,
    ) -> int:
        value = values.get(key)
        if (
            type(value) is not int
            or value == 0
            or not -_MAX_TELEGRAM_INTEGER <= value <= _MAX_TELEGRAM_INTEGER
        ):
            raise ChannelCapabilityError(f"Telegram {label}无效")
        return value

    @classmethod
    def _message_time(cls, message: Mapping[str, JsonValue]) -> datetime:
        timestamp = cls._required_positive_integer(message, "date", "消息时间")
        if not _MIN_MESSAGE_TIMESTAMP <= timestamp <= _MAX_MESSAGE_TIMESTAMP:
            raise ChannelCapabilityError("Telegram 消息时间超出允许范围")
        try:
            return datetime.fromtimestamp(timestamp, UTC)
        except (OverflowError, OSError, ValueError):
            raise ChannelCapabilityError("Telegram 消息时间无效") from None

    @staticmethod
    def _message_text(message: Mapping[str, JsonValue]) -> str:
        value = message.get("text")
        if not isinstance(value, str):
            raise ChannelCapabilityError("Telegram 入站当前只支持文本消息")
        text = value.strip()
        if not text:
            raise ChannelCapabilityError("Telegram 入站消息文本不能为空")
        if len(text) > _MAX_MESSAGE_TEXT_CHARS:
            raise ChannelCapabilityError("Telegram 入站消息文本超过长度上限")
        if "\x00" in text or any(0xD800 <= ord(character) <= 0xDFFF for character in text):
            raise ChannelCapabilityError("Telegram 入站消息文本包含无效字符")
        return text

    @classmethod
    def _thread_id(
        cls,
        message: Mapping[str, JsonValue],
        *,
        chat_type: str,
    ) -> int | None:
        value = message.get("message_thread_id")
        if value is None:
            if message.get("is_topic_message") is True:
                raise ChannelCapabilityError("Telegram Forum 消息缺少线程 ID")
            return None
        if chat_type != "supergroup":
            raise ChannelCapabilityError("Telegram Forum 线程必须属于超级群组")
        if message.get("is_topic_message") not in {None, True}:
            raise ChannelCapabilityError("Telegram Forum 消息标识无效")
        return cls._required_positive_integer(message, "message_thread_id", "Forum 线程 ID")


__all__ = ["TelegramChannelAdapter", "TelegramTransport"]
