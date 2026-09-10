"""外部身份、线程路由和可重放入站 Inbox 的应用用例。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from cnb_adapters import ChannelInboundEvent
from cnb_application.channel_service import ChannelRepository
from cnb_application.configuration_service import ConfigurationService
from cnb_application.conversation_service import ConversationRepository
from cnb_application.task_service import (
    BackgroundTaskService,
    EnqueueResult,
    PermanentTaskError,
    TaskConflictError,
    TaskNotFoundError,
    TaskValidationError,
)
from cnb_domain import (
    INBOUND_ENVELOPE_SCHEMA_VERSION,
    BackgroundJob,
    BackgroundJobKind,
    ChannelInstance,
    ChannelInstanceStatus,
    ContentBlockKind,
    ExternalConversationKind,
    ExternalConversationMapping,
    ExternalIdentityMapping,
    ExternalMappingStatus,
    InboundEnvelope,
    InboundVerification,
    InboxEvent,
    InboxEventStatus,
    JsonValue,
    MultimodalContentBlock,
)


class InboundValidationError(ValueError):
    """入站契约、映射命令或验证结果不满足稳定边界。"""


class InboundNotFoundError(LookupError):
    """目标资源不在当前租户与 Agent 双重作用域内。"""


class InboundConflictError(RuntimeError):
    """映射唯一性、启停状态或重放状态发生冲突。"""


@dataclass(frozen=True, slots=True)
class InboundAcceptance:
    """入站幂等落库结果。"""

    inbox: InboxEvent
    job: BackgroundJob
    created: bool


class InboundGatewayRepository(Protocol):
    """外部映射持久化端口；Inbox 事务仍由可靠任务仓储负责。"""

    async def user_exists(self, *, tenant_id: UUID, user_id: UUID) -> bool: ...

    async def create_identity_mapping(
        self, mapping: ExternalIdentityMapping
    ) -> ExternalIdentityMapping: ...

    async def get_identity_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
    ) -> ExternalIdentityMapping | None: ...

    async def find_identity_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        external_subject_id: str,
        enabled_only: bool,
    ) -> ExternalIdentityMapping | None: ...

    async def list_identity_mappings(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ExternalMappingStatus | None,
        limit: int,
    ) -> tuple[ExternalIdentityMapping, ...]: ...

    async def update_identity_mapping_status(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
        status: ExternalMappingStatus,
        actor_id: UUID,
        updated_at: datetime,
    ) -> ExternalIdentityMapping | None: ...

    async def create_conversation_mapping(
        self, mapping: ExternalConversationMapping
    ) -> ExternalConversationMapping: ...

    async def get_conversation_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
    ) -> ExternalConversationMapping | None: ...

    async def find_conversation_mapping(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        external_conversation_id: str,
        external_thread_id: str | None,
        enabled_only: bool,
    ) -> ExternalConversationMapping | None: ...

    async def list_conversation_mappings(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ExternalMappingStatus | None,
        limit: int,
    ) -> tuple[ExternalConversationMapping, ...]: ...

    async def update_conversation_mapping_status(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
        status: ExternalMappingStatus,
        actor_id: UUID,
        updated_at: datetime,
    ) -> ExternalConversationMapping | None: ...


class InboundGatewayService:
    """在任何 Agent 执行前解析显式映射并原子写入可靠 Inbox。"""

    def __init__(
        self,
        *,
        repository: InboundGatewayRepository,
        channel_repository: ChannelRepository,
        conversation_repository: ConversationRepository,
        task_service: BackgroundTaskService,
        configuration_service: ConfigurationService,
    ) -> None:
        self._repository = repository
        self._channel_repository = channel_repository
        self._conversation_repository = conversation_repository
        self._task_service = task_service
        self._configuration_service = configuration_service

    async def bind_identity(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        external_subject_id: str,
        user_id: UUID,
        actor_id: UUID,
    ) -> ExternalIdentityMapping:
        channel = await self._required_channel(tenant_id, agent_id, channel_id)
        if not await self._repository.user_exists(tenant_id=tenant_id, user_id=user_id):
            raise InboundNotFoundError("本地用户不存在")
        now = self._aware_now()
        mapping = ExternalIdentityMapping(
            id=uuid4(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            platform=channel.platform,
            external_subject_id=self._external_id(external_subject_id, "外部主体 ID"),
            user_id=user_id,
            status=ExternalMappingStatus.ENABLED,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        try:
            return await self._repository.create_identity_mapping(mapping)
        except ValueError as error:
            raise InboundConflictError(str(error)) from error

    async def list_identities(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ExternalMappingStatus | None,
        limit: int,
    ) -> tuple[ExternalIdentityMapping, ...]:
        return await self._repository.list_identity_mappings(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            status=status,
            limit=self._limit(limit),
        )

    async def set_identity_status(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
        status: ExternalMappingStatus,
        actor_id: UUID,
        confirmed: bool,
    ) -> ExternalIdentityMapping:
        if not confirmed:
            raise InboundValidationError("身份映射状态变更必须明确确认")
        item = await self._repository.update_identity_mapping_status(
            tenant_id=tenant_id,
            agent_id=agent_id,
            mapping_id=mapping_id,
            status=status,
            actor_id=actor_id,
            updated_at=self._aware_now(),
        )
        if item is None:
            raise InboundNotFoundError("身份映射不存在")
        return item

    async def bind_conversation(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        user_id: UUID,
        kind: ExternalConversationKind,
        external_conversation_id: str,
        external_thread_id: str | None,
        conversation_id: UUID,
        actor_id: UUID,
    ) -> ExternalConversationMapping:
        channel = await self._required_channel(tenant_id, agent_id, channel_id)
        if not await self._repository.user_exists(tenant_id=tenant_id, user_id=user_id):
            raise InboundNotFoundError("本地用户不存在")
        identity = await self._repository.list_identity_mappings(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            status=ExternalMappingStatus.ENABLED,
            limit=500,
        )
        if not any(item.user_id == user_id for item in identity):
            raise InboundConflictError("绑定会话前必须存在同渠道的已启用外部身份映射")
        conversation = await self._conversation_repository.get_conversation_for_user(
            conversation_id,
            user_id,
            agent_id,
        )
        if conversation is None or conversation.tenant_id != tenant_id:
            raise InboundNotFoundError("内部会话不存在")
        now = self._aware_now()
        mapping = ExternalConversationMapping(
            id=uuid4(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            platform=channel.platform,
            kind=kind,
            external_conversation_id=self._external_id(external_conversation_id, "外部会话 ID"),
            external_thread_id=self._optional_external_id(external_thread_id, "外部线程 ID"),
            conversation_id=conversation_id,
            status=ExternalMappingStatus.ENABLED,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        try:
            return await self._repository.create_conversation_mapping(mapping)
        except ValueError as error:
            raise InboundConflictError(str(error)) from error

    async def list_conversations(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID | None,
        status: ExternalMappingStatus | None,
        limit: int,
    ) -> tuple[ExternalConversationMapping, ...]:
        return await self._repository.list_conversation_mappings(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            status=status,
            limit=self._limit(limit),
        )

    async def set_conversation_status(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        mapping_id: UUID,
        status: ExternalMappingStatus,
        actor_id: UUID,
        confirmed: bool,
    ) -> ExternalConversationMapping:
        if not confirmed:
            raise InboundValidationError("会话映射状态变更必须明确确认")
        item = await self._repository.update_conversation_mapping_status(
            tenant_id=tenant_id,
            agent_id=agent_id,
            mapping_id=mapping_id,
            status=status,
            actor_id=actor_id,
            updated_at=self._aware_now(),
        )
        if item is None:
            raise InboundNotFoundError("会话映射不存在")
        return item

    async def accept_normalized(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        channel_id: UUID,
        event: ChannelInboundEvent,
        verification: InboundVerification,
        created_by: UUID | None,
    ) -> InboundAcceptance:
        """校验可信边界，解析映射并只持久化净化后的标准 Envelope。"""
        channel = await self._required_channel(tenant_id, agent_id, channel_id)
        if channel.status is not ChannelInstanceStatus.ENABLED:
            raise InboundConflictError("渠道实例已停用")
        if not verification.signature_valid:
            raise InboundValidationError("平台签名验证失败")
        self._aware(verification.received_at, "接收时间")
        self._aware(event.occurred_at, "事件时间")
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        maximum_bytes = self._integer_setting(configuration.values, "inbox.max_payload_bytes")
        if not 0 < verification.payload_size_bytes <= maximum_bytes:
            raise InboundValidationError("入站事件大小超出配置边界")
        maximum_age = self._integer_setting(configuration.values, "inbox.max_event_age_seconds")
        age_seconds = abs((verification.received_at - event.occurred_at).total_seconds())
        if age_seconds > maximum_age:
            raise InboundValidationError("入站事件已过期或时间偏移过大")

        subject_id = self._external_id(event.sender_external_id, "外部主体 ID")
        external_conversation_id = self._external_id(event.conversation_external_id, "外部会话 ID")
        external_thread_id = self._optional_external_id(event.thread_external_id, "外部线程 ID")
        external_event_id = self._external_id(event.external_event_id, "外部事件 ID")
        external_message_id = self._external_id(
            event.message_external_id or event.external_event_id,
            "外部消息 ID",
        )
        identity = await self._repository.find_identity_mapping(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            external_subject_id=subject_id,
            enabled_only=True,
        )
        if identity is None:
            raise InboundNotFoundError("外部身份映射不存在")
        conversation = await self._repository.find_conversation_mapping(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            external_conversation_id=external_conversation_id,
            external_thread_id=external_thread_id,
            enabled_only=True,
        )
        if conversation is None:
            raise InboundNotFoundError("外部会话或线程映射不存在")
        if conversation.kind is not event.conversation_kind:
            raise InboundConflictError("入站会话类型与显式映射不一致")
        local_conversation = await self._conversation_repository.get_conversation_for_user(
            conversation.conversation_id,
            identity.user_id,
            agent_id,
        )
        if local_conversation is None or local_conversation.tenant_id != tenant_id:
            raise InboundNotFoundError("外部主体无权访问映射的内部会话")
        if not event.blocks:
            raise InboundValidationError("标准化入站消息至少需要一个内容块")

        envelope = InboundEnvelope(
            schema_version=INBOUND_ENVELOPE_SCHEMA_VERSION,
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            platform=channel.platform,
            external_event_id=external_event_id,
            external_subject_id=subject_id,
            user_id=identity.user_id,
            conversation_kind=conversation.kind,
            external_conversation_id=external_conversation_id,
            external_thread_id=external_thread_id,
            conversation_id=conversation.conversation_id,
            external_message_id=external_message_id,
            blocks=self._blocks(event.blocks),
            occurred_at=event.occurred_at,
            received_at=verification.received_at,
        )
        job_id = uuid4()
        message_digest = self._digest(external_message_id)
        inbox = InboxEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            event_key=f"im:{channel_id}:{message_digest}",
            event_type=self._text(event.event_type, "事件类型", 120),
            payload=self._envelope_payload(envelope),
            status=InboxEventStatus.PENDING,
            job_id=job_id,
            received_at=verification.received_at,
            processed_at=None,
            last_error_code=None,
            agent_id=agent_id,
            channel_id=channel_id,
            schema_version=envelope.schema_version,
            platform=envelope.platform.value,
            external_event_digest=self._digest(external_event_id),
            external_subject_digest=self._digest(subject_id),
            external_conversation_digest=self._digest(external_conversation_id),
            external_thread_digest=(
                self._digest(external_thread_id) if external_thread_id is not None else None
            ),
            external_message_digest=message_digest,
            user_id=identity.user_id,
            conversation_id=conversation.conversation_id,
            content_kinds=tuple(block.kind.value for block in envelope.blocks),
            content_block_count=len(envelope.blocks),
        )
        result = await self._task_service.enqueue(
            tenant_id=tenant_id,
            kind=BackgroundJobKind.INBOUND_MESSAGE,
            payload=inbox.payload,
            deduplication_key=inbox.event_key,
            created_by=created_by,
            max_attempts=self._integer_setting(configuration.values, "inbox.max_attempts"),
            lease_seconds=self._integer_setting(configuration.values, "tasks.lease_seconds"),
            retry_base_seconds=self._integer_setting(
                configuration.values, "tasks.retry_base_seconds"
            ),
            correlation_id=inbox.external_event_digest,
            source_inbox=inbox,
            job_id=job_id,
        )
        stored_inbox = await self._stored_inbox(result, inbox)
        return InboundAcceptance(inbox=stored_inbox, job=result.job, created=result.created)

    async def list_inbox(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: InboxEventStatus | None,
        channel_id: UUID | None,
        limit: int,
    ) -> tuple[InboxEvent, ...]:
        return await self._task_service.list_inbox(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=status,
            channel_id=channel_id,
            limit=self._limit(limit),
        )

    async def replay_inbox(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        inbox_id: UUID,
        actor_id: UUID,
        confirmed: bool,
        reason: str,
    ) -> BackgroundJob:
        try:
            inbox = await self._task_service.get_inbox(
                tenant_id=tenant_id,
                agent_id=agent_id,
                inbox_id=inbox_id,
            )
        except TaskNotFoundError as error:
            raise InboundNotFoundError(str(error)) from error
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=inbox.channel_id,
            user_id=inbox.user_id,
        )
        replay_enabled = configuration.values.get("inbox.replay_enabled")
        if replay_enabled is not True:
            raise InboundConflictError("当前生效配置已禁用 Inbox 重放")
        try:
            return await self._task_service.replay(
                tenant_id=tenant_id,
                job_id=inbox.job_id,
                actor_id=actor_id,
                confirmed=confirmed,
                reason=reason,
            )
        except TaskNotFoundError as error:
            raise InboundNotFoundError(str(error)) from error
        except (TaskValidationError, TaskConflictError) as error:
            raise InboundConflictError(str(error)) from error

    async def _required_channel(
        self, tenant_id: UUID, agent_id: UUID, channel_id: UUID
    ) -> ChannelInstance:
        channel = await self._channel_repository.get_instance(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        if channel is None:
            raise InboundNotFoundError("渠道实例不存在")
        return channel

    async def _stored_inbox(self, result: EnqueueResult, proposed: InboxEvent) -> InboxEvent:
        inbox_id = result.job.source_inbox_id
        if result.created:
            return proposed
        if inbox_id is None:
            raise InboundConflictError("重复入站任务缺少 Inbox 来源")
        return await self._task_service.get_inbox_by_id(
            tenant_id=result.job.tenant_id,
            inbox_id=inbox_id,
        )

    @staticmethod
    def _envelope_payload(envelope: InboundEnvelope) -> dict[str, JsonValue]:
        return {
            "schema_version": envelope.schema_version,
            "agent_id": str(envelope.agent_id),
            "channel_id": str(envelope.channel_id),
            "platform": envelope.platform.value,
            "external_event_id": envelope.external_event_id,
            "external_subject_id": envelope.external_subject_id,
            "user_id": str(envelope.user_id),
            "conversation_kind": envelope.conversation_kind.value,
            "external_conversation_id": envelope.external_conversation_id,
            "external_thread_id": envelope.external_thread_id,
            "conversation_id": str(envelope.conversation_id),
            "external_message_id": envelope.external_message_id,
            "blocks": [InboundGatewayService._block_payload(block) for block in envelope.blocks],
            "occurred_at": envelope.occurred_at.isoformat(),
            "received_at": envelope.received_at.isoformat(),
        }

    @staticmethod
    def _block_payload(block: MultimodalContentBlock) -> dict[str, JsonValue]:
        return {
            "kind": block.kind.value,
            "text": block.text,
            "attachment_id": str(block.attachment_id) if block.attachment_id else None,
            "content_type": block.content_type,
            "file_name": block.file_name,
            "size_bytes": block.size_bytes,
            "sha256": block.sha256,
            "alt_text": block.alt_text,
        }

    @staticmethod
    def _blocks(blocks: Sequence[MultimodalContentBlock]) -> tuple[MultimodalContentBlock, ...]:
        normalized: list[MultimodalContentBlock] = []
        for block in blocks:
            if block.kind in {ContentBlockKind.TEXT, ContentBlockKind.MARKDOWN}:
                text = (block.text or "").strip()
                if not text:
                    raise InboundValidationError("文本内容块不能为空")
                normalized.append(replace(block, text=text))
            elif block.attachment_id is None:
                raise InboundValidationError("附件内容块必须引用已校验的本地附件")
            else:
                normalized.append(block)
        return tuple(normalized)

    @staticmethod
    def _external_id(value: str, label: str) -> str:
        return InboundGatewayService._text(value, label, 255)

    @staticmethod
    def _optional_external_id(value: str | None, label: str) -> str | None:
        if value is None or not value.strip():
            return None
        return InboundGatewayService._external_id(value, label)

    @staticmethod
    def _text(value: str, label: str, maximum: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise InboundValidationError(f"{label}不能为空")
        if len(normalized) > maximum:
            raise InboundValidationError(f"{label}不能超过 {maximum} 个字符")
        if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
            raise InboundValidationError(f"{label}不能包含控制字符")
        return normalized

    @staticmethod
    def _digest(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _limit(value: int) -> int:
        if not 1 <= value <= 500:
            raise InboundValidationError("查询数量必须位于 1 到 500 之间")
        return value

    @staticmethod
    def _aware(value: datetime, label: str) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InboundValidationError(f"{label}必须包含时区")

    @staticmethod
    def _aware_now() -> datetime:
        from datetime import UTC

        return datetime.now(UTC)

    @staticmethod
    def _integer_setting(values: Mapping[str, JsonValue], key: str) -> int:
        value = values.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise InboundConflictError(f"生效配置中的整数参数无效：{key}")
        return value


class InboundMessageTaskHandler:
    """验证已净化 Envelope 并冻结后续 Agent 消费的稳定路由结果。"""

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        if job.payload.get("schema_version") != INBOUND_ENVELOPE_SCHEMA_VERSION:
            raise PermanentTaskError("Inbox Envelope schema version 不受支持")
        required = ("agent_id", "channel_id", "user_id", "conversation_id")
        result: dict[str, JsonValue] = {
            "schema_version": INBOUND_ENVELOPE_SCHEMA_VERSION,
            "routing_status": "ready_for_agent",
        }
        for key in required:
            value = job.payload.get(key)
            if not isinstance(value, str):
                raise PermanentTaskError(f"Inbox Envelope 缺少路由字段：{key}")
            try:
                parsed = UUID(value)
            except ValueError as error:
                raise PermanentTaskError(f"Inbox Envelope 路由字段无效：{key}") from error
            result[key] = str(parsed)
        blocks = job.payload.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            raise PermanentTaskError("Inbox Envelope 缺少内容块")
        result["content_block_count"] = len(blocks)
        return result


__all__ = [
    "InboundAcceptance",
    "InboundConflictError",
    "InboundGatewayRepository",
    "InboundGatewayService",
    "InboundMessageTaskHandler",
    "InboundNotFoundError",
    "InboundValidationError",
]
