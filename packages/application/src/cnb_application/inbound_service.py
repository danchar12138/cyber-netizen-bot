"""外部身份、线程路由和可重放入站 Inbox 的应用用例。"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest
from typing import Protocol, cast, overload
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from cnb_adapters import ChannelInboundEvent
from cnb_application.attachment_service import (
    AttachmentConflictError,
    AttachmentNotFoundError,
    AttachmentService,
    AttachmentValidationError,
)
from cnb_application.channel_service import ChannelDeliveryReceipt, ChannelRepository
from cnb_application.configuration_service import ConfigurationService
from cnb_application.conversation_service import (
    ConversationConflictError,
    ConversationNotFoundError,
    ConversationRepository,
    ConversationService,
)
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
    AgentRunStatus,
    Attachment,
    BackgroundJob,
    BackgroundJobKind,
    ChannelInstance,
    ChannelInstanceStatus,
    ChannelPlatform,
    ContentBlockKind,
    DevelopmentIdentity,
    ExternalConversationKind,
    ExternalConversationMapping,
    ExternalIdentityMapping,
    ExternalMappingStatus,
    InboundEnvelope,
    InboundVerification,
    InboxEvent,
    InboxEventStatus,
    JsonValue,
    Message,
    MessageStatus,
    MultimodalContentBlock,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


@dataclass(frozen=True, slots=True)
class InboundProcessingResult:
    """不含正文和附件元数据的入站对话处理摘要。"""

    message_id: UUID
    run_id: UUID
    run_status: AgentRunStatus
    idempotent_replay: bool
    execution_status: str
    reply_delivery: ChannelDeliveryReceipt | None = None


class InboundConversationServiceFactory(Protocol):
    """按 Envelope 身份和渠道构建现有对话应用服务。"""

    def __call__(
        self,
        *,
        identity: DevelopmentIdentity,
        channel_id: UUID,
    ) -> ConversationService: ...


class InboundAttachmentServiceFactory(Protocol):
    """按 Envelope 身份构建现有附件生命周期服务。"""

    def __call__(self, *, identity: DevelopmentIdentity) -> AttachmentService: ...


class InboundReplyDispatcher(Protocol):
    """将已完成的外部入站回复投递回原渠道。"""

    async def dispatch(
        self,
        *,
        envelope: InboundEnvelope,
        response: Message,
        idempotency_key: str,
    ) -> ChannelDeliveryReceipt: ...


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


class InboundConversationProcessor:
    """将可信 Envelope 接入既有 Conversation 与 Agent Run 纵向链路。"""

    def __init__(
        self,
        *,
        conversation_services: InboundConversationServiceFactory,
        attachment_services: InboundAttachmentServiceFactory,
        reply_dispatcher: InboundReplyDispatcher | None = None,
    ) -> None:
        self._conversation_services = conversation_services
        self._attachment_services = attachment_services
        self._reply_dispatcher = reply_dispatcher

    async def process(self, envelope: InboundEnvelope) -> InboundProcessingResult:
        identity = DevelopmentIdentity(
            tenant_id=envelope.tenant_id,
            user_id=envelope.user_id,
            agent_id=envelope.agent_id,
            user_name="外部渠道用户",
            agent_name="渠道 Agent",
        )
        client_message_id = self.client_message_id(envelope)
        conversation_service = self._conversation_services(
            identity=identity,
            channel_id=envelope.channel_id,
        )
        attachment_service = self._attachment_services(identity=identity)
        attachment_ids = tuple(
            block.attachment_id for block in envelope.blocks if block.attachment_id is not None
        )
        try:
            attachments = await attachment_service.prepare_message_attachments(
                conversation_id=envelope.conversation_id,
                client_message_id=client_message_id,
                attachment_ids=attachment_ids,
            )
            content_blocks = self._authoritative_blocks(envelope.blocks, attachments)
            pending = await conversation_service.send_message(
                envelope.conversation_id,
                client_message_id=client_message_id,
                content=self._content_projection(content_blocks),
                attachments=attachments,
                content_blocks=content_blocks,
            )
            await attachment_service.attach_to_message(
                attachment_ids=attachment_ids,
                message=pending.trigger_message,
            )
        except (
            AttachmentConflictError,
            AttachmentNotFoundError,
            AttachmentValidationError,
            ConversationConflictError,
            ConversationNotFoundError,
        ) as error:
            raise PermanentTaskError("Inbox Envelope 引用的会话或附件无效") from error

        if not pending.created and pending.run.status is AgentRunStatus.RUNNING:
            await conversation_service.fail_interrupted_run(pending)
            raise PermanentTaskError("检测到中断的 Agent Run，已停止自动续跑")

        if pending.run.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.CANCELLED,
            AgentRunStatus.FAILED,
        }:
            delivery = await self._deliver_reply(
                envelope=envelope,
                conversation_service=conversation_service,
                run_status=pending.run.status,
                run_id=pending.run.id,
                response_message_id=pending.run.response_message_id,
            )
            return InboundProcessingResult(
                message_id=pending.trigger_message.id,
                run_id=pending.run.id,
                run_status=pending.run.status,
                idempotent_replay=True,
                execution_status="already_terminal",
                reply_delivery=delivery,
            )

        completed = await conversation_service.execute_run(
            pending,
            resume_queued=not pending.created,
        )
        delivery = await self._deliver_reply(
            envelope=envelope,
            conversation_service=conversation_service,
            run_status=completed.status,
            run_id=completed.id,
            response_message_id=completed.response_message_id,
        )
        return InboundProcessingResult(
            message_id=pending.trigger_message.id,
            run_id=pending.run.id,
            run_status=completed.status,
            idempotent_replay=not pending.created,
            execution_status=(
                "claimed_elsewhere"
                if completed.status is AgentRunStatus.QUEUED
                else "agent_run_processed"
            ),
            reply_delivery=delivery,
        )

    async def _deliver_reply(
        self,
        *,
        envelope: InboundEnvelope,
        conversation_service: ConversationService,
        run_status: AgentRunStatus,
        run_id: UUID,
        response_message_id: UUID,
    ) -> ChannelDeliveryReceipt | None:
        """仅发送成功且有正文的 Telegram 回复，所有其他情况保持静默。"""
        if self._reply_dispatcher is None or envelope.platform is not ChannelPlatform.TELEGRAM:
            return None
        if run_status is not AgentRunStatus.COMPLETED:
            return None
        response = await conversation_service.get_response_message_for_run(run_id)
        if response.id != response_message_id or response.status is not MessageStatus.COMPLETED:
            return None
        if not response.content.strip():
            return None
        return await self._reply_dispatcher.dispatch(
            envelope=envelope,
            response=response,
            idempotency_key=(
                f"inbound-reply:{envelope.tenant_id}:{envelope.channel_id}:"
                f"{envelope.external_message_id}"
            ),
        )

    @staticmethod
    def client_message_id(envelope: InboundEnvelope) -> UUID:
        """为所有平台重试和 Worker 重投生成同一个消息幂等键。"""
        return uuid5(
            NAMESPACE_URL,
            (
                f"cnb-inbound:{envelope.tenant_id}:{envelope.channel_id}:"
                f"{envelope.external_message_id}"
            ),
        )

    @staticmethod
    def _authoritative_blocks(
        blocks: Sequence[MultimodalContentBlock],
        attachments: Sequence[Attachment],
    ) -> tuple[MultimodalContentBlock, ...]:
        stored = {item.id: item for item in attachments}
        normalized: list[MultimodalContentBlock] = []
        for block in blocks:
            if block.kind in {ContentBlockKind.TEXT, ContentBlockKind.MARKDOWN}:
                normalized.append(block)
                continue
            if block.attachment_id is None or block.attachment_id not in stored:
                raise AttachmentValidationError("附件内容块未引用当前消息的已校验附件")
            attachment = stored[block.attachment_id]
            expected_kind = (
                ContentBlockKind.IMAGE
                if attachment.content_type.startswith("image/")
                else ContentBlockKind.FILE
            )
            metadata_matches = (
                block.kind is expected_kind
                and (
                    block.content_type is None
                    or block.content_type.split(";", maxsplit=1)[0].strip().lower()
                    == attachment.content_type
                )
                and (block.file_name is None or block.file_name == attachment.original_name)
                and (block.size_bytes is None or block.size_bytes == attachment.size_bytes)
                and (
                    block.sha256 is None or compare_digest(block.sha256.lower(), attachment.sha256)
                )
            )
            if not metadata_matches:
                raise AttachmentValidationError("附件内容块元数据与持久化记录不一致")
            normalized.append(
                MultimodalContentBlock(
                    kind=expected_kind,
                    attachment_id=attachment.id,
                    content_type=attachment.content_type,
                    file_name=attachment.original_name,
                    size_bytes=attachment.size_bytes,
                    sha256=attachment.sha256,
                    alt_text=block.alt_text,
                )
            )
        return tuple(normalized)

    @staticmethod
    def _content_projection(blocks: Sequence[MultimodalContentBlock]) -> str:
        text = "\n\n".join(
            block.text or ""
            for block in blocks
            if block.kind in {ContentBlockKind.TEXT, ContentBlockKind.MARKDOWN}
        ).strip()
        return text or "[用户发送了附件]"


class InboundMessageTaskHandler:
    """严格解析已净化 Envelope，并按配置接入真实 Agent 执行链路。"""

    def __init__(self, processor: InboundConversationProcessor | None = None) -> None:
        self._processor = processor

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        envelope = self._envelope(job)
        result: dict[str, JsonValue] = {
            "schema_version": envelope.schema_version,
            "routing_status": "ready_for_agent" if self._processor is None else "processed",
            "agent_id": str(envelope.agent_id),
            "channel_id": str(envelope.channel_id),
            "user_id": str(envelope.user_id),
            "conversation_id": str(envelope.conversation_id),
            "content_block_count": len(envelope.blocks),
        }
        if self._processor is None:
            return result
        processed = await self._processor.process(envelope)
        result.update(
            {
                "message_id": str(processed.message_id),
                "run_id": str(processed.run_id),
                "run_status": processed.run_status.value,
                "idempotent_replay": processed.idempotent_replay,
                "execution_status": processed.execution_status,
            }
        )
        if processed.reply_delivery is not None:
            result.update(
                {
                    "reply_delivery_status": processed.reply_delivery.status.value,
                    "reply_external_message_id": processed.reply_delivery.external_message_id,
                    "reply_idempotent_replay": processed.reply_delivery.idempotent_replay,
                }
            )
        return result

    @classmethod
    def _envelope(cls, job: BackgroundJob) -> InboundEnvelope:
        payload = job.payload
        schema_version = cls._required_text(payload, "schema_version", 20)
        if schema_version != INBOUND_ENVELOPE_SCHEMA_VERSION:
            raise PermanentTaskError("Inbox Envelope schema version 不受支持")
        blocks_payload = payload.get("blocks")
        if not isinstance(blocks_payload, list) or not blocks_payload:
            raise PermanentTaskError("Inbox Envelope 缺少内容块")
        occurred_at = cls._datetime(payload, "occurred_at")
        received_at = cls._datetime(payload, "received_at")
        return InboundEnvelope(
            schema_version=schema_version,
            tenant_id=job.tenant_id,
            agent_id=cls._uuid(payload, "agent_id"),
            channel_id=cls._uuid(payload, "channel_id"),
            platform=cls._enum(payload, "platform", ChannelPlatform),
            external_event_id=cls._required_text(payload, "external_event_id", 255),
            external_subject_id=cls._required_text(payload, "external_subject_id", 255),
            user_id=cls._uuid(payload, "user_id"),
            conversation_kind=cls._enum(
                payload,
                "conversation_kind",
                ExternalConversationKind,
            ),
            external_conversation_id=cls._required_text(payload, "external_conversation_id", 255),
            external_thread_id=cls._optional_text(payload, "external_thread_id", 255),
            conversation_id=cls._uuid(payload, "conversation_id"),
            external_message_id=cls._required_text(payload, "external_message_id", 255),
            blocks=tuple(cls._block(item) for item in blocks_payload),
            occurred_at=occurred_at,
            received_at=received_at,
        )

    @classmethod
    def _block(cls, value: object) -> MultimodalContentBlock:
        if not isinstance(value, dict):
            raise PermanentTaskError("Inbox Envelope 内容块必须是对象")
        block = cast(dict[str, object], value)
        kind = cls._enum(block, "kind", ContentBlockKind)
        text = cls._optional_content(block, "text", 200_000)
        attachment_id = cls._optional_uuid(block, "attachment_id")
        if kind in {ContentBlockKind.TEXT, ContentBlockKind.MARKDOWN}:
            if text is None or attachment_id is not None:
                raise PermanentTaskError("Inbox Envelope 文本内容块无效")
        elif attachment_id is None or text is not None:
            raise PermanentTaskError("Inbox Envelope 附件内容块无效")
        size_bytes = cls._optional_integer(block, "size_bytes")
        if size_bytes is not None and size_bytes <= 0:
            raise PermanentTaskError("Inbox Envelope 附件大小无效")
        digest = cls._optional_text(block, "sha256", 64)
        if digest is not None and not _SHA256_PATTERN.fullmatch(digest.lower()):
            raise PermanentTaskError("Inbox Envelope 附件摘要无效")
        return MultimodalContentBlock(
            kind=kind,
            text=text,
            attachment_id=attachment_id,
            content_type=cls._optional_text(block, "content_type", 255),
            file_name=cls._optional_text(block, "file_name", 255),
            size_bytes=size_bytes,
            sha256=digest.lower() if digest is not None else None,
            alt_text=cls._optional_text(block, "alt_text", 2_000),
        )

    @staticmethod
    def _required_text(values: Mapping[str, object], key: str, maximum: int) -> str:
        value = values.get(key)
        if not isinstance(value, str):
            raise PermanentTaskError(f"Inbox Envelope 缺少文本字段：{key}")
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > maximum
            or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        ):
            raise PermanentTaskError(f"Inbox Envelope 文本字段无效：{key}")
        return normalized

    @classmethod
    def _optional_text(cls, values: Mapping[str, object], key: str, maximum: int) -> str | None:
        value = values.get(key)
        if value is None:
            return None
        return cls._required_text(values, key, maximum)

    @staticmethod
    def _optional_content(values: Mapping[str, object], key: str, maximum: int) -> str | None:
        value = values.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise PermanentTaskError(f"Inbox Envelope 内容字段无效：{key}")
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > maximum
            or any(
                (ord(character) < 32 and character not in {"\t", "\n", "\r"})
                or ord(character) == 127
                for character in normalized
            )
        ):
            raise PermanentTaskError(f"Inbox Envelope 内容字段无效：{key}")
        return normalized

    @staticmethod
    def _uuid(values: Mapping[str, object], key: str) -> UUID:
        value = values.get(key)
        if not isinstance(value, str):
            raise PermanentTaskError(f"Inbox Envelope 缺少路由字段：{key}")
        try:
            return UUID(value)
        except ValueError as error:
            raise PermanentTaskError(f"Inbox Envelope 路由字段无效：{key}") from error

    @classmethod
    def _optional_uuid(cls, values: Mapping[str, object], key: str) -> UUID | None:
        if values.get(key) is None:
            return None
        return cls._uuid(values, key)

    @staticmethod
    def _optional_integer(values: Mapping[str, object], key: str) -> int | None:
        value = values.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise PermanentTaskError(f"Inbox Envelope 整数字段无效：{key}")
        return value

    @staticmethod
    def _datetime(values: Mapping[str, object], key: str) -> datetime:
        value = values.get(key)
        if not isinstance(value, str):
            raise PermanentTaskError(f"Inbox Envelope 缺少时间字段：{key}")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise PermanentTaskError(f"Inbox Envelope 时间字段无效：{key}") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PermanentTaskError(f"Inbox Envelope 时间字段缺少时区：{key}")
        return parsed

    @staticmethod
    @overload
    def _enum(
        values: Mapping[str, object], key: str, enum_type: type[ChannelPlatform]
    ) -> ChannelPlatform: ...

    @staticmethod
    @overload
    def _enum(
        values: Mapping[str, object], key: str, enum_type: type[ExternalConversationKind]
    ) -> ExternalConversationKind: ...

    @staticmethod
    @overload
    def _enum(
        values: Mapping[str, object], key: str, enum_type: type[ContentBlockKind]
    ) -> ContentBlockKind: ...

    @staticmethod
    def _enum(
        values: Mapping[str, object],
        key: str,
        enum_type: type[StrEnum],
    ) -> StrEnum:
        value = values.get(key)
        if not isinstance(value, str):
            raise PermanentTaskError(f"Inbox Envelope 枚举字段缺失：{key}")
        try:
            return enum_type(value)
        except ValueError as error:
            raise PermanentTaskError(f"Inbox Envelope 枚举字段无效：{key}") from error


__all__ = [
    "InboundAcceptance",
    "InboundAttachmentServiceFactory",
    "InboundConflictError",
    "InboundConversationProcessor",
    "InboundConversationServiceFactory",
    "InboundGatewayRepository",
    "InboundGatewayService",
    "InboundMessageTaskHandler",
    "InboundNotFoundError",
    "InboundProcessingResult",
    "InboundReplyDispatcher",
    "InboundValidationError",
]
