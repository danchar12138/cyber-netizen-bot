"""渠道实例、凭证边界、能力协商、限流和安全诊断用例。"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol
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
    negotiate_capabilities,
    summarize_blocks,
)
from cnb_application.configuration_service import SecretStore
from cnb_domain import (
    ChannelCapabilities,
    ChannelDiagnosticEvent,
    ChannelEventDirection,
    ChannelEventStatus,
    ChannelHealthStatus,
    ChannelInstance,
    ChannelInstanceStatus,
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


class ChannelRepository(Protocol):
    """渠道实例、限流窗口和诊断事件持久化端口。"""

    async def create_instance(self, instance: ChannelInstance) -> ChannelInstance: ...

    async def get_instance(
        self,
        *,
        tenant_id: UUID,
        channel_id: UUID,
    ) -> ChannelInstance | None: ...

    async def list_instances(self, *, tenant_id: UUID) -> tuple[ChannelInstance, ...]: ...

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
        channel_id: UUID | None,
        limit: int,
    ) -> tuple[ChannelDiagnosticEvent, ...]: ...

    async def reserve_rate_limit(
        self,
        *,
        tenant_id: UUID,
        channel_id: UUID,
        limit: int,
        now: datetime,
    ) -> bool: ...


_CREDENTIAL_KEYS = {
    ChannelPlatform.FEISHU: "channel.feishu.bot_credential",
    ChannelPlatform.DISCORD: "channel.discord.bot_token",
    ChannelPlatform.TELEGRAM: "channel.telegram.bot_token",
}
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
                implementation_status=(
                    "ready" if adapter.platform is ChannelPlatform.WEB else "placeholder"
                ),
                credential_required=adapter.platform is not ChannelPlatform.WEB,
                capabilities=adapter.capabilities,
            )
            for adapter in self._adapters.all()
        )

    async def create(
        self,
        *,
        tenant_id: UUID,
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
        instance = ChannelInstance(
            id=uuid4(),
            tenant_id=tenant_id,
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
                channel_id=stored.id,
                plaintext=credential,
                actor_id=actor_id,
            )
        return await self._view(stored)

    async def get(self, *, tenant_id: UUID, channel_id: UUID) -> ChannelInstanceView:
        instance = await self._required(tenant_id=tenant_id, channel_id=channel_id)
        return await self._view(instance)

    async def list(self, *, tenant_id: UUID) -> tuple[ChannelInstanceView, ...]:
        instances = await self._repository.list_instances(tenant_id=tenant_id)
        return tuple([await self._view(instance) for instance in instances])

    async def update(
        self,
        *,
        tenant_id: UUID,
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
        current = await self._required(tenant_id=tenant_id, channel_id=channel_id)
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
        channel_id: UUID,
        plaintext: str,
        actor_id: UUID,
    ) -> ChannelInstanceView:
        instance = await self._required(tenant_id=tenant_id, channel_id=channel_id)
        if instance.platform is ChannelPlatform.WEB:
            raise ChannelConflictError("内部 Web Adapter 不使用外部凭证")
        if not plaintext:
            raise ChannelValidationError("渠道凭证不能为空")
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
        channel_id: UUID,
        actor_id: UUID,
        confirmed: bool,
    ) -> ChannelInstanceView:
        if not confirmed:
            raise ChannelValidationError("清除渠道凭证必须明确确认")
        instance = await self._required(tenant_id=tenant_id, channel_id=channel_id)
        key = _CREDENTIAL_KEYS.get(instance.platform)
        if key is None:
            raise ChannelConflictError("内部 Web Adapter 没有可清除凭证")
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
        channel_id: UUID,
        actor_id: UUID,
    ) -> ChannelInstanceView:
        instance = await self._required(tenant_id=tenant_id, channel_id=channel_id)
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
                health = await self._adapters.get(instance.platform).test_connection(
                    credential=credential
                )
                health_status = health.status
                detail = health.detail
                checked_at = health.checked_at
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
                error_code=None
                if health_status is ChannelHealthStatus.HEALTHY
                else health_status.value,
                degradations=(),
                external_message_id=None,
                occurred_at=checked_at,
            )
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
        channel_id: UUID,
        recipient_id: str,
        blocks: tuple[MultimodalContentBlock, ...],
        idempotency_key: str,
        request_streaming: bool,
        thread_id: str | None,
        edit_message_id: str | None,
        proactive: bool,
    ) -> ChannelDeliveryReceipt:
        instance = await self._required(tenant_id=tenant_id, channel_id=channel_id)
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
            credential = await self._resolve_credential(instance)
            if instance.platform is not ChannelPlatform.WEB and credential is None:
                raise ChannelNotConfiguredError("渠道凭证尚未配置，未发送任何外部消息")
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
        channel_id: UUID,
        payload: Mapping[str, JsonValue],
    ) -> ChannelInboundEvent:
        instance = await self._required(tenant_id=tenant_id, channel_id=channel_id)
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

    async def events(
        self,
        *,
        tenant_id: UUID,
        channel_id: UUID | None,
        limit: int,
    ) -> tuple[ChannelDiagnosticEvent, ...]:
        if not 1 <= limit <= 500:
            raise ChannelValidationError("诊断事件数量必须位于 1 到 500 之间")
        if channel_id is not None:
            await self._required(tenant_id=tenant_id, channel_id=channel_id)
        return await self._repository.list_events(
            tenant_id=tenant_id,
            channel_id=channel_id,
            limit=limit,
        )

    async def _required(self, *, tenant_id: UUID, channel_id: UUID) -> ChannelInstance:
        instance = await self._repository.get_instance(
            tenant_id=tenant_id,
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
            implementation_status=(
                "ready" if instance.platform is ChannelPlatform.WEB else "placeholder"
            ),
            credential_configured=configured,
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
                    if isinstance(error, ChannelRateLimitError)
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
