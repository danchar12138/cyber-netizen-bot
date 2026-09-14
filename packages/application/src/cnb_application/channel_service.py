"""渠道实例、凭证边界、能力协商、限流和安全诊断用例。"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from secrets import compare_digest
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from cnb_adapters import (
    AdapterDeliveryResult,
    ChannelAdapterError,
    ChannelAdapterRegistry,
    ChannelCapabilityError,
    ChannelDeliveryCommand,
    ChannelInboundEvent,
    ChannelNotConfiguredError,
    ChannelRateLimitError,
    WebhookInfo,
    negotiate_capabilities,
    summarize_blocks,
)
from cnb_application.configuration_service import SecretStore
from cnb_domain import (
    AlertSeverity,
    ChannelAlert,
    ChannelAlertDisposition,
    ChannelAlertDispositionStatus,
    ChannelAlertLifecycle,
    ChannelAlertLifecycleStatus,
    ChannelCapabilities,
    ChannelDiagnosticEvent,
    ChannelErrorMetrics,
    ChannelEventDirection,
    ChannelEventStatus,
    ChannelHealthSnapshot,
    ChannelHealthStatus,
    ChannelInstance,
    ChannelInstanceStatus,
    ChannelOperationMetrics,
    ChannelPlatform,
    ConfigScope,
    JsonValue,
    MultimodalContentBlock,
)


class ChannelValidationError(ValueError):
    """渠道命令、设置或多模态内容不满足稳定约束。"""


class ChannelNotFoundError(LookupError):
    """渠道实例不属于当前租户。"""


class ChannelConflictError(RuntimeError):
    """渠道名称、状态或凭证操作发生冲突。"""


@dataclass(frozen=True, slots=True)
class ChannelCatalogItem:
    """管理后台可查看的 Adapter 静态描述。"""

    platform: ChannelPlatform
    display_name: str
    implementation_status: Literal["ready", "placeholder"]
    credential_required: bool
    capabilities: ChannelCapabilities


@dataclass(frozen=True, slots=True)
class ChannelInstanceView:
    """渠道实例与只表示是否配置的凭证状态。"""

    instance: ChannelInstance
    display_name: str
    implementation_status: Literal["ready", "placeholder"]
    credential_configured: bool
    inbound_webhook_configured: bool
    capabilities: ChannelCapabilities


@dataclass(frozen=True, slots=True)
class ChannelSimulationResult:
    """平台模拟器输出，不产生外部副作用。"""

    platform: ChannelPlatform
    blocks: tuple[MultimodalContentBlock, ...]
    degradations: tuple[str, ...]
    buffered: bool
    thread_preserved: bool
    edit_preserved: bool


@dataclass(frozen=True, slots=True)
class ChannelDeliveryReceipt:
    """实际 Adapter 发送结果或幂等重放结果。"""

    status: ChannelEventStatus
    external_message_id: str
    degradations: tuple[str, ...]
    delivered_at: datetime
    idempotent_replay: bool


@dataclass(frozen=True, slots=True)
class TelegramWebhookContext:
    """已完成密钥校验的 Telegram 公开入站归属，绝不离开应用层。"""

    tenant_id: UUID
    agent_id: UUID
    channel_id: UUID


@dataclass(frozen=True, slots=True)
class TelegramWebhookStatus:
    """Telegram Webhook 的运营状态摘要，不包含 URL、Secret 或远端错误正文。"""

    channel_id: UUID
    status: ChannelHealthStatus
    configured: bool
    pending_update_count: int
    last_error_at: datetime | None
    last_error_present: bool
    allowed_updates: tuple[str, ...]
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class ChannelAlertLifecycleEvaluation:
    """一次告警评估得到的活动事件与待升级事件。"""

    alerts: tuple[ChannelAlert, ...]
    active_lifecycles: tuple[ChannelAlertLifecycle, ...]
    due_escalations: tuple[ChannelAlertLifecycle, ...]


@dataclass(frozen=True, slots=True)
class ChannelAlertLifecycleTrendPoint:
    """一个固定时间桶内的生命周期变化计数。"""

    bucket_started_at: datetime
    opened: int
    resolved: int
    escalated: int


@dataclass(frozen=True, slots=True)
class ChannelAlertLifecycleMetrics:
    """当前 Agent 的告警生命周期聚合与趋势。"""

    window_started_at: datetime
    window_ended_at: datetime
    active: int
    opened: int
    resolved: int
    escalated: int
    mean_recovery_seconds: float
    p95_recovery_seconds: int
    trend: tuple[ChannelAlertLifecycleTrendPoint, ...]


class ChannelRepository(Protocol):
    """渠道实例、限流窗口和诊断事件持久化端口。"""

    async def create_instance(self, instance: ChannelInstance) -> ChannelInstance: ...

    async def get_instance(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
    ) -> ChannelInstance | None: ...

    async def get_instance_by_id(self, *, channel_id: UUID) -> ChannelInstance | None: ...

    async def list_instances(
        self, *, tenant_id: UUID, agent_id: UUID
    ) -> tuple[ChannelInstance, ...]: ...

    async def list_probe_candidates(self) -> tuple[ChannelInstance, ...]: ...

    async def update_instance(
        self,
        *,
        instance: ChannelInstance,
        actor_id: UUID,
        action: str,
    ) -> ChannelInstance: ...

    async def record_event(
        self,
        event: ChannelDiagnosticEvent,
    ) -> ChannelDiagnosticEvent: ...

    async def reserve_delivery(self, event: ChannelDiagnosticEvent) -> bool: ...

    async def get_event_by_idempotency(
        self,
        *,
        tenant_id: UUID,
        channel_id: UUID,
        direction: ChannelEventDirection,
        idempotency_key: str,
    ) -> ChannelDiagnosticEvent | None: ...

    async def list_events(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        event_type: str | None,
        limit: int,
    ) -> tuple[ChannelDiagnosticEvent, ...]: ...

    async def get_operation_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> tuple[ChannelOperationMetrics, ...]: ...

    async def get_error_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> tuple[ChannelErrorMetrics, ...]: ...

    async def record_health_snapshot(
        self, snapshot: ChannelHealthSnapshot
    ) -> ChannelHealthSnapshot: ...

    async def list_health_snapshots(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_started_at: datetime,
        window_ended_at: datetime,
        limit: int,
    ) -> tuple[ChannelHealthSnapshot, ...]: ...

    async def reserve_rate_limit(
        self,
        *,
        tenant_id: UUID,
        channel_id: UUID,
        limit: int,
        now: datetime,
    ) -> bool: ...

    async def get_alert_disposition(
        self, *, tenant_id: UUID, agent_id: UUID, alert_key: str
    ) -> ChannelAlertDisposition | None: ...

    async def list_alert_dispositions(
        self, *, tenant_id: UUID, agent_id: UUID
    ) -> tuple[ChannelAlertDisposition, ...]: ...

    async def save_alert_disposition(
        self, disposition: ChannelAlertDisposition
    ) -> ChannelAlertDisposition: ...

    async def clear_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alert_key: str,
        actor_id: UUID | None = None,
    ) -> None: ...

    async def reconcile_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alerts: tuple[ChannelAlert, ...],
        observed_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]: ...

    async def mark_alert_lifecycles_escalated(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        escalated_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]: ...

    async def list_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ChannelAlertLifecycleStatus | None,
        severity: AlertSeverity | None,
        limit: int,
    ) -> tuple[ChannelAlertLifecycle, ...]: ...

    async def list_alert_lifecycles_in_window(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]: ...


_CREDENTIAL_KEYS = {
    ChannelPlatform.FEISHU: "channel.feishu.bot_credential",
    ChannelPlatform.DISCORD: "channel.discord.bot_token",
    ChannelPlatform.TELEGRAM: "channel.telegram.bot_token",
}
_TELEGRAM_WEBHOOK_SECRET_KEY = "telegram_webhook_secret"
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "credential")


class ChannelService:
    """管理平台接入，并将外部副作用关在能力、限流和幂等边界之后。"""

    def __init__(
        self,
        repository: ChannelRepository,
        adapters: ChannelAdapterRegistry,
        secret_store: SecretStore,
    ) -> None:
        self._repository = repository
        self._adapters = adapters
        self._secret_store = secret_store

    def catalog(self) -> tuple[ChannelCatalogItem, ...]:
        return tuple(
            ChannelCatalogItem(
                platform=adapter.platform,
                display_name=adapter.display_name,
                implementation_status=adapter.implementation_status,
                credential_required=adapter.platform is not ChannelPlatform.WEB,
                capabilities=adapter.capabilities,
            )
            for adapter in self._adapters.all()
        )

    async def create(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        name: str,
        platform: ChannelPlatform,
        status: ChannelInstanceStatus,
        rate_limit_per_minute: int,
        settings: Mapping[str, JsonValue],
        credential: str | None,
        actor_id: UUID,
    ) -> ChannelInstanceView:
        now = datetime.now(UTC)
        normalized_settings = self._settings(settings)
        if credential is not None:
            if platform is ChannelPlatform.WEB:
                raise ChannelValidationError("内部 Web 适配器不使用外部凭证")
            self._validate_credential(platform, credential)
        instance = ChannelInstance(
            id=uuid4(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            name=self._text(name, "渠道名称", 120),
            platform=platform,
            status=status,
            rate_limit_per_minute=self._rate_limit(rate_limit_per_minute),
            settings=normalized_settings,
            health_status=(
                ChannelHealthStatus.NOT_CONFIGURED
                if status is ChannelInstanceStatus.ENABLED
                else ChannelHealthStatus.DISABLED
            ),
            health_detail=None,
            last_checked_at=None,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        try:
            stored = await self._repository.create_instance(instance)
        except ValueError as error:
            raise ChannelConflictError(str(error)) from error
        if credential is not None:
            await self.set_credential(
                tenant_id=tenant_id,
                agent_id=agent_id,
                channel_id=stored.id,
                plaintext=credential,
                actor_id=actor_id,
            )
        return await self._view(stored)

    async def get(
        self, *, tenant_id: UUID, agent_id: UUID, channel_id: UUID
    ) -> ChannelInstanceView:
        instance = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        return await self._view(instance)

    async def list(self, *, tenant_id: UUID, agent_id: UUID) -> tuple[ChannelInstanceView, ...]:
        instances = await self._repository.list_instances(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        return tuple([await self._view(instance) for instance in instances])

    async def update(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        actor_id: UUID,
        name: str | None,
        status: ChannelInstanceStatus | None,
        rate_limit_per_minute: int | None,
        settings: Mapping[str, JsonValue] | None,
        confirmed: bool,
    ) -> ChannelInstanceView:
        if not confirmed:
            raise ChannelValidationError("渠道状态与设置变更必须明确确认")
        current = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        updated = replace(
            current,
            name=self._text(name, "渠道名称", 120) if name is not None else current.name,
            status=status or current.status,
            rate_limit_per_minute=(
                self._rate_limit(rate_limit_per_minute)
                if rate_limit_per_minute is not None
                else current.rate_limit_per_minute
            ),
            settings=self._settings(settings) if settings is not None else current.settings,
            health_status=(
                ChannelHealthStatus.DISABLED
                if status is ChannelInstanceStatus.DISABLED
                else ChannelHealthStatus.NOT_CONFIGURED
                if status is ChannelInstanceStatus.ENABLED
                else current.health_status
            ),
            health_detail=None if status is not None else current.health_detail,
            last_checked_at=None if status is not None else current.last_checked_at,
            updated_at=datetime.now(UTC),
        )
        stored = await self._repository.update_instance(
            instance=updated,
            actor_id=actor_id,
            action="channel_instance.updated",
        )
        return await self._view(stored)

    async def set_credential(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        plaintext: str,
        actor_id: UUID,
    ) -> ChannelInstanceView:
        instance = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        if instance.platform is ChannelPlatform.WEB:
            raise ChannelConflictError("内部 Web 适配器不使用外部凭证")
        if not plaintext:
            raise ChannelValidationError("渠道凭证不能为空")
        self._validate_credential(instance.platform, plaintext)
        await self._secret_store.set_secret(
            key=_CREDENTIAL_KEYS[instance.platform],
            scope_type=ConfigScope.CHANNEL,
            scope_id=instance.id,
            plaintext=plaintext,
            actor_id=actor_id,
        )
        return await self._view(instance)

    async def clear_credential(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        actor_id: UUID,
        confirmed: bool,
    ) -> ChannelInstanceView:
        if not confirmed:
            raise ChannelValidationError("清除渠道凭证必须明确确认")
        instance = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        key = _CREDENTIAL_KEYS.get(instance.platform)
        if key is None:
            raise ChannelConflictError("内部 Web 适配器没有可清除凭证")
        metadata = await self._secret_store.list_metadata()
        match = next(
            (
                item
                for item in metadata
                if item.key == key
                and item.scope_type is ConfigScope.CHANNEL
                and item.scope_id == channel_id
            ),
            None,
        )
        if match is not None:
            await self._secret_store.clear_secret(match.id, actor_id=actor_id)
        return await self._view(instance)

    async def test_connection(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        actor_id: UUID,
    ) -> ChannelInstanceView:
        instance = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        connection_error_code: str | None = None
        if instance.status is ChannelInstanceStatus.DISABLED:
            health_status = ChannelHealthStatus.DISABLED
            detail = "渠道实例已停用，未执行连接测试。"
            checked_at = datetime.now(UTC)
        else:
            credential = await self._resolve_credential(instance)
            if instance.platform is not ChannelPlatform.WEB and credential is None:
                health_status = ChannelHealthStatus.NOT_CONFIGURED
                detail = "渠道尚未写入凭证，未访问外部 API。"
                checked_at = datetime.now(UTC)
            else:
                try:
                    health = await self._adapters.get(instance.platform).test_connection(
                        credential=credential
                    )
                    health_status = health.status
                    detail = health.detail
                    checked_at = health.checked_at
                except ChannelAdapterError as error:
                    health_status = ChannelHealthStatus.DEGRADED
                    detail = error.safe_message
                    connection_error_code = error.code
                    checked_at = datetime.now(UTC)
        updated = replace(
            instance,
            health_status=health_status,
            health_detail=self._text(detail, "健康详情", 500),
            last_checked_at=checked_at,
            updated_at=checked_at,
        )
        stored = await self._repository.update_instance(
            instance=updated,
            actor_id=actor_id,
            action="channel_instance.connection_tested",
        )
        await self._repository.record_event(
            self._event(
                instance=stored,
                direction=ChannelEventDirection.SYSTEM,
                event_type="connection.tested",
                status=(
                    ChannelEventStatus.DELIVERED
                    if health_status is ChannelHealthStatus.HEALTHY
                    else ChannelEventStatus.DEGRADED
                ),
                idempotency_key=f"connection-test:{uuid4()}",
                payload_summary={"health_status": health_status.value},
                error_code=connection_error_code
                or (None if health_status is ChannelHealthStatus.HEALTHY else health_status.value),
                degradations=(),
                external_message_id=None,
                occurred_at=checked_at,
            )
        )
        await self._record_health_snapshot(
            instance=stored,
            status=health_status,
            configured=(health_status is not ChannelHealthStatus.NOT_CONFIGURED),
            pending_update_count=0,
            remote_error_present=(health_status is ChannelHealthStatus.DEGRADED),
            sampled_at=checked_at,
        )
        return await self._view(stored)

    def simulate(
        self,
        *,
        platform: ChannelPlatform,
        blocks: tuple[MultimodalContentBlock, ...],
        request_streaming: bool,
        thread_id: str | None,
        edit_message_id: str | None,
        proactive: bool,
    ) -> ChannelSimulationResult:
        adapter = self._adapters.get(platform)
        negotiation = negotiate_capabilities(
            ChannelDeliveryCommand(
                channel_id=UUID(int=0),
                recipient_id="simulator",
                blocks=blocks,
                idempotency_key=f"simulation:{uuid4()}",
                request_streaming=request_streaming,
                thread_id=thread_id,
                edit_message_id=edit_message_id,
                proactive=proactive,
            ),
            adapter.capabilities,
        )
        return ChannelSimulationResult(
            platform=platform,
            blocks=negotiation.blocks,
            degradations=negotiation.degradations,
            buffered=negotiation.buffered,
            thread_preserved=thread_id is None or negotiation.thread_id is not None,
            edit_preserved=edit_message_id is None or negotiation.edit_message_id is not None,
        )

    async def deliver(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        recipient_id: str,
        blocks: tuple[MultimodalContentBlock, ...],
        idempotency_key: str,
        request_streaming: bool,
        thread_id: str | None,
        edit_message_id: str | None,
        proactive: bool,
    ) -> ChannelDeliveryReceipt:
        instance = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        normalized_key = self._text(idempotency_key, "发送幂等键", 255)
        existing = await self._repository.get_event_by_idempotency(
            tenant_id=tenant_id,
            channel_id=channel_id,
            direction=ChannelEventDirection.OUTBOUND,
            idempotency_key=normalized_key,
        )
        if existing is not None and existing.status in {
            ChannelEventStatus.DELIVERED,
            ChannelEventStatus.DEGRADED,
        }:
            if existing.external_message_id is None:
                raise ChannelConflictError("已完成渠道事件缺少消息标识")
            return ChannelDeliveryReceipt(
                status=existing.status,
                external_message_id=existing.external_message_id,
                degradations=existing.degradations,
                delivered_at=existing.occurred_at,
                idempotent_replay=True,
            )
        if existing is not None and existing.status is ChannelEventStatus.ACCEPTED:
            raise ChannelConflictError("相同幂等键的渠道发送正在处理中")
        if instance.status is ChannelInstanceStatus.DISABLED:
            raise ChannelConflictError("渠道实例已停用")
        command = ChannelDeliveryCommand(
            channel_id=channel_id,
            recipient_id=self._text(recipient_id, "收件人标识", 255),
            blocks=blocks,
            idempotency_key=normalized_key,
            request_streaming=request_streaming,
            thread_id=self._optional_text(thread_id, 255),
            edit_message_id=self._optional_text(edit_message_id, 255),
            proactive=proactive,
        )
        adapter = self._adapters.get(instance.platform)
        try:
            negotiation = negotiate_capabilities(command, adapter.capabilities)
        except ChannelAdapterError as error:
            await self._record_failed_delivery(instance, normalized_key, blocks, error)
            raise
        credential = await self._resolve_credential(instance)
        if instance.platform is not ChannelPlatform.WEB and credential is None:
            error = ChannelNotConfiguredError("渠道凭证尚未配置，未发送任何外部消息")
            await self._record_failed_delivery(instance, normalized_key, blocks, error)
            raise error
        reservation = self._event(
            instance=instance,
            direction=ChannelEventDirection.OUTBOUND,
            event_type="message.delivery_started",
            status=ChannelEventStatus.ACCEPTED,
            idempotency_key=normalized_key,
            payload_summary=summarize_blocks(negotiation.blocks),
            error_code=None,
            degradations=negotiation.degradations,
            external_message_id=None,
            occurred_at=datetime.now(UTC),
        )
        if not await self._repository.reserve_delivery(reservation):
            concurrent = await self._repository.get_event_by_idempotency(
                tenant_id=tenant_id,
                channel_id=channel_id,
                direction=ChannelEventDirection.OUTBOUND,
                idempotency_key=normalized_key,
            )
            if (
                concurrent is not None
                and concurrent.status in {ChannelEventStatus.DELIVERED, ChannelEventStatus.DEGRADED}
                and concurrent.external_message_id is not None
            ):
                return ChannelDeliveryReceipt(
                    status=concurrent.status,
                    external_message_id=concurrent.external_message_id,
                    degradations=concurrent.degradations,
                    delivered_at=concurrent.occurred_at,
                    idempotent_replay=True,
                )
            raise ChannelConflictError("相同幂等键的渠道发送正在处理中")
        allowed = await self._repository.reserve_rate_limit(
            tenant_id=tenant_id,
            channel_id=channel_id,
            limit=instance.rate_limit_per_minute,
            now=datetime.now(UTC),
        )
        if not allowed:
            error = ChannelRateLimitError()
            await self._record_failed_delivery(instance, normalized_key, blocks, error)
            raise error
        try:
            result = await adapter.deliver(
                command=command,
                negotiation=negotiation,
                credential=credential,
            )
        except ChannelAdapterError as error:
            await self._record_failed_delivery(instance, normalized_key, blocks, error)
            raise
        event = await self._repository.record_event(
            self._event(
                instance=instance,
                direction=ChannelEventDirection.OUTBOUND,
                event_type="message.delivered",
                status=result.status,
                idempotency_key=normalized_key,
                payload_summary=summarize_blocks(negotiation.blocks),
                error_code=None,
                degradations=result.degradations,
                external_message_id=result.external_message_id,
                occurred_at=result.delivered_at,
            )
        )
        return self._receipt(result, event)

    async def normalize_inbound(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        payload: Mapping[str, JsonValue],
    ) -> ChannelInboundEvent:
        instance = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        if instance.status is ChannelInstanceStatus.DISABLED:
            raise ChannelConflictError("渠道实例已停用")
        adapter = self._adapters.get(instance.platform)
        event = await adapter.normalize_inbound(payload)
        existing = await self._repository.get_event_by_idempotency(
            tenant_id=tenant_id,
            channel_id=channel_id,
            direction=ChannelEventDirection.INBOUND,
            idempotency_key=event.external_event_id,
        )
        if existing is None:
            await self._repository.record_event(
                self._event(
                    instance=instance,
                    direction=ChannelEventDirection.INBOUND,
                    event_type=event.event_type,
                    status=ChannelEventStatus.ACCEPTED,
                    idempotency_key=event.external_event_id,
                    external_event_id=event.external_event_id,
                    payload_summary=summarize_blocks(event.blocks),
                    error_code=None,
                    degradations=(),
                    external_message_id=None,
                    occurred_at=event.occurred_at,
                )
            )
        return event

    async def verify_telegram_webhook(
        self,
        *,
        channel_id: UUID,
        presented_secret: str | None,
    ) -> TelegramWebhookContext:
        """校验公开 Webhook 且只返回处理所需的隔离上下文。"""
        instance = await self._repository.get_instance_by_id(channel_id=channel_id)
        if (
            instance is None
            or instance.platform is not ChannelPlatform.TELEGRAM
            or instance.status is not ChannelInstanceStatus.ENABLED
            or presented_secret is None
        ):
            raise ChannelNotFoundError("Telegram Webhook 不可用")
        expected_secret = await self._secret_store.resolve_secret(
            _TELEGRAM_WEBHOOK_SECRET_KEY,
            tenant_id=instance.tenant_id,
            channel_id=instance.id,
        )
        if expected_secret is None or not compare_digest(expected_secret, presented_secret):
            raise ChannelNotFoundError("Telegram Webhook 不可用")
        return TelegramWebhookContext(
            tenant_id=instance.tenant_id,
            agent_id=instance.agent_id,
            channel_id=instance.id,
        )

    async def telegram_webhook_status(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
    ) -> TelegramWebhookStatus:
        """探测 Telegram Webhook，并将结果裁剪为可安全展示的状态摘要。"""
        instance = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        if instance.platform is not ChannelPlatform.TELEGRAM:
            raise ChannelNotFoundError(f"Telegram Webhook 不适用于渠道：{channel_id}")
        checked_at = datetime.now(UTC)
        if instance.status is ChannelInstanceStatus.DISABLED:
            result = TelegramWebhookStatus(
                channel_id=channel_id,
                status=ChannelHealthStatus.DISABLED,
                configured=False,
                pending_update_count=0,
                last_error_at=None,
                last_error_present=False,
                allowed_updates=(),
                checked_at=checked_at,
            )
            await self._record_health_snapshot(
                instance=instance,
                status=result.status,
                configured=result.configured,
                pending_update_count=result.pending_update_count,
                remote_error_present=result.last_error_present,
                sampled_at=result.checked_at,
            )
            return result
        credential = await self._resolve_credential(instance)
        if credential is None:
            result = TelegramWebhookStatus(
                channel_id=channel_id,
                status=ChannelHealthStatus.NOT_CONFIGURED,
                configured=False,
                pending_update_count=0,
                last_error_at=None,
                last_error_present=False,
                allowed_updates=(),
                checked_at=checked_at,
            )
            await self._record_health_snapshot(
                instance=instance,
                status=result.status,
                configured=result.configured,
                pending_update_count=result.pending_update_count,
                remote_error_present=result.last_error_present,
                sampled_at=result.checked_at,
            )
            return result
        info = await self._adapters.get(ChannelPlatform.TELEGRAM).get_webhook_info(
            credential=credential
        )
        result = self._webhook_status(channel_id, info)
        await self._record_health_snapshot(
            instance=instance,
            status=result.status,
            configured=result.configured,
            pending_update_count=result.pending_update_count,
            remote_error_present=result.last_error_present,
            sampled_at=result.checked_at,
        )
        return result

    async def register_telegram_webhook(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        webhook_url: str,
        drop_pending_updates: bool,
        actor_id: UUID,
        confirmed: bool,
    ) -> TelegramWebhookStatus:
        del actor_id
        if not confirmed:
            raise ChannelValidationError("注册 Telegram Webhook 必须明确确认")
        instance = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        self._ensure_telegram_webhook_instance(instance)
        credential = await self._resolve_credential(instance)
        if credential is None:
            raise ChannelNotConfiguredError("Telegram Bot Token 尚未配置")
        secret = await self._secret_store.resolve_secret(
            _TELEGRAM_WEBHOOK_SECRET_KEY,
            tenant_id=tenant_id,
            channel_id=channel_id,
        )
        if secret is None:
            raise ChannelNotConfiguredError("Telegram Webhook Secret 尚未配置")
        try:
            info = await self._adapters.get(ChannelPlatform.TELEGRAM).set_webhook(
                credential=credential,
                webhook_url=webhook_url,
                secret_token=secret,
                drop_pending_updates=drop_pending_updates,
            )
        except ChannelAdapterError as error:
            await self._record_webhook_event(
                instance,
                event_type="telegram.webhook.registration_failed",
                status=ChannelEventStatus.FAILED,
                error_code=error.code,
                info=None,
            )
            raise
        result = self._webhook_status(channel_id, info)
        await self._record_webhook_event(
            instance,
            event_type="telegram.webhook.registered",
            status=(
                ChannelEventStatus.DELIVERED
                if result.status is ChannelHealthStatus.HEALTHY
                else ChannelEventStatus.DEGRADED
            ),
            error_code=None,
            info=info,
        )
        return result

    async def clear_telegram_webhook(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        drop_pending_updates: bool,
        actor_id: UUID,
        confirmed: bool,
    ) -> TelegramWebhookStatus:
        del actor_id
        if not confirmed:
            raise ChannelValidationError("清理 Telegram Webhook 必须明确确认")
        instance = await self._required(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        self._ensure_telegram_webhook_instance(instance)
        credential = await self._resolve_credential(instance)
        if credential is None:
            raise ChannelNotConfiguredError("Telegram Bot Token 尚未配置")
        try:
            info = await self._adapters.get(ChannelPlatform.TELEGRAM).delete_webhook(
                credential=credential,
                drop_pending_updates=drop_pending_updates,
            )
        except ChannelAdapterError as error:
            await self._record_webhook_event(
                instance,
                event_type="telegram.webhook.clear_failed",
                status=ChannelEventStatus.FAILED,
                error_code=error.code,
                info=None,
            )
            raise
        result = self._webhook_status(channel_id, info)
        await self._record_webhook_event(
            instance,
            event_type="telegram.webhook.cleared",
            status=(
                ChannelEventStatus.DELIVERED
                if not result.configured
                else ChannelEventStatus.DEGRADED
            ),
            error_code=None,
            info=info,
        )
        return result

    async def events(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        event_type: str | None = None,
        limit: int,
    ) -> tuple[ChannelDiagnosticEvent, ...]:
        if not 1 <= limit <= 500:
            raise ChannelValidationError("诊断事件数量必须位于 1 到 500 之间")
        normalized_event_type = event_type.strip() if event_type is not None else None
        if normalized_event_type is not None and not normalized_event_type:
            raise ChannelValidationError("诊断事件类型不能为空")
        if normalized_event_type is not None and len(normalized_event_type) > 120:
            raise ChannelValidationError("诊断事件类型不能超过 120 个字符")
        if channel_id is not None:
            await self._required(
                tenant_id=tenant_id,
                agent_id=agent_id,
                channel_id=channel_id,
            )
        return await self._repository.list_events(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            event_type=normalized_event_type,
            limit=limit,
        )

    async def operation_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_minutes: int,
        now: datetime | None = None,
    ) -> tuple[ChannelOperationMetrics, ...]:
        """返回当前 Agent 渠道的安全运营聚合，不读取或返回事件正文。"""
        if not 5 <= window_minutes <= 1_440:
            raise ChannelValidationError("运营指标时间窗必须位于 5 到 1440 分钟之间")
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        started_at = ended_at - timedelta(minutes=window_minutes)
        if channel_id is not None:
            await self._required(
                tenant_id=tenant_id,
                agent_id=agent_id,
                channel_id=channel_id,
            )
        return await self._repository.get_operation_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            window_started_at=started_at,
            window_ended_at=ended_at,
        )

    async def error_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_minutes: int,
        now: datetime | None = None,
    ) -> tuple[ChannelErrorMetrics, ...]:
        if not 5 <= window_minutes <= 1_440:
            raise ChannelValidationError("错误指标时间窗必须位于 5 到 1440 分钟之间")
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        started_at = ended_at - timedelta(minutes=window_minutes)
        if channel_id is not None:
            await self._required(tenant_id=tenant_id, agent_id=agent_id, channel_id=channel_id)
        return await self._repository.get_error_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            window_started_at=started_at,
            window_ended_at=ended_at,
        )

    async def health_trend(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        window_minutes: int,
        limit: int = 500,
        now: datetime | None = None,
    ) -> tuple[ChannelHealthSnapshot, ...]:
        if not 5 <= window_minutes <= 10_080:
            raise ChannelValidationError("健康趋势时间窗必须位于 5 到 10080 分钟之间")
        if not 1 <= limit <= 2_000:
            raise ChannelValidationError("健康快照数量必须位于 1 到 2000 之间")
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        started_at = ended_at - timedelta(minutes=window_minutes)
        if channel_id is not None:
            await self._required(tenant_id=tenant_id, agent_id=agent_id, channel_id=channel_id)
        return await self._repository.list_health_snapshots(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            window_started_at=started_at,
            window_ended_at=ended_at,
            limit=limit,
        )

    async def alerts(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        values: Mapping[str, JsonValue],
        window_minutes: int,
        now: datetime | None = None,
    ) -> tuple[ChannelAlert, ...]:
        """按已发布策略生成去重的渠道活动告警，不读取事件正文。"""
        if not self._boolean(values["alerts.channel.enabled"], "alerts.channel.enabled"):
            return ()
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        enabled_codes = self._string_list(
            values["alerts.channel.enabled_error_codes"],
            "alerts.channel.enabled_error_codes",
        )
        minimum_severity = AlertSeverity(
            self._string(
                values["alerts.channel.minimum_severity"],
                "alerts.channel.minimum_severity",
            )
        )
        cooldown_minutes = self._integer(
            values["alerts.channel.cooldown_minutes"], "alerts.channel.cooldown_minutes"
        )
        failure_rate_threshold = self._number(
            values["alerts.channel.failure_rate_percent"],
            "alerts.channel.failure_rate_percent",
        )
        degraded_minutes = self._integer(
            values["alerts.channel.health_degraded_minutes"],
            "alerts.channel.health_degraded_minutes",
        )
        operation_metrics = await self.operation_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            window_minutes=window_minutes,
            now=ended_at,
        )
        error_metrics = await self.error_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            window_minutes=window_minutes,
            now=ended_at,
        )
        snapshots = await self.health_trend(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            window_minutes=window_minutes,
            limit=2_000,
            now=ended_at,
        )
        operations_by_channel = {item.channel_id: item for item in operation_metrics}
        alerts: list[ChannelAlert] = []
        for metric in error_metrics:
            operation = operations_by_channel.get(metric.channel_id)
            failure_rate = operation.outbound_failure_rate_percent if operation else 0.0
            if failure_rate < failure_rate_threshold or (
                "*" not in enabled_codes and metric.error_code not in enabled_codes
            ):
                continue
            severity = (
                AlertSeverity.CRITICAL
                if failure_rate >= min(100.0, failure_rate_threshold * 2)
                else AlertSeverity.WARNING
            )
            if not self._severity_enabled(severity, minimum_severity):
                continue
            alerts.append(
                ChannelAlert(
                    channel_id=metric.channel_id,
                    code="channel_error_rate",
                    error_code=metric.error_code,
                    severity=severity,
                    title="渠道出站失败率过高",
                    summary="当前窗口内该安全错误码对应的出站失败率达到告警阈值。",
                    occurrences=metric.occurrences,
                    current_value=failure_rate,
                    threshold_value=failure_rate_threshold,
                    unit="%",
                    first_occurred_at=metric.first_occurred_at,
                    last_occurred_at=metric.last_occurred_at,
                    cooldown_until=metric.last_occurred_at + timedelta(minutes=cooldown_minutes),
                )
            )
        snapshots_by_channel: dict[UUID, list[ChannelHealthSnapshot]] = {}
        for snapshot in snapshots:
            snapshots_by_channel.setdefault(snapshot.channel_id, []).append(snapshot)
        for snapshot_channel_id, channel_snapshots in snapshots_by_channel.items():
            newest = channel_snapshots[0]
            if newest.status is not ChannelHealthStatus.DEGRADED:
                continue
            consecutive: list[ChannelHealthSnapshot] = []
            for snapshot in channel_snapshots:
                if snapshot.status is not ChannelHealthStatus.DEGRADED:
                    break
                consecutive.append(snapshot)
            degraded_for = max(
                0.0,
                (ended_at - consecutive[-1].sampled_at).total_seconds() / 60,
            )
            if degraded_for < degraded_minutes:
                continue
            severity = (
                AlertSeverity.CRITICAL
                if degraded_for >= max(degraded_minutes * 2, degraded_minutes + 1)
                else AlertSeverity.WARNING
            )
            if not self._severity_enabled(severity, minimum_severity):
                continue
            alerts.append(
                ChannelAlert(
                    channel_id=snapshot_channel_id,
                    code="channel_health_degraded",
                    error_code=None,
                    severity=severity,
                    title="渠道健康持续降级",
                    summary="最近的连续安全健康快照均为降级状态。",
                    occurrences=len(consecutive),
                    current_value=round(degraded_for, 4),
                    threshold_value=float(degraded_minutes),
                    unit="minutes",
                    first_occurred_at=consecutive[-1].sampled_at,
                    last_occurred_at=newest.sampled_at,
                    cooldown_until=newest.sampled_at + timedelta(minutes=cooldown_minutes),
                )
            )
        dispositions = {
            item.alert_key: item
            for item in await self._repository.list_alert_dispositions(
                tenant_id=tenant_id, agent_id=agent_id
            )
            if item.expires_at is None or item.expires_at > ended_at
        }
        enriched: list[ChannelAlert] = []
        for alert in alerts:
            disposition = dispositions.get(alert.alert_key)
            enriched.append(
                replace(
                    alert,
                    disposition_status=(disposition.status if disposition else None),
                    disposition_reason=(disposition.reason if disposition else None),
                    disposition_expires_at=(disposition.expires_at if disposition else None),
                )
            )
        return tuple(
            sorted(
                enriched,
                key=lambda item: (item.last_occurred_at, str(item.channel_id), item.code),
                reverse=True,
            )
        )

    async def reconcile_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        values: Mapping[str, JsonValue],
        window_minutes: int,
        now: datetime | None = None,
    ) -> ChannelAlertLifecycleEvaluation:
        """评估并原子对账生命周期；升级仅在通知成功入队后另行确认。"""
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        alerts = await self.alerts(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=None,
            values=values,
            window_minutes=window_minutes,
            now=observed_at,
        )
        active = await self._repository.reconcile_alert_lifecycles(
            tenant_id=tenant_id,
            agent_id=agent_id,
            alerts=alerts,
            observed_at=observed_at,
        )
        escalation_enabled = self._boolean(
            values["alerts.notification.escalation_enabled"],
            "alerts.notification.escalation_enabled",
        )
        escalation_minutes = self._integer(
            values["alerts.notification.escalation_after_minutes"],
            "alerts.notification.escalation_after_minutes",
        )
        cutoff = observed_at - timedelta(minutes=escalation_minutes)
        due = (
            tuple(
                item
                for item in active
                if item.escalated_at is None and item.first_occurred_at <= cutoff
            )
            if escalation_enabled
            else ()
        )
        return ChannelAlertLifecycleEvaluation(
            alerts=alerts,
            active_lifecycles=active,
            due_escalations=due,
        )

    async def mark_alert_lifecycles_escalated(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        lifecycle_ids: tuple[UUID, ...],
        escalated_at: datetime,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        if not lifecycle_ids:
            return ()
        return await self._repository.mark_alert_lifecycles_escalated(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_ids=lifecycle_ids,
            escalated_at=escalated_at.astimezone(UTC),
        )

    async def alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ChannelAlertLifecycleStatus | None,
        severity: AlertSeverity | None,
        limit: int = 100,
    ) -> tuple[ChannelAlertLifecycle, ...]:
        if not 1 <= limit <= 500:
            raise ChannelValidationError("告警生命周期数量必须位于 1 到 500 之间")
        if channel_id is not None:
            await self._required(tenant_id=tenant_id, agent_id=agent_id, channel_id=channel_id)
        return await self._repository.list_alert_lifecycles(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            status=status,
            severity=severity,
            limit=limit,
        )

    async def alert_lifecycle_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_minutes: int,
        bucket_minutes: int,
        now: datetime | None = None,
    ) -> ChannelAlertLifecycleMetrics:
        if not 5 <= window_minutes <= 10_080:
            raise ChannelValidationError("生命周期统计窗口必须位于 5 到 10080 分钟之间")
        if not 5 <= bucket_minutes <= 1_440 or bucket_minutes > window_minutes:
            raise ChannelValidationError("生命周期趋势时间桶必须位于 5 分钟到统计窗口之间")
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        started_at = ended_at - timedelta(minutes=window_minutes)
        rows = await self._repository.list_alert_lifecycles_in_window(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_started_at=started_at,
            window_ended_at=ended_at,
        )
        bucket_seconds = bucket_minutes * 60
        bucket_count = (window_minutes + bucket_minutes - 1) // bucket_minutes
        buckets = [
            ChannelAlertLifecycleTrendPoint(
                bucket_started_at=started_at + timedelta(minutes=index * bucket_minutes),
                opened=0,
                resolved=0,
                escalated=0,
            )
            for index in range(bucket_count)
        ]

        def add_event(
            event_at: datetime | None,
            field: Literal["opened", "resolved", "escalated"],
        ) -> None:
            if event_at is None or not started_at <= event_at <= ended_at:
                return
            index = min(
                bucket_count - 1,
                int((event_at - started_at).total_seconds()) // bucket_seconds,
            )
            point = buckets[index]
            buckets[index] = replace(point, **{field: getattr(point, field) + 1})

        for row in rows:
            add_event(row.first_occurred_at, "opened")
            add_event(row.resolved_at, "resolved")
            add_event(row.escalated_at, "escalated")
        recovery = sorted(
            row.recovery_duration_seconds
            for row in rows
            if row.resolved_at is not None
            and started_at <= row.resolved_at <= ended_at
            and row.recovery_duration_seconds is not None
        )
        p95_index = max(0, (95 * len(recovery) + 99) // 100 - 1) if recovery else 0
        return ChannelAlertLifecycleMetrics(
            window_started_at=started_at,
            window_ended_at=ended_at,
            active=sum(item.status is ChannelAlertLifecycleStatus.ACTIVE for item in rows),
            opened=sum(started_at <= item.first_occurred_at <= ended_at for item in rows),
            resolved=sum(
                item.resolved_at is not None and started_at <= item.resolved_at <= ended_at
                for item in rows
            ),
            escalated=sum(
                item.escalated_at is not None and started_at <= item.escalated_at <= ended_at
                for item in rows
            ),
            mean_recovery_seconds=(sum(recovery) / len(recovery) if recovery else 0.0),
            p95_recovery_seconds=recovery[p95_index] if recovery else 0,
            trend=tuple(buckets),
        )

    async def set_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        alert_key: str,
        status: ChannelAlertDispositionStatus,
        reason: str,
        expires_at: datetime | None,
        actor_id: UUID,
        confirmed: bool,
        values: Mapping[str, JsonValue],
        window_minutes: int = 60,
    ) -> ChannelAlertDisposition:
        """确认或抑制当前告警，处置前重新校验稳定键和归属。"""
        if not confirmed:
            raise ChannelValidationError("告警处置必须明确确认")
        normalized_reason = self._text(reason, "处置原因", 500)
        if status is ChannelAlertDispositionStatus.SUPPRESSED:
            if expires_at is None:
                raise ChannelValidationError("抑制告警必须设置过期时间")
            expires_at = expires_at.astimezone(UTC)
            if expires_at <= datetime.now(UTC):
                raise ChannelValidationError("抑制过期时间必须晚于当前时间")
        alerts = await self.alerts(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            values=values,
            window_minutes=window_minutes,
        )
        target = next((item for item in alerts if item.alert_key == alert_key), None)
        if target is None:
            raise ChannelNotFoundError(f"活动告警不存在：{alert_key}")
        now = datetime.now(UTC)
        disposition = ChannelAlertDisposition(
            id=uuid4(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            alert_key=alert_key,
            code=target.code,
            error_code=target.error_code,
            status=status,
            reason=normalized_reason,
            actor_id=actor_id,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        existing = await self._repository.get_alert_disposition(
            tenant_id=tenant_id, agent_id=agent_id, alert_key=alert_key
        )
        if existing is not None:
            disposition = replace(disposition, id=existing.id, created_at=existing.created_at)
        return await self._repository.save_alert_disposition(disposition)

    async def get_alert_disposition(
        self, *, tenant_id: UUID, agent_id: UUID, alert_key: str
    ) -> ChannelAlertDisposition | None:
        """读取当前 Agent 的单条告警处置记录。"""
        return await self._repository.get_alert_disposition(
            tenant_id=tenant_id, agent_id=agent_id, alert_key=alert_key
        )

    async def clear_alert_disposition(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        alert_key: str,
        actor_id: UUID | None = None,
        confirmed: bool,
    ) -> None:
        if not confirmed:
            raise ChannelValidationError("解除告警处置必须明确确认")
        await self._repository.clear_alert_disposition(
            tenant_id=tenant_id,
            agent_id=agent_id,
            alert_key=alert_key,
            actor_id=actor_id,
        )

    @staticmethod
    def _webhook_status(channel_id: UUID, info: WebhookInfo) -> TelegramWebhookStatus:
        if not info.configured:
            status = ChannelHealthStatus.NOT_CONFIGURED
        elif info.last_error_present:
            status = ChannelHealthStatus.DEGRADED
        else:
            status = ChannelHealthStatus.HEALTHY
        return TelegramWebhookStatus(
            channel_id=channel_id,
            status=status,
            configured=info.configured,
            pending_update_count=info.pending_update_count,
            last_error_at=info.last_error_at,
            last_error_present=info.last_error_present,
            allowed_updates=info.allowed_updates,
            checked_at=info.checked_at,
        )

    @staticmethod
    def _ensure_telegram_webhook_instance(instance: ChannelInstance) -> None:
        if instance.platform is not ChannelPlatform.TELEGRAM:
            raise ChannelNotFoundError(f"Telegram Webhook 不适用于渠道：{instance.id}")
        if instance.status is ChannelInstanceStatus.DISABLED:
            raise ChannelConflictError("停用渠道不能管理 Telegram Webhook")

    async def _record_webhook_event(
        self,
        instance: ChannelInstance,
        *,
        event_type: str,
        status: ChannelEventStatus,
        error_code: str | None,
        info: WebhookInfo | None,
    ) -> None:
        summary: dict[str, JsonValue] = {}
        if info is not None:
            summary = {
                "configured": info.configured,
                "pending_update_count": info.pending_update_count,
                "last_error_present": info.last_error_present,
                "allowed_updates": list(info.allowed_updates),
            }
        await self._repository.record_event(
            self._event(
                instance=instance,
                direction=ChannelEventDirection.SYSTEM,
                event_type=event_type,
                status=status,
                idempotency_key=f"{event_type}:{uuid4()}",
                payload_summary=summary,
                error_code=error_code,
                degradations=(),
                external_message_id=None,
                occurred_at=datetime.now(UTC),
            )
        )

    async def _record_health_snapshot(
        self,
        *,
        instance: ChannelInstance,
        status: ChannelHealthStatus,
        configured: bool,
        pending_update_count: int,
        remote_error_present: bool,
        sampled_at: datetime,
    ) -> None:
        await self._repository.record_health_snapshot(
            ChannelHealthSnapshot(
                id=uuid4(),
                tenant_id=instance.tenant_id,
                agent_id=instance.agent_id,
                channel_id=instance.id,
                platform=instance.platform,
                status=status,
                configured=configured,
                pending_update_count=max(0, pending_update_count),
                remote_error_present=remote_error_present,
                sampled_at=sampled_at,
            )
        )

    @staticmethod
    def _boolean(value: object, key: str) -> bool:
        if not isinstance(value, bool):
            raise TypeError(f"生效配置 {key} 必须是布尔值")
        return value

    @staticmethod
    def _integer(value: object, key: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"生效配置 {key} 必须是整数")
        return value

    @staticmethod
    def _number(value: object, key: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"生效配置 {key} 必须是数值")
        return float(value)

    @staticmethod
    def _string(value: object, key: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"生效配置 {key} 必须是字符串")
        return value

    @staticmethod
    def _string_list(value: object, key: str) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise TypeError(f"生效配置 {key} 必须是字符串列表")
        items = cast(list[object], value)
        if not all(isinstance(item, str) for item in items):
            raise TypeError(f"生效配置 {key} 必须是字符串列表")
        return tuple(cast(str, item) for item in items)

    @staticmethod
    def _severity_enabled(severity: AlertSeverity, minimum: AlertSeverity) -> bool:
        order = {AlertSeverity.WARNING: 0, AlertSeverity.CRITICAL: 1}
        return order[severity] >= order[minimum]

    async def _required(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
    ) -> ChannelInstance:
        instance = await self._repository.get_instance(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        if instance is None:
            raise ChannelNotFoundError(f"渠道实例不存在：{channel_id}")
        return instance

    async def _view(self, instance: ChannelInstance) -> ChannelInstanceView:
        adapter = self._adapters.get(instance.platform)
        configured = (
            True
            if instance.platform is ChannelPlatform.WEB
            else await self._credential_configured(instance)
        )
        return ChannelInstanceView(
            instance=instance,
            display_name=adapter.display_name,
            implementation_status=adapter.implementation_status,
            credential_configured=configured,
            inbound_webhook_configured=await self._inbound_webhook_configured(instance),
            capabilities=adapter.capabilities,
        )

    async def _credential_configured(self, instance: ChannelInstance) -> bool:
        """仅检查不可逆元数据，管理视图不得为展示状态而解密凭证。"""
        key = _CREDENTIAL_KEYS.get(instance.platform)
        if key is None:
            return False
        metadata = await self._secret_store.list_metadata()
        return any(
            item.key == key
            and item.scope_type is ConfigScope.CHANNEL
            and item.scope_id == instance.id
            for item in metadata
        )

    async def _inbound_webhook_configured(self, instance: ChannelInstance) -> bool:
        if instance.platform is not ChannelPlatform.TELEGRAM:
            return False
        metadata = await self._secret_store.list_metadata()
        return any(
            item.key == _TELEGRAM_WEBHOOK_SECRET_KEY
            and item.scope_type is ConfigScope.CHANNEL
            and item.scope_id == instance.id
            for item in metadata
        )

    async def _resolve_credential(self, instance: ChannelInstance) -> str | None:
        key = _CREDENTIAL_KEYS.get(instance.platform)
        if key is None:
            return None
        return await self._secret_store.resolve_secret(
            key,
            tenant_id=instance.tenant_id,
            channel_id=instance.id,
        )

    async def _record_failed_delivery(
        self,
        instance: ChannelInstance,
        idempotency_key: str,
        blocks: tuple[MultimodalContentBlock, ...],
        error: ChannelAdapterError,
    ) -> None:
        await self._repository.record_event(
            self._event(
                instance=instance,
                direction=ChannelEventDirection.OUTBOUND,
                event_type="message.delivery_failed",
                status=(
                    ChannelEventStatus.RATE_LIMITED
                    if error.code in {"rate_limited", "telegram_rate_limited"}
                    else ChannelEventStatus.REJECTED
                    if isinstance(error, ChannelCapabilityError)
                    else ChannelEventStatus.FAILED
                ),
                idempotency_key=idempotency_key,
                payload_summary=summarize_blocks(blocks),
                error_code=error.code,
                degradations=(),
                external_message_id=None,
                occurred_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _receipt(
        result: AdapterDeliveryResult,
        event: ChannelDiagnosticEvent,
    ) -> ChannelDeliveryReceipt:
        return ChannelDeliveryReceipt(
            status=result.status,
            external_message_id=result.external_message_id,
            degradations=event.degradations,
            delivered_at=result.delivered_at,
            idempotent_replay=False,
        )

    @staticmethod
    def _event(
        *,
        instance: ChannelInstance,
        direction: ChannelEventDirection,
        event_type: str,
        status: ChannelEventStatus,
        idempotency_key: str,
        payload_summary: dict[str, JsonValue],
        error_code: str | None,
        degradations: tuple[str, ...],
        external_message_id: str | None,
        occurred_at: datetime,
        external_event_id: str | None = None,
    ) -> ChannelDiagnosticEvent:
        return ChannelDiagnosticEvent(
            id=uuid4(),
            tenant_id=instance.tenant_id,
            channel_id=instance.id,
            direction=direction,
            event_type=event_type,
            status=status,
            external_event_id=external_event_id,
            idempotency_key=idempotency_key,
            external_message_id=external_message_id,
            payload_summary=payload_summary,
            error_code=error_code,
            degradations=degradations,
            occurred_at=occurred_at,
        )

    @staticmethod
    def _settings(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        settings = dict(value)
        pending: list[Mapping[str, JsonValue]] = [settings]
        while pending:
            current = pending.pop()
            for key, item in current.items():
                if any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS):
                    raise ChannelValidationError(f"渠道公开设置禁止包含疑似密钥字段：{key}")
                if isinstance(item, dict):
                    pending.append(item)
                elif isinstance(item, list):
                    pending.extend(entry for entry in item if isinstance(entry, dict))
        return settings

    @staticmethod
    def _text(value: str, label: str, maximum: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ChannelValidationError(f"{label}不能为空")
        if len(normalized) > maximum:
            raise ChannelValidationError(f"{label}不能超过 {maximum} 个字符")
        return normalized

    @staticmethod
    def _optional_text(value: str | None, maximum: int) -> str | None:
        if value is None or not value.strip():
            return None
        return ChannelService._text(value, "标识", maximum)

    @staticmethod
    def _rate_limit(value: int) -> int:
        if not 1 <= value <= 10_000:
            raise ChannelValidationError("每分钟限流必须位于 1 到 10000 之间")
        return value

    def _validate_credential(self, platform: ChannelPlatform, credential: str) -> None:
        try:
            self._adapters.get(platform).validate_credential(credential)
        except ChannelAdapterError as error:
            raise ChannelValidationError(error.safe_message) from None
