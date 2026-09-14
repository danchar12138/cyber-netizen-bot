"""厂商无关的通知 Adapter 契约与安全实现。

通知载荷由应用层生成安全摘要；本模块只负责协议投递，不记录载荷、凭证或
远端响应正文。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import smtplib
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from cnb_adapters.alert_webhook import AlertWebhookNotifier


class NotificationAdapterError(RuntimeError):
    """可安全向任务系统报告的通知失败。"""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class NotificationAdapterValidationError(ValueError):
    """通知 Adapter 配置或目标不满足安全约束。"""


@dataclass(frozen=True, slots=True)
class NotificationDeliveryCommand:
    """通知 Adapter 的标准化发送命令，不携带 Secret。"""

    target: str
    payload: Mapping[str, object]
    idempotency_key: str
    timeout_seconds: float = 5.0
    max_retries: int = 2
    settings: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class NotificationDeliveryResult:
    """通知投递完成后的安全摘要。"""

    delivered: bool
    attempts: int
    status_code: int | None
    idempotency_key: str
    elapsed_ms: int


class NotificationAdapter(Protocol):
    """所有告警通知渠道必须满足的最小端口。"""

    @property
    def key(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    async def deliver(
        self,
        *,
        command: NotificationDeliveryCommand,
        secret: str | None,
    ) -> NotificationDeliveryResult: ...

    async def aclose(self) -> None: ...


class NotificationAdapterRegistry:
    """按稳定键选择通知 Adapter，并拒绝重复注册。"""

    def __init__(self, adapters: Iterable[NotificationAdapter]) -> None:
        self._adapters: dict[str, NotificationAdapter] = {}
        for adapter in adapters:
            key = adapter.key.strip().casefold()
            if not key or key in self._adapters:
                raise ValueError(f"重复或无效通知适配器：{adapter.key}")
            self._adapters[key] = adapter

    def get(self, key: str) -> NotificationAdapter:
        try:
            return self._adapters[key.strip().casefold()]
        except KeyError as error:
            raise NotificationAdapterValidationError(f"未知通知适配器：{key}") from error

    def all(self) -> tuple[NotificationAdapter, ...]:
        return tuple(self._adapters.values())

    async def aclose(self) -> None:
        for adapter in self._adapters.values():
            await adapter.aclose()


class HttpsWebhookNotificationAdapter:
    """通用 HTTPS Webhook 通知 Adapter。"""

    key = "webhook"
    display_name = "HTTPS Webhook"

    def __init__(self, notifier: AlertWebhookNotifier | None = None) -> None:
        self._notifier = notifier or AlertWebhookNotifier()

    async def deliver(
        self,
        *,
        command: NotificationDeliveryCommand,
        secret: str | None,
    ) -> NotificationDeliveryResult:
        if not secret:
            raise NotificationAdapterError("secret_missing", "通知签名密钥尚未配置")
        try:
            result = await self._notifier.notify(
                url=command.target,
                signing_secret=secret,
                payload=command.payload,
                idempotency_key=command.idempotency_key,
                timeout_seconds=command.timeout_seconds,
                max_retries=command.max_retries,
            )
        except ValueError as error:
            raise NotificationAdapterValidationError(str(error)) from error
        except Exception as error:
            from cnb_adapters.alert_webhook import AlertWebhookError

            if isinstance(error, AlertWebhookError):
                raise NotificationAdapterError(
                    error.code,
                    "Webhook 未接受通知",
                    retryable=error.retryable,
                ) from error
            raise NotificationAdapterError("webhook_transport_error", "Webhook 请求失败") from error
        return NotificationDeliveryResult(
            delivered=result.delivered,
            attempts=result.attempts,
            status_code=result.status_code,
            idempotency_key=result.idempotency_key,
            elapsed_ms=result.elapsed_ms,
        )

    async def aclose(self) -> None:
        return None


class FeishuWebhookNotificationAdapter(HttpsWebhookNotificationAdapter):
    """飞书机器人 Webhook Adapter，复用 HTTPS/SSRF/HMAC 投递边界。"""

    key = "feishu_webhook"
    display_name = "飞书 Webhook"

    async def deliver(
        self,
        *,
        command: NotificationDeliveryCommand,
        secret: str | None,
    ) -> NotificationDeliveryResult:
        if not secret:
            raise NotificationAdapterError("secret_missing", "飞书签名密钥尚未配置")
        timestamp = str(int(time.time()))
        signature_material = f"{timestamp}\n{secret}".encode()
        signature = base64.b64encode(
            hmac.new(signature_material, digestmod=hashlib.sha256).digest()
        ).decode()
        payload = dict(command.payload)
        # 飞书机器人接受 JSON 卡片；告警字段保持摘要形式，不加入消息正文。
        feishu_payload = {
            "timestamp": timestamp,
            "sign": signature,
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {
                    "template": "orange",
                    "title": {"tag": "plain_text", "content": "渠道告警"},
                },
                "body": {
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": json.dumps(payload, ensure_ascii=False),
                        }
                    ]
                },
            },
        }
        return await super().deliver(
            command=NotificationDeliveryCommand(
                target=command.target,
                payload=feishu_payload,
                idempotency_key=command.idempotency_key,
                timeout_seconds=command.timeout_seconds,
                max_retries=command.max_retries,
                settings=command.settings,
            ),
            secret=secret,
        )


class EmailTransport(Protocol):
    """SMTP 传输端口，便于单元测试不访问真实邮件服务。"""

    async def send(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        recipient: str,
        subject: str,
        body: str,
        timeout_seconds: float,
        use_tls: bool,
    ) -> None: ...


class SmtpEmailTransport:
    """标准库 SMTP 传输实现；阻塞调用放入线程池。"""

    async def send(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        recipient: str,
        subject: str,
        body: str,
        timeout_seconds: float,
        use_tls: bool,
    ) -> None:
        await asyncio.to_thread(
            self._send_sync,
            host,
            port,
            username,
            password,
            sender,
            recipient,
            subject,
            body,
            timeout_seconds,
            use_tls,
        )

    @staticmethod
    def _send_sync(
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        recipient: str,
        subject: str,
        body: str,
        timeout_seconds: float,
        use_tls: bool,
    ) -> None:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(host, port, timeout=timeout_seconds) as client:
            if use_tls:
                client.starttls()
            if username:
                client.login(username, password or "")
            client.send_message(message)


class EmailNotificationAdapter:
    """SMTP 邮件通知 Adapter，正文仅由安全摘要 JSON 组成。"""

    key = "email"
    display_name = "SMTP 邮件"

    def __init__(self, transport: EmailTransport | None = None) -> None:
        self._transport = transport or SmtpEmailTransport()

    async def deliver(
        self,
        *,
        command: NotificationDeliveryCommand,
        secret: str | None,
    ) -> NotificationDeliveryResult:
        settings = command.settings or {}
        if command.timeout_seconds <= 0 or not 0 <= command.max_retries <= 5:
            raise NotificationAdapterValidationError("邮件投递参数无效")
        host = _text(settings.get("smtp_host"))
        sender = _email(settings.get("from_address"))
        recipient = command.target.strip()
        if not host or not sender or not _email(recipient):
            raise NotificationAdapterValidationError("邮件 SMTP 或收件人配置无效")
        port = _integer(settings.get("smtp_port"), 1, 65535, default=587)
        username = _optional_text(settings.get("smtp_username"))
        if username and not secret:
            raise NotificationAdapterError("secret_missing", "邮件 SMTP 密码尚未配置")
        use_tls = settings.get("smtp_tls", True)
        if not isinstance(use_tls, bool):
            raise NotificationAdapterValidationError("邮件 TLS 配置无效")
        body = json.dumps(command.payload, ensure_ascii=False, sort_keys=True, indent=2)
        subject = "赛博网友渠道告警"
        attempts = 0
        while attempts <= command.max_retries:
            attempts += 1
            try:
                await self._transport.send(
                    host=host,
                    port=port,
                    username=username,
                    password=secret,
                    sender=sender,
                    recipient=recipient,
                    subject=subject,
                    body=body,
                    timeout_seconds=command.timeout_seconds,
                    use_tls=use_tls,
                )
                return NotificationDeliveryResult(
                    delivered=True,
                    attempts=attempts,
                    status_code=None,
                    idempotency_key=command.idempotency_key,
                    elapsed_ms=0,
                )
            except (TimeoutError, OSError, smtplib.SMTPException) as error:
                if attempts > command.max_retries:
                    raise NotificationAdapterError(
                        "email_unavailable",
                        "邮件服务暂时不可用",
                        retryable=True,
                    ) from error
                await asyncio.sleep(min(0.2 * 2 ** (attempts - 1), 2.0))
        raise AssertionError("邮件投递循环未返回")

    async def aclose(self) -> None:
        return None


def build_default_notification_registry(
    *, notifier: AlertWebhookNotifier | None = None
) -> NotificationAdapterRegistry:
    """构建通用 Webhook、飞书 Webhook 和邮件通知 Adapter。"""
    return NotificationAdapterRegistry(
        (
            HttpsWebhookNotificationAdapter(notifier),
            FeishuWebhookNotificationAdapter(notifier),
            EmailNotificationAdapter(),
        )
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _email(value: object) -> str:
    candidate = _text(value)
    if not candidate or "\r" in candidate or "\n" in candidate or "@" not in candidate:
        return ""
    return candidate


def _integer(value: object, minimum: int, maximum: int, *, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise NotificationAdapterValidationError("邮件 SMTP 端口配置无效")
    return value


__all__ = [
    "EmailNotificationAdapter",
    "EmailTransport",
    "FeishuWebhookNotificationAdapter",
    "HttpsWebhookNotificationAdapter",
    "NotificationAdapter",
    "NotificationAdapterError",
    "NotificationAdapterRegistry",
    "NotificationAdapterValidationError",
    "NotificationDeliveryCommand",
    "NotificationDeliveryResult",
    "SmtpEmailTransport",
    "build_default_notification_registry",
]
