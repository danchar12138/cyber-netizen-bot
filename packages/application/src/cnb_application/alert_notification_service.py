"""渠道告警通知投递用例。"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast
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
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    ChannelAlert,
    ChannelAlertDispositionStatus,
    ChannelAlertLifecycle,
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


@dataclass(frozen=True, slots=True)
class PreparedAlertNotification:
    """已通过配置校验、但仍不包含运行时密钥的通知准备结果。"""

    adapter: str
    target: str
    secret_key: str
    settings: dict[str, object]
    timeout_seconds: int
    max_retries: int
    payload: dict[str, JsonValue]
    idempotency_key: str
    recovery_enabled: bool
    escalated_lifecycle_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class NotificationDeliveryTimelineItem:
    """通知任务的安全时间线投影。"""

    job_id: UUID
    status: BackgroundJobStatus
    adapter: str
    event: str
    alert_count: int
    attempt_count: int
    max_attempts: int
    consecutive_failures: int
    last_error_code: str | None
    delivered: bool | None
    status_code: int | None
    elapsed_ms: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationDeliveryTimeline:
    """通知投递时间线及当前 Agent 的安全聚合。"""

    total: int
    pending: int
    running: int
    retrying: int
    succeeded: int
    failed: int
    dead_letters: int
    current_consecutive_failures: int
    last_succeeded_at: datetime | None
    items: tuple[NotificationDeliveryTimelineItem, ...]


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
        return await self._enqueue_prepared(
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor_id=actor_id,
            prepared=prepared,
        )

    async def enqueue_if_active(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        window_minutes: int = 60,
        now: datetime | None = None,
    ) -> "EnqueueResult | None":
        """有活动告警且通知完整配置时入队；禁用或未配置时安全跳过。"""
        if self._task_service is None:
            raise AlertNotificationValidationError("通知任务服务尚未就绪")
        try:
            prepared = await self._prepare(
                tenant_id=tenant_id,
                agent_id=agent_id,
                window_minutes=window_minutes,
                adapter_key=None,
                now=now,
                require_secret=True,
            )
        except (AlertNotificationDisabledError, AlertNotificationNotConfiguredError):
            return None
        if prepared.payload.get("alert_count") == 0:
            if not prepared.recovery_enabled:
                return None
            recovery = await self._prepare_recovery(
                tenant_id=tenant_id,
                agent_id=agent_id,
                adapter=prepared.adapter,
                generated_at=(now or datetime.now(UTC)).astimezone(UTC),
            )
            if recovery is None:
                return None
            prepared = replace(prepared, payload=recovery[0], idempotency_key=recovery[1])
        return await self._enqueue_prepared(
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor_id=actor_id,
            prepared=prepared,
        )

    async def _enqueue_prepared(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        prepared: PreparedAlertNotification,
    ) -> "EnqueueResult":
        task_service = self._task_service
        if task_service is None:
            raise AlertNotificationValidationError("通知任务服务尚未就绪")
        selected_adapter = prepared.adapter
        payload = prepared.payload
        idempotency_key = prepared.idempotency_key
        timeout_seconds = prepared.timeout_seconds
        max_retries = prepared.max_retries
        task_payload: dict[str, JsonValue] = {
            "agent_id": str(agent_id),
            "adapter": selected_adapter,
            "delivery_payload": payload,
            "idempotency_key": idempotency_key,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
        }
        result = await task_service.enqueue(
            tenant_id=tenant_id,
            kind=BackgroundJobKind.NOTIFICATION_DELIVERY,
            payload=task_payload,
            deduplication_key=f"notification:{idempotency_key}:{selected_adapter}",
            created_by=actor_id,
        )
        if prepared.escalated_lifecycle_ids:
            await self._channel_service.mark_alert_lifecycles_escalated(
                tenant_id=tenant_id,
                agent_id=agent_id,
                lifecycle_ids=prepared.escalated_lifecycle_ids,
                escalated_at=datetime.now(UTC),
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

    async def timeline(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: BackgroundJobStatus | None = None,
        adapter: str | None = None,
        event: str | None = None,
        limit: int = 100,
    ) -> NotificationDeliveryTimeline:
        """返回当前 Agent 的通知投递状态和连续失败趋势。"""
        if self._task_service is None:
            raise AlertNotificationValidationError("通知任务服务尚未就绪")
        if not 1 <= limit <= 500:
            raise AlertNotificationValidationError("通知时间线数量必须位于 1 到 500 之间")
        selected_adapter = adapter.strip().casefold() if adapter is not None else None
        if selected_adapter == "":
            raise AlertNotificationValidationError("通知适配器不能为空")
        selected_event = event.strip().casefold() if event else None
        if selected_event not in {None, "active", "escalation", "recovery", "unknown"}:
            raise AlertNotificationValidationError("通知事件类型无效")
        jobs = await self._task_service.list_notification_jobs(
            tenant_id=tenant_id,
            agent_id=agent_id,
            limit=500,
        )
        records: list[NotificationDeliveryTimelineItem] = []
        consecutive = 0
        last_succeeded_at: datetime | None = None
        counts = {status: 0 for status in BackgroundJobStatus}
        for job in sorted(jobs, key=lambda item: (item.created_at, str(item.id))):
            counts[job.status] += 1
            if job.status in {BackgroundJobStatus.FAILED, BackgroundJobStatus.DEAD_LETTER}:
                consecutive += 1
            elif job.status is BackgroundJobStatus.SUCCEEDED:
                consecutive = 0
                last_succeeded_at = job.completed_at or job.updated_at
            item = self._timeline_item(job, consecutive)
            if selected_adapter is not None and item.adapter.casefold() != selected_adapter:
                continue
            if status is not None and item.status is not status:
                continue
            if selected_event is not None and item.event != selected_event:
                continue
            records.append(item)
        records.sort(key=lambda item: (item.created_at, str(item.job_id)), reverse=True)
        return NotificationDeliveryTimeline(
            total=len(jobs),
            pending=counts[BackgroundJobStatus.PENDING],
            running=counts[BackgroundJobStatus.RUNNING],
            retrying=counts[BackgroundJobStatus.RETRYING],
            succeeded=counts[BackgroundJobStatus.SUCCEEDED],
            failed=counts[BackgroundJobStatus.FAILED],
            dead_letters=counts[BackgroundJobStatus.DEAD_LETTER],
            current_consecutive_failures=consecutive,
            last_succeeded_at=last_succeeded_at,
            items=tuple(records[:limit]),
        )

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
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        evaluation = await self._channel_service.reconcile_alert_lifecycles(
            tenant_id=tenant_id,
            agent_id=agent_id,
            values=values,
            window_minutes=window_minutes,
            now=generated_at,
        )
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
        suppressed_keys = {
            item.alert_key
            for item in evaluation.alerts
            if item.disposition_status is ChannelAlertDispositionStatus.SUPPRESSED
        }
        active_lifecycles = tuple(
            item for item in evaluation.active_lifecycles if item.alert_key not in suppressed_keys
        )
        due_escalations = tuple(
            item for item in evaluation.due_escalations if item.alert_key not in suppressed_keys
        )
        event = "channel.alerts.escalated" if due_escalations else "channel.alerts.active"
        selected_lifecycles = due_escalations or active_lifecycles
        payload = self._lifecycle_payload(selected_lifecycles, generated_at, event=event)
        idempotency_key = self._lifecycle_idempotency_key(
            tenant_id,
            agent_id,
            selected_lifecycles,
            event=event,
        )
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
                    "alert_count": len(selected_lifecycles),
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
                    "alert_count": len(selected_lifecycles),
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
                "alert_count": len(selected_lifecycles),
                "attempts": delivery.attempts,
                "status_code": delivery.status_code,
                "elapsed_ms": delivery.elapsed_ms,
                "idempotency_key": delivery.idempotency_key,
            },
        )
        if due_escalations:
            await self._channel_service.mark_alert_lifecycles_escalated(
                tenant_id=tenant_id,
                agent_id=agent_id,
                lifecycle_ids=tuple(item.id for item in due_escalations),
                escalated_at=generated_at,
            )
        return AlertNotificationResult(
            delivered=delivery.delivered,
            alert_count=len(selected_lifecycles),
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
    ) -> PreparedAlertNotification:
        if not 5 <= window_minutes <= 1_440:
            raise AlertNotificationValidationError("告警通知时间窗必须位于 5 到 1440 分钟之间")
        effective = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        values = effective.values
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        evaluation = await self._channel_service.reconcile_alert_lifecycles(
            tenant_id=tenant_id,
            agent_id=agent_id,
            values=values,
            window_minutes=window_minutes,
            now=generated_at,
        )
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
        suppressed_keys = {
            item.alert_key
            for item in evaluation.alerts
            if item.disposition_status is ChannelAlertDispositionStatus.SUPPRESSED
        }
        active_lifecycles = tuple(
            item for item in evaluation.active_lifecycles if item.alert_key not in suppressed_keys
        )
        due_escalations = tuple(
            item for item in evaluation.due_escalations if item.alert_key not in suppressed_keys
        )
        event = "channel.alerts.escalated" if due_escalations else "channel.alerts.active"
        selected_lifecycles = due_escalations or active_lifecycles
        payload = self._lifecycle_payload(selected_lifecycles, generated_at, event=event)
        idempotency_key = self._lifecycle_idempotency_key(
            tenant_id,
            agent_id,
            selected_lifecycles,
            event=event,
        )
        if require_secret:
            secret = await self._secret_store.resolve_secret(
                secret_key,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            if selected_adapter != "email" and not secret:
                raise AlertNotificationSecretMissingError("当前 Agent 尚未配置告警签名密钥")
        return PreparedAlertNotification(
            adapter=selected_adapter,
            target=target,
            secret_key=secret_key,
            settings=adapter_settings,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            payload=payload,
            idempotency_key=idempotency_key,
            recovery_enabled=self._boolean(
                values.get("alerts.notification.recovery_enabled", True),
                "alerts.notification.recovery_enabled",
            ),
            escalated_lifecycle_ids=tuple(item.id for item in due_escalations),
        )

    async def _prepare_recovery(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        adapter: str,
        generated_at: datetime,
    ) -> tuple[dict[str, JsonValue], str] | None:
        """只从最近一次已成功活动通知派生一次恢复事件。"""
        if self._task_service is None:
            return None
        jobs = await self._task_service.list_notification_jobs(
            tenant_id=tenant_id,
            agent_id=agent_id,
            limit=500,
        )
        for job in jobs:
            if job.status is not BackgroundJobStatus.SUCCEEDED:
                continue
            if job.payload.get("adapter") != adapter:
                continue
            delivery_payload = job.payload.get("delivery_payload")
            if not isinstance(delivery_payload, dict):
                continue
            if delivery_payload.get("event") not in {
                "channel.alerts.active",
                "channel.alerts.escalated",
            }:
                return None
            count = delivery_payload.get("alert_count")
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                continue
            payload: dict[str, JsonValue] = {
                "schema_version": "1",
                "event": "channel.alerts.recovered",
                "generated_at": generated_at.isoformat(),
                "alert_count": count,
                "recovered_alert_keys": self._recovered_keys(delivery_payload),
            }
            material = {
                "tenant_id": str(tenant_id),
                "agent_id": str(agent_id),
                "adapter": adapter,
                "source_job_id": str(job.id),
                "event": "channel.alerts.recovered",
            }
            key = hashlib.sha256(
                json.dumps(material, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest()
            return payload, key
        return None

    @staticmethod
    def _recovered_keys(payload: Mapping[str, object]) -> list[JsonValue]:
        entries = payload.get("alerts")
        if not isinstance(entries, list):
            return []
        keys: list[JsonValue] = []
        for entry in cast("list[object]", entries):
            if not isinstance(entry, dict):
                continue
            key = cast("dict[str, object]", entry).get("alert_key")
            if isinstance(key, str):
                keys.append(key)
        return keys

    @staticmethod
    def _timeline_item(
        job: BackgroundJob,
        consecutive_failures: int,
    ) -> NotificationDeliveryTimelineItem:
        adapter = job.payload.get("adapter")
        selected_adapter = (
            adapter.strip() if isinstance(adapter, str) and adapter.strip() else "unknown"
        )
        delivery_payload = job.payload.get("delivery_payload")
        payload = delivery_payload if isinstance(delivery_payload, dict) else {}
        event_value = payload.get("event")
        event = (
            "active"
            if event_value == "channel.alerts.active"
            else "escalation"
            if event_value == "channel.alerts.escalated"
            else "recovery"
            if event_value == "channel.alerts.recovered"
            else "unknown"
        )
        alert_count = payload.get("alert_count", 0)
        if not isinstance(alert_count, int) or isinstance(alert_count, bool) or alert_count < 0:
            alert_count = 0
        delivered = job.result_summary.get("delivered")
        delivered_value = delivered if isinstance(delivered, bool) else None
        status_code = job.result_summary.get("status_code")
        status_code_value = (
            status_code
            if isinstance(status_code, int) and not isinstance(status_code, bool)
            else None
        )
        elapsed_ms = job.result_summary.get("elapsed_ms")
        elapsed_value = (
            elapsed_ms if isinstance(elapsed_ms, int) and not isinstance(elapsed_ms, bool) else None
        )
        return NotificationDeliveryTimelineItem(
            job_id=job.id,
            status=job.status,
            adapter=selected_adapter,
            event=event,
            alert_count=alert_count,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            consecutive_failures=consecutive_failures,
            last_error_code=job.last_error_code,
            delivered=delivered_value,
            status_code=status_code_value,
            elapsed_ms=elapsed_value,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            updated_at=job.updated_at,
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
    def _lifecycle_payload(
        lifecycles: tuple[ChannelAlertLifecycle, ...],
        generated_at: datetime,
        *,
        event: str,
    ) -> dict[str, JsonValue]:
        return {
            "schema_version": "1",
            "event": event,
            "generated_at": generated_at.isoformat(),
            "alert_count": len(lifecycles),
            "alerts": [
                {
                    "lifecycle_id": str(item.id),
                    "alert_key": item.alert_key,
                    "channel_id": str(item.channel_id),
                    "code": item.code,
                    "error_code": item.error_code,
                    "severity": item.severity.value,
                    "occurrences": item.occurrences,
                    "current_value": item.current_value,
                    "threshold_value": item.threshold_value,
                    "unit": item.unit,
                    "first_occurred_at": item.first_occurred_at.isoformat(),
                    "last_occurred_at": item.last_occurred_at.isoformat(),
                }
                for item in lifecycles
            ],
        }

    @staticmethod
    def _lifecycle_idempotency_key(
        tenant_id: UUID,
        agent_id: UUID,
        lifecycles: tuple[ChannelAlertLifecycle, ...],
        *,
        event: str,
    ) -> str:
        material = {
            "tenant_id": str(tenant_id),
            "agent_id": str(agent_id),
            "event": event,
            "lifecycle_ids": sorted(str(item.id) for item in lifecycles),
        }
        return hashlib.sha256(
            json.dumps(material, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()

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
    "NotificationDeliveryTimeline",
    "NotificationDeliveryTimelineItem",
    "PreparedAlertNotification",
]
