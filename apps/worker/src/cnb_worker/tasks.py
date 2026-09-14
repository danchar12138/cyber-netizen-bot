"""Dramatiq Worker 组合根、可靠任务 Actor 与后台维护循环。"""

import asyncio
import logging
import os
import socket
import threading
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID, uuid4

import dramatiq
from dramatiq import Broker, Middleware, Worker
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AsyncIO

from cnb_adapters import build_default_channel_registry, build_default_notification_registry
from cnb_application import (
    AlertNotificationService,
    AttachmentService,
    BackgroundJobHandler,
    BackgroundTaskService,
    ChannelConnectionProbeScheduler,
    ChannelConnectionProbeTaskHandler,
    ChannelDeliveryReceipt,
    ChannelService,
    CognitionService,
    ConfigurationService,
    ConversationService,
    EmbeddingRebuildTaskHandler,
    EpisodeConsolidationTaskHandler,
    InboundConversationProcessor,
    InboundMessageTaskHandler,
    InboundReplyDispatcher,
    MemoryExtractionTaskHandler,
    MemoryService,
    ModelReliabilityGuard,
    MultimodalInputService,
    NotificationDeliveryTaskHandler,
    ObservabilityService,
    ReflectionTaskHandler,
    RelationshipUpdateTaskHandler,
    ScheduledActionService,
    ScheduledActionTaskHandler,
    TaskDispatcher,
    build_default_registry,
)
from cnb_cognition import AnthropomorphicCognitiveRuntime
from cnb_domain import (
    BackgroundJobKind,
    ChannelPlatform,
    ContentBlockKind,
    DevelopmentIdentity,
    InboundEnvelope,
    Message,
    MultimodalContentBlock,
)
from cnb_infrastructure import (
    AesGcmEnvelopeCipher,
    ConfiguredModelProviderResolver,
    MinioObjectStorage,
    SqlAlchemyAdministrationRepository,
    SqlAlchemyAttachmentRepository,
    SqlAlchemyChannelRepository,
    SqlAlchemyCognitionRepository,
    SqlAlchemyConfigurationRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyMemoryRepository,
    SqlAlchemyObservabilityRepository,
    SqlAlchemySecretStore,
    SqlAlchemyTaskRepository,
    get_settings,
)
from cnb_infrastructure.database import create_session_factory

logger = logging.getLogger(__name__)
settings = get_settings()
broker = RedisBroker(url=settings.redis_url.get_secret_value())
broker.add_middleware(AsyncIO())  # pyright: ignore[reportUnknownMemberType]
dramatiq.set_broker(broker)

session_factory = create_session_factory(settings)
task_repository = SqlAlchemyTaskRepository(session_factory)
memory_repository = SqlAlchemyMemoryRepository(session_factory)
configuration_repository = SqlAlchemyConfigurationRepository(session_factory)
administration_repository = SqlAlchemyAdministrationRepository(session_factory)
conversation_repository = SqlAlchemyConversationRepository(session_factory)
cognition_repository = SqlAlchemyCognitionRepository(session_factory)
attachment_repository = SqlAlchemyAttachmentRepository(session_factory)
observability_repository = SqlAlchemyObservabilityRepository(session_factory)
task_service = BackgroundTaskService(task_repository)
memory_service = MemoryService(memory_repository)
configuration_service = ConfigurationService(build_default_registry(), configuration_repository)
observability_service = ObservabilityService(observability_repository, configuration_service)
channel_repository = SqlAlchemyChannelRepository(session_factory)
object_storage = MinioObjectStorage(settings)
secret_cipher = AesGcmEnvelopeCipher.from_encoded_key(
    settings.config_master_key.get_secret_value(),
    allow_development_placeholder=settings.environment in {"development", "test"},
)
secret_store = SqlAlchemySecretStore(session_factory, secret_cipher)
model_provider_resolver = ConfiguredModelProviderResolver(secret_store)
cognitive_runtime = AnthropomorphicCognitiveRuntime()
model_reliability_guard = ModelReliabilityGuard()
scheduled_action_service = ScheduledActionService(task_repository, task_service)
channel_service = ChannelService(
    channel_repository,
    build_default_channel_registry(),
    secret_store,
)
channel_probe_scheduler = ChannelConnectionProbeScheduler(
    repository=channel_repository,
    task_service=task_service,
    configuration=configuration_service,
)
alert_notification_service = AlertNotificationService(
    channel_service=channel_service,
    configuration_service=configuration_service,
    secret_store=secret_store,
    adapter_registry=build_default_notification_registry(),
    audit_recorder=administration_repository,
    task_service=task_service,
)
worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
worker_started_at = datetime.now(UTC)
_last_observability_bucket: int | None = None
worker_queues = (
    "system",
    "memory",
    "reflection",
    "proactive",
    "inbound",
    "notification",
    "channel",
)


class _ActorSender(Protocol):
    def send(self, job_id: str) -> object: ...


class TelegramInboundReplyDispatcher(InboundReplyDispatcher):
    """将完成的 Telegram 入站 Run 回复投递回原会话。"""

    def __init__(self, channels: ChannelService) -> None:
        self._channels = channels

    async def dispatch(
        self,
        *,
        envelope: InboundEnvelope,
        response: Message,
        idempotency_key: str,
    ) -> ChannelDeliveryReceipt:
        if envelope.platform is not ChannelPlatform.TELEGRAM:
            raise ValueError("Telegram 回复分发器收到不匹配的平台")
        text = response.content.strip()
        if not text:
            raise ValueError("Telegram 回复正文不能为空")
        return await self._channels.deliver(
            tenant_id=envelope.tenant_id,
            agent_id=envelope.agent_id,
            channel_id=envelope.channel_id,
            recipient_id=envelope.external_conversation_id,
            blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text=text),),
            idempotency_key=idempotency_key,
            request_streaming=False,
            thread_id=envelope.external_thread_id,
            edit_message_id=None,
            proactive=False,
        )


class DramatiqTaskDispatcher(TaskDispatcher):
    """按真相任务记录的队列将单一 job_id 投递给对应 Actor。"""

    async def dispatch(self, *, job_id: UUID, queue: str) -> None:
        actor = _actors_by_queue.get(queue)
        if actor is None:
            raise ValueError(f"没有对应队列 Actor：{queue}")
        actor.send(str(job_id))


def _conversation_service(
    *,
    identity: DevelopmentIdentity,
    channel_id: UUID,
) -> ConversationService:
    """为单个入站 Envelope 构建绑定身份与渠道作用域的真实对话服务。"""
    return ConversationService(
        repository=conversation_repository,
        runtime=cognitive_runtime,
        model_provider_resolver=model_provider_resolver,
        configuration_service=configuration_service,
        cognition_service=CognitionService(cognition_repository, agent_id=identity.agent_id),
        memory_service=memory_service,
        task_service=task_service,
        multimodal_input_service=MultimodalInputService(
            repository=attachment_repository,
            object_storage=object_storage,
            user_id=identity.user_id,
            tenant_id=identity.tenant_id,
        ),
        identity=identity,
        channel_id=channel_id,
        reliability_guard=model_reliability_guard,
    )


def _attachment_service(*, identity: DevelopmentIdentity) -> AttachmentService:
    """为入站附件复核构建绑定当前外部用户的生命周期服务。"""
    return AttachmentService(
        repository=attachment_repository,
        object_storage=object_storage,
        conversation_repository=conversation_repository,
        configuration_service=configuration_service,
        identity=identity,
    )


async def _execute_job(job_id: str) -> None:
    try:
        parsed_id = UUID(job_id)
    except ValueError:
        logger.error("收到无效任务 ID", extra={"job_id": job_id})
        return
    await task_service.execute(
        job_id=parsed_id,
        worker_id=worker_id,
        handlers=_handlers,
    )


@dramatiq.actor(queue_name="memory", max_retries=0)  # pyright: ignore[reportUnknownMemberType]
async def process_memory_job(job_id: str) -> None:
    """执行 Episode、记忆、关系与向量任务。"""
    await _execute_job(job_id)


@dramatiq.actor(queue_name="reflection", max_retries=0)  # pyright: ignore[reportUnknownMemberType]
async def process_reflection_job(job_id: str) -> None:
    """执行对话后的异步反思任务。"""
    await _execute_job(job_id)


@dramatiq.actor(queue_name="proactive", max_retries=0)  # pyright: ignore[reportUnknownMemberType]
async def process_proactive_job(job_id: str) -> None:
    """重新评估并分发到 P6 Channel Adapter 边界的主动行为。"""
    await _execute_job(job_id)


@dramatiq.actor(queue_name="inbound", max_retries=0)  # pyright: ignore[reportUnknownMemberType]
async def process_inbound_job(job_id: str) -> None:
    """消费已验签、净化并落库的版本化入站 Envelope。"""
    await _execute_job(job_id)


@dramatiq.actor(queue_name="notification", max_retries=0)  # pyright: ignore[reportUnknownMemberType]
async def process_notification_job(job_id: str) -> None:
    """执行告警邮件、飞书和 HTTPS Webhook 通知。"""
    await _execute_job(job_id)


@dramatiq.actor(queue_name="channel", max_retries=0)  # pyright: ignore[reportUnknownMemberType]
async def process_channel_job(job_id: str) -> None:
    """执行渠道连接探测等渠道运维任务。"""
    await _execute_job(job_id)


_handlers: dict[BackgroundJobKind, BackgroundJobHandler] = {
    BackgroundJobKind.REFLECTION: ReflectionTaskHandler(
        memory_service,
        conversation_repository,
        configuration_service,
    ),
    BackgroundJobKind.EPISODE_CONSOLIDATION: EpisodeConsolidationTaskHandler(memory_service),
    BackgroundJobKind.MEMORY_EXTRACTION: MemoryExtractionTaskHandler(memory_service),
    BackgroundJobKind.EMBEDDING_REBUILD: EmbeddingRebuildTaskHandler(memory_service),
    BackgroundJobKind.RELATIONSHIP_UPDATE: RelationshipUpdateTaskHandler(memory_service),
    BackgroundJobKind.SCHEDULED_ACTION: ScheduledActionTaskHandler(
        repository=task_repository,
        scheduled_actions=scheduled_action_service,
        configuration=configuration_service,
        memory=memory_service,
        channel_service=channel_service,
    ),
    BackgroundJobKind.INBOUND_MESSAGE: InboundMessageTaskHandler(
        InboundConversationProcessor(
            conversation_services=_conversation_service,
            attachment_services=_attachment_service,
            reply_dispatcher=TelegramInboundReplyDispatcher(channel_service),
        )
    ),
    BackgroundJobKind.NOTIFICATION_DELIVERY: NotificationDeliveryTaskHandler(
        build_default_notification_registry(),
        configuration_service,
        secret_store,
        observability_repository,
    ),
    BackgroundJobKind.CHANNEL_CONNECTION_TEST: ChannelConnectionProbeTaskHandler(
        channel_service,
        alert_notification_service,
    ),
}
_actors_by_queue: dict[str, _ActorSender] = {
    "memory": cast(_ActorSender, process_memory_job),
    "reflection": cast(_ActorSender, process_reflection_job),
    "proactive": cast(_ActorSender, process_proactive_job),
    "inbound": cast(_ActorSender, process_inbound_job),
    "notification": cast(_ActorSender, process_notification_job),
    "channel": cast(_ActorSender, process_channel_job),
}
dispatcher = DramatiqTaskDispatcher()


async def _maintenance_once() -> None:
    global _last_observability_bucket
    await task_service.heartbeat(
        worker_id=worker_id,
        queues=worker_queues,
        started_at=worker_started_at,
    )
    await task_service.recover_expired()
    await channel_probe_scheduler.schedule()
    observability_bucket = int(datetime.now(UTC).timestamp() // 60)
    if observability_bucket != _last_observability_bucket:
        for tenant_id, agent_id in await observability_repository.list_observability_agent_ids():
            evaluation = await observability_service.reconcile_alert_lifecycles(
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            for lifecycle in evaluation.activated_lifecycles:
                await alert_notification_service.enqueue_observability_active(
                    lifecycle=lifecycle,
                )
            for candidate in evaluation.due_escalations:
                await alert_notification_service.enqueue_observability_escalation(
                    lifecycle=candidate.lifecycle,
                    target_level=candidate.target_level,
                    adapter_key=candidate.adapter,
                )
            for lifecycle in evaluation.recovered_lifecycles:
                await alert_notification_service.enqueue_observability_recovery(
                    lifecycle=lifecycle,
                )
        _last_observability_bucket = observability_bucket
    await task_service.publish_due(dispatcher=dispatcher, worker_id=worker_id)


class ReliableTaskMaintenance(Middleware):
    """随 Worker 生命周期维护心跳、过期租约和事务 Outbox。"""

    def __init__(self, interval_seconds: float = 2.0) -> None:
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def after_worker_boot(self, broker: Broker, worker: Worker) -> None:
        del broker, worker
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="cnb-task-maintenance",
            daemon=True,
        )
        self._thread.start()

    def before_worker_shutdown(self, broker: Broker, worker: Worker) -> None:
        del broker, worker
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(_maintenance_once())
            except Exception:
                logger.exception("任务维护循环执行失败")
            self._stop.wait(self._interval_seconds)


broker.add_middleware(ReliableTaskMaintenance())  # pyright: ignore[reportUnknownMemberType]


@dramatiq.actor(queue_name="system", max_retries=2)  # pyright: ignore[reportUnknownMemberType]
def ping(value: str = "pong") -> dict[str, str]:
    """用于验证 Broker 与 Worker 连接的诊断 Actor。"""
    return {"status": value}
