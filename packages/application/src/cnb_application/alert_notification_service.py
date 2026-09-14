"""渠道告警通知投递用例。"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from cnb_adapters import (
    AlertWebhookNotifier,
    NotificationAdapterError,
    NotificationAdapterRegistry,
    NotificationAdapterValidationError,
    NotificationDeliveryCommand,
    build_default_notification_registry,
)
from cnb_application.channel_service import ChannelService
from cnb_application.configuration_service import ConfigurationService, SecretStore
from cnb_domain import (
    BackgroundJobKind,
    ChannelAlert,
    ChannelAlertDispositionStatus,
    JsonValue,
)

if TYPE_CHECKING:
    from cnb_application.task_service import BackgroundTaskService, EnqueueResult


class AlertNotificationValidationError(ValueError):
    """通知命令或运行时配置不满足安全约束。"""


class AlertNotificationDisabledError(AlertNotificationValidationError):
    """当前 Agent 未启用告警通知。"""


class AlertNotificationNotConfiguredError(AlertNotificationValidationError):
    """当前 Agent 尚未配置完整的告警 Webhook。"""


class AlertNotificationSecretMissingError(AlertNotificationNotConfiguredError):
    """当前 Agent 尚未配置告警签名密钥。"""


class AlertNotificationDeliveryError(RuntimeError):
    """告警 Webhook 投递失败。"""

    def __init__(self, code: str, message: str = "告警通知投递失败") -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class AlertAuditRecorder(Protocol):
    """记录不含 Secret 和远端正文的安全审计事件。"""

    async def record_audit(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: str | None,
        detail: Mapping[str, JsonValue],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AlertNotificationResult:
    """告警通知投递的安全结果。"""

    delivered: bool
    alert_count: int
    attempts: int
    idempotency_key: str
    elapsed_ms: int
    status_code: int | None


class AlertNotificationService:
    """按当前租户和 Agent 投递安全的活动告警摘要。"""

    def __init__(
        self,
        *,
        channel_service: ChannelService,
        configuration_service: ConfigurationService,
        secret_store: SecretStore,
        notifier: AlertWebhookNotifier | None = None,
        adapter_registry: NotificationAdapterRegistry | None = None,
        audit_recorder: AlertAuditRecorder,
        task_service: "BackgroundTaskService | None" = None,
    ) -> None:
        self._channel_service = channel_service
        self._configuration_service = configuration_service
        self._secret_store = secret_store
        self._adapters = adapter_registry or build_default_notification_registry(notifier=notifier)
        self._audit_recorder = audit_recorder
        self._task_service = task_service

    async def enqueue(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        window_minutes: int = 60,
        confirmed: bool,
        adapter_key: str | None = None,
        now: datetime | None = None,
    ) -> "EnqueueResult":
        """创建安全通知任务；任务只持有配置引用和告警摘要。"""
        if self._task_service is None:
            raise AlertNotificationValidationError("通知任务服务尚未就绪")
        if not confirmed:
            raise AlertNotificationValidationError("告警通知投递必须显式确认")
        prepared = await self._prepare(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_minutes=window_minutes,
            adapter_key=adapter_key,
            now=now,
            require_secret=True,
        )
        (
            selected_adapter,
            _target,
            _secret_key,
            _settings,
            timeout_seconds,
            max_retries,
            payload,
            idempotency_key,
        ) = prepared
        task_payload: dict[str, JsonValue] = {
            "agent_id": str(agent_id),
            "adapter": selected_adapter,
            "delivery_payload": payload,
            "idempotency_key": idempotency_key,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
        }
        result = await self._task_service.enqueue(
            tenant_id=tenant_id,
            kind=BackgroundJobKind.NOTIFICATION_DELIVERY,
            payload=task_payload,
            deduplication_key=f"notification:{idempotency_key}:{selected_adapter}",
            created_by=actor_id,
        )
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="channel_alert.notification_queued",
            detail={
                "agent_id": str(agent_id),
                "adapter": selected_adapter,
                "alert_count": payload.get("alert_count", 0),
                "job_id": str(result.job.id),
                "idempotency_key": idempotency_key,
            },
        )
        return result

    async def notify(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        window_minutes: int = 60,
        confirmed: bool,
        adapter_key: str | None = None,
        now: datetime | None = None,
    ) -> AlertNotificationResult:
        if not confirmed:
            raise AlertNotificationValidationError("告警通知投递必须显式确认")
        if not 5 <= window_minutes <= 1_440:
            raise AlertNotificationValidationError("告警通知时间窗必须位于 5 到 1440 分钟之间")
        effective = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        values = effective.values
        if not self._boolean(
            values.get("alerts.notification.enabled"), "alerts.notification.enabled"
        ):
            raise AlertNotificationDisabledError("当前 Agent 未启用告警通知")
        selected_adapter = adapter_key or self._string(
            values.get("alerts.notification.adapter", "webhook"), "alerts.notification.adapter"
        )
        try:
            adapter = self._adapters.get(selected_adapter)
        except NotificationAdapterValidationError as error:
            raise AlertNotificationNotConfiguredError(str(error)) from error
        target, secret_key, adapter_settings = self._adapter_config(selected_adapter, values)
        if not target:
            raise AlertNotificationNotConfiguredError("当前 Agent 尚未配置通知目标")
        timeout_seconds = self._integer(
            values.get("alerts.notification.webhook_timeout_seconds"),
            "alerts.notification.webhook_timeout_seconds",
            minimum=1,
            maximum=30,
        )
        max_retries = self._integer(
            values.get("alerts.notification.webhook_max_retries"),
            "alerts.notification.webhook_max_retries",
            minimum=0,
            maximum=5,
        )
        alerts = await self._channel_service.alerts(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=None,
            values=values,
            window_minutes=window_minutes,
            now=now,
        )
        active_alerts = tuple(
            item
            for item in alerts
            if item.disposition_status is not ChannelAlertDispositionStatus.SUPPRESSED
        )
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        payload = self._payload(active_alerts, generated_at)
        idempotency_key = self._idempotency_key(tenant_id, agent_id, active_alerts)
        secret = await self._secret_store.resolve_secret(
            secret_key,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        if selected_adapter != "email" and not secret:
            raise AlertNotificationSecretMissingError("当前 Agent 尚未配置告警签名密钥")
        try:
            delivery = await adapter.deliver(
                command=NotificationDeliveryCommand(
                    target=target,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    timeout_seconds=timeout_seconds,
                    max_retries=max_retries,
                    settings=adapter_settings,
                ),
                secret=secret,
            )
        except NotificationAdapterError as error:
            await self._audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="channel_alert.notification_failed",
                detail={
                    "agent_id": str(agent_id),
                    "adapter": selected_adapter,
                    "alert_count": len(active_alerts),
                    "error_code": error.code,
                    "idempotency_key": idempotency_key,
                },
            )
            raise AlertNotificationDeliveryError(error.code) from error
        except NotificationAdapterValidationError as error:
            await self._audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="channel_alert.notification_failed",
                detail={
                    "agent_id": str(agent_id),
                    "adapter": selected_adapter,
                    "alert_count": len(active_alerts),
                    "error_code": "notification_validation_error",
                    "idempotency_key": idempotency_key,
                },
            )
            raise AlertNotificationDeliveryError("notification_validation_error") from error
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="channel_alert.notification_delivered",
            detail={
                "agent_id": str(agent_id),
                "adapter": selected_adapter,
                "alert_count": len(active_alerts),
                "attempts": delivery.attempts,
                "status_code": delivery.status_code,
                "elapsed_ms": delivery.elapsed_ms,
                "idempotency_key": delivery.idempotency_key,
            },
        )
        return AlertNotificationResult(
            delivered=delivery.delivered,
            alert_count=len(active_alerts),
            attempts=delivery.attempts,
            idempotency_key=delivery.idempotency_key,
            elapsed_ms=delivery.elapsed_ms,
            status_code=delivery.status_code,
        )

    async def _prepare(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_minutes: int,
        adapter_key: str | None,
        now: datetime | None,
        require_secret: bool,
    ) -> tuple[str, str, str, dict[str, object], int, int, dict[str, JsonValue], str]:
        if not 5 <= window_minutes <= 1_440:
            raise AlertNotificationValidationError("告警通知时间窗必须位于 5 到 1440 分钟之间")
        effective = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        values = effective.values
        if not self._boolean(
            values.get("alerts.notification.enabled"), "alerts.notification.enabled"
        ):
            raise AlertNotificationDisabledError("当前 Agent 未启用告警通知")
        selected_adapter = adapter_key or self._string(
            values.get("alerts.notification.adapter", "webhook"),
            "alerts.notification.adapter",
        )
        try:
            self._adapters.get(selected_adapter)
        except NotificationAdapterValidationError as error:
            raise AlertNotificationNotConfiguredError(str(error)) from error
        target, secret_key, adapter_settings = self._adapter_config(selected_adapter, values)
        if not target:
            raise AlertNotificationNotConfiguredError("当前 Agent 尚未配置通知目标")
        timeout_seconds = self._integer(
            values.get("alerts.notification.webhook_timeout_seconds"),
            "alerts.notification.webhook_timeout_seconds",
            minimum=1,
            maximum=30,
        )
        max_retries = self._integer(
            values.get("alerts.notification.webhook_max_retries"),
            "alerts.notification.webhook_max_retries",
            minimum=0,
            maximum=5,
        )
        alerts = await self._channel_service.alerts(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=None,
            values=values,
            window_minutes=window_minutes,
            now=now,
        )
        active_alerts = tuple(
            item
            for item in alerts
            if item.disposition_status is not ChannelAlertDispositionStatus.SUPPRESSED
        )
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        payload = self._payload(active_alerts, generated_at)
        idempotency_key = self._idempotency_key(tenant_id, agent_id, active_alerts)
        if require_secret:
            secret = await self._secret_store.resolve_secret(
                secret_key,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            if selected_adapter != "email" and not secret:
                raise AlertNotificationSecretMissingError("当前 Agent 尚未配置告警签名密钥")
        return (
            selected_adapter,
            target,
            secret_key,
            adapter_settings,
            timeout_seconds,
            max_retries,
            payload,
            idempotency_key,
        )

    @staticmethod
    def _adapter_config(
        adapter_key: str,
        values: Mapping[str, JsonValue],
    ) -> tuple[str, str, dict[str, object]]:
        if adapter_key == "webhook":
            return (
                AlertNotificationService._string(
                    values.get("alerts.notification.webhook_url"),
                    "alerts.notification.webhook_url",
                ),
                "alerts.notification.webhook_signing_secret",
                {},
            )
        if adapter_key == "feishu_webhook":
            return (
                AlertNotificationService._string(
                    values.get("alerts.notification.feishu_webhook_url"),
                    "alerts.notification.feishu_webhook_url",
                ),
                "alerts.notification.feishu_signing_secret",
                {},
            )
        if adapter_key == "email":
            settings: dict[str, object] = {
                "smtp_host": values.get("alerts.notification.email.smtp_host", ""),
                "smtp_port": values.get("alerts.notification.email.smtp_port", 587),
                "smtp_username": values.get("alerts.notification.email.smtp_username", ""),
                "from_address": values.get("alerts.notification.email.from_address", ""),
                "smtp_tls": values.get("alerts.notification.email.smtp_tls", True),
            }
            return (
                AlertNotificationService._string(
                    values.get("alerts.notification.email.recipient"),
                    "alerts.notification.email.recipient",
                ),
                "alerts.notification.email_password",
                settings,
            )
        raise AlertNotificationNotConfiguredError(f"未知通知适配器：{adapter_key}")

    async def _audit(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        detail: Mapping[str, JsonValue],
    ) -> None:
        await self._audit_recorder.record_audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type="channel_alert_notification",
            resource_id=None,
            detail=detail,
        )

    @staticmethod
    def _payload(alerts: tuple[ChannelAlert, ...], generated_at: datetime) -> dict[str, JsonValue]:
        return {
            "schema_version": "1",
            "event": "channel.alerts.active",
            "generated_at": generated_at.isoformat(),
            "alert_count": len(alerts),
            "alerts": [
                {
                    "alert_key": item.alert_key,
                    "channel_id": str(item.channel_id),
                    "code": item.code,
                    "error_code": item.error_code,
                    "severity": item.severity.value,
                    "occurrences": item.occurrences,
                    "current_value": item.current_value,
                    "threshold_value": item.threshold_value,
                    "unit": item.unit,
                    "last_occurred_at": item.last_occurred_at.isoformat(),
                }
                for item in alerts
            ],
        }

    @staticmethod
    def _idempotency_key(tenant_id: UUID, agent_id: UUID, alerts: tuple[ChannelAlert, ...]) -> str:
        material = {
            "tenant_id": str(tenant_id),
            "agent_id": str(agent_id),
            "alerts": [
                {"alert_key": item.alert_key, "last_occurred_at": item.last_occurred_at.isoformat()}
                for item in sorted(alerts, key=lambda value: value.alert_key)
            ],
        }
        return hashlib.sha256(
            json.dumps(material, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()

    @staticmethod
    def _boolean(value: JsonValue | None, key: str) -> bool:
        if not isinstance(value, bool):
            raise AlertNotificationValidationError(f"配置 {key} 类型无效")
        return value

    @staticmethod
    def _string(value: JsonValue | None, key: str) -> str:
        if not isinstance(value, str):
            raise AlertNotificationValidationError(f"配置 {key} 类型无效")
        return value.strip()

    @staticmethod
    def _integer(value: JsonValue | None, key: str, *, minimum: int, maximum: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise AlertNotificationValidationError(f"配置 {key} 超出允许范围")
        return value


__all__ = [
    "AlertAuditRecorder",
    "AlertNotificationDeliveryError",
    "AlertNotificationDisabledError",
    "AlertNotificationNotConfiguredError",
    "AlertNotificationResult",
    "AlertNotificationSecretMissingError",
    "AlertNotificationService",
    "AlertNotificationValidationError",
]
