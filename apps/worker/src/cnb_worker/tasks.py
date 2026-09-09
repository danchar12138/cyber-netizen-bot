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

from cnb_application import (
    BackgroundJobHandler,
    BackgroundTaskService,
    ConfigurationService,
    EmbeddingRebuildTaskHandler,
    EpisodeConsolidationTaskHandler,
    MemoryExtractionTaskHandler,
    MemoryService,
    ReflectionTaskHandler,
    RelationshipUpdateTaskHandler,
    ScheduledActionService,
    ScheduledActionTaskHandler,
    TaskDispatcher,
    build_default_registry,
)
from cnb_domain import BackgroundJobKind
from cnb_infrastructure import (
    SqlAlchemyConfigurationRepository,
    SqlAlchemyMemoryRepository,
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
task_service = BackgroundTaskService(task_repository)
memory_service = MemoryService(memory_repository)
configuration_service = ConfigurationService(build_default_registry(), configuration_repository)
scheduled_action_service = ScheduledActionService(task_repository, task_service)
worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
worker_started_at = datetime.now(UTC)
worker_queues = ("system", "memory", "reflection", "proactive")


class _ActorSender(Protocol):
    def send(self, job_id: str) -> object: ...


class DramatiqTaskDispatcher(TaskDispatcher):
    """按真相任务记录的队列将单一 job_id 投递给对应 Actor。"""

    async def dispatch(self, *, job_id: UUID, queue: str) -> None:
        actor = _actors_by_queue.get(queue)
        if actor is None:
            raise ValueError(f"没有对应队列 Actor：{queue}")
        actor.send(str(job_id))


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


_handlers: dict[BackgroundJobKind, BackgroundJobHandler] = {
    BackgroundJobKind.REFLECTION: ReflectionTaskHandler(memory_service),
    BackgroundJobKind.EPISODE_CONSOLIDATION: EpisodeConsolidationTaskHandler(memory_service),
    BackgroundJobKind.MEMORY_EXTRACTION: MemoryExtractionTaskHandler(memory_service),
    BackgroundJobKind.EMBEDDING_REBUILD: EmbeddingRebuildTaskHandler(memory_service),
    BackgroundJobKind.RELATIONSHIP_UPDATE: RelationshipUpdateTaskHandler(memory_service),
    BackgroundJobKind.SCHEDULED_ACTION: ScheduledActionTaskHandler(
        repository=task_repository,
        scheduled_actions=scheduled_action_service,
        configuration=configuration_service,
        memory=memory_service,
    ),
}
_actors_by_queue: dict[str, _ActorSender] = {
    "memory": cast(_ActorSender, process_memory_job),
    "reflection": cast(_ActorSender, process_reflection_job),
    "proactive": cast(_ActorSender, process_proactive_job),
}
dispatcher = DramatiqTaskDispatcher()


async def _maintenance_once() -> None:
    await task_service.heartbeat(
        worker_id=worker_id,
        queues=worker_queues,
        started_at=worker_started_at,
    )
    await task_service.recover_expired()
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
