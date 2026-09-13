"""告警 HTTPS Webhook 通知器。

该模块只处理网络投递协议，不知道告警领域对象，也不会记录请求正文、URL
查询参数、签名密钥或远端响应正文。
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlsplit

import httpx


class AlertWebhookValidationError(ValueError):
    """Webhook 地址或签名密钥不满足安全约束。"""


class AlertWebhookError(RuntimeError):
    """Webhook 投递失败的安全错误，不携带远端正文。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AlertWebhookDeliveryResult:
    """Webhook 投递的可审计结果。"""

    delivered: bool
    attempts: int
    status_code: int | None
    idempotency_key: str
    elapsed_ms: int


_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
        "instance-data.ec2.internal",
    }
)


def validate_webhook_url(value: str) -> str:
    """校验并规范化 Webhook 地址，拒绝常见 SSRF 目标。"""
    if not value.strip():
        raise AlertWebhookValidationError("Webhook 地址不能为空")
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme.casefold() != "https":
        raise AlertWebhookValidationError("Webhook 仅允许使用 HTTPS")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise AlertWebhookValidationError("Webhook 地址不得包含用户信息")
    host = parsed.hostname.rstrip(".").casefold()
    if host in _BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".local"):
        raise AlertWebhookValidationError("Webhook 地址指向受限主机")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    ):
        raise AlertWebhookValidationError("Webhook 地址不得指向内网或本机地址")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise AlertWebhookValidationError("Webhook 端口无效")
    return normalized


def _host_resolves_to_restricted_address(host: str) -> bool:
    """在投递前检查 DNS 解析结果，降低恶意域名解析到内网的风险。"""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            return True
    return False


class AlertWebhookNotifier:
    """使用 JSON + HMAC-SHA256 投递安全告警摘要。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve_dns: bool = True,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Webhook 超时必须为正数")
        if not 0 <= max_retries <= 5:
            raise ValueError("Webhook 重试次数必须位于 0 到 5 之间")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._transport = transport
        self._resolve_dns = resolve_dns

    async def notify(
        self,
        *,
        url: str,
        signing_secret: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> AlertWebhookDeliveryResult:
        target = validate_webhook_url(url)
        if not signing_secret or len(signing_secret) > 16_384:
            raise AlertWebhookValidationError("Webhook 签名密钥无效")
        if not idempotency_key or len(idempotency_key) > 255:
            raise AlertWebhookValidationError("Webhook 幂等键无效")
        request_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        request_retries = self.max_retries if max_retries is None else max_retries
        if request_timeout <= 0 or not 0 <= request_retries <= 5:
            raise AlertWebhookValidationError("Webhook 投递参数无效")
        hostname = urlsplit(target).hostname
        if self._resolve_dns and hostname and _host_resolves_to_restricted_address(hostname):
            raise AlertWebhookValidationError("Webhook 地址解析到受限主机")
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        signature = hmac.new(signing_secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "cyber-netizen-bot-alerts/1",
            "X-CNB-Idempotency-Key": idempotency_key,
            "X-CNB-Signature": f"sha256={signature}",
        }
        started = monotonic()
        attempts = 0
        last_status: int | None = None
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(request_timeout),
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            while attempts <= request_retries:
                attempts += 1
                try:
                    response = await client.post(target, content=body, headers=headers)
                    last_status = response.status_code
                    if 200 <= response.status_code < 300:
                        return AlertWebhookDeliveryResult(
                            delivered=True,
                            attempts=attempts,
                            status_code=response.status_code,
                            idempotency_key=idempotency_key,
                            elapsed_ms=int((monotonic() - started) * 1000),
                        )
                    retryable = response.status_code == 429 or response.status_code >= 500
                except (httpx.TimeoutException, httpx.NetworkError):
                    retryable = True
                except httpx.HTTPError as error:
                    raise AlertWebhookError(
                        "webhook_transport_error", "Webhook 请求失败"
                    ) from error
                if not retryable or attempts > request_retries:
                    raise AlertWebhookError(
                        (
                            "webhook_rejected"
                            if last_status and last_status < 500
                            else "webhook_unavailable"
                        ),
                        "Webhook 未接受告警通知",
                        retryable=retryable,
                    )
                await asyncio.sleep(min(0.2 * 2 ** (attempts - 1), 2.0))
        raise AssertionError("Webhook 投递循环未返回")


__all__ = [
    "AlertWebhookDeliveryResult",
    "AlertWebhookError",
    "AlertWebhookNotifier",
    "AlertWebhookValidationError",
    "validate_webhook_url",
]
