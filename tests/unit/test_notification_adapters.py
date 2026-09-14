"""通知 Adapter 的协议、安全边界和可测试传输。"""

import httpx
import pytest

from cnb_adapters import (
    AlertWebhookNotifier,
    EmailNotificationAdapter,
    EmailTransport,
    FeishuWebhookNotificationAdapter,
    HttpsWebhookNotificationAdapter,
    NotificationAdapterError,
    NotificationDeliveryCommand,
    build_default_notification_registry,
)


class RecordingEmailTransport(EmailTransport):
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[dict[str, object]] = []

    async def send(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise OSError("smtp failure body must not escape")


@pytest.mark.asyncio
async def test_webhook_adapter_reuses_ssrf_and_hmac_boundary() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    adapter = HttpsWebhookNotificationAdapter(
        notifier=AlertWebhookNotifier(
            transport=httpx.MockTransport(handler),
            resolve_dns=False,
        )
    )
    result = await adapter.deliver(
        command=NotificationDeliveryCommand(
            target="https://notify.example.invalid/hook",
            payload={"event": "safe", "count": 1},
            idempotency_key="idempotency-1",
            max_retries=0,
        ),
        secret="signing-secret",
    )
    assert result.delivered is True
    assert requests[0].headers["X-CNB-Idempotency-Key"] == "idempotency-1"
    assert requests[0].headers["X-CNB-Signature"].startswith("sha256=")


@pytest.mark.asyncio
async def test_feishu_adapter_adds_native_timestamp_signature() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    adapter = FeishuWebhookNotificationAdapter(
        notifier=AlertWebhookNotifier(
            transport=httpx.MockTransport(handler),
            resolve_dns=False,
        )
    )
    await adapter.deliver(
        command=NotificationDeliveryCommand(
            target="https://open.feishu.example.invalid/hook",
            payload={"event": "safe"},
            idempotency_key="feishu-idempotency",
            max_retries=0,
        ),
        secret="feishu-secret",
    )
    body = requests[0].content.decode()
    assert '"timestamp"' in body
    assert '"sign"' in body
    assert "feishu-secret" not in body


@pytest.mark.asyncio
async def test_email_adapter_retries_without_exposing_password_or_body() -> None:
    transport = RecordingEmailTransport(failures=1)
    adapter = EmailNotificationAdapter(transport)
    result = await adapter.deliver(
        command=NotificationDeliveryCommand(
            target="ops@example.com",
            payload={"event": "channel.alerts.active", "alert_count": 0},
            idempotency_key="email-idempotency",
            max_retries=1,
            settings={
                "smtp_host": "mail.example.invalid",
                "smtp_port": 587,
                "from_address": "bot@example.com",
                "smtp_tls": True,
            },
        ),
        secret="smtp-password",
    )
    assert result.delivered is True
    assert result.attempts == 2
    assert transport.calls[0]["password"] == "smtp-password"
    assert "smtp-password" not in str(result)


@pytest.mark.asyncio
async def test_email_adapter_reports_safe_terminal_error() -> None:
    transport = RecordingEmailTransport(failures=3)
    adapter = EmailNotificationAdapter(transport)
    with pytest.raises(NotificationAdapterError) as captured:
        await adapter.deliver(
            command=NotificationDeliveryCommand(
                target="ops@example.com",
                payload={"event": "safe"},
                idempotency_key="email-idempotency",
                max_retries=1,
                settings={
                    "smtp_host": "mail.example.invalid",
                    "from_address": "bot@example.com",
                },
            ),
            secret="password",
        )
    assert captured.value.code == "email_unavailable"
    assert "smtp failure body" not in str(captured.value)


def test_default_notification_registry_has_stable_keys() -> None:
    registry = build_default_notification_registry()
    assert [adapter.key for adapter in registry.all()] == [
        "webhook",
        "feishu_webhook",
        "email",
    ]
