"""可靠异步任务、反思与主动行为策略测试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cnb_application import (
    BackgroundTaskService,
    MemoryService,
    ReflectionTaskHandler,
    ScheduledActionService,
)
from cnb_cognition import ProactiveContext, ProactivePolicy, ProactivePolicyEvaluator
from cnb_domain import (
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    JsonValue,
    OutboxEventStatus,
    ScheduledAction,
    ScheduledActionKind,
    ScheduledActionStatus,
)
from cnb_infrastructure import InMemoryMemoryRepository, InMemoryTaskRepository


class RecordingDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[tuple[UUID, str]] = []

    async def dispatch(self, *, job_id: UUID, queue: str) -> None:
        self.dispatched.append((job_id, queue))


class CountingHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        self.calls += 1
        return {"handled_job_id": str(job.id)}


class FailingHandler:
    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        del job
        raise RuntimeError("测试故障正文不能进入任务摘要")


async def test_outbox_dispatch_and_duplicate_delivery_have_one_side_effect() -> None:
    repository = InMemoryTaskRepository()
    service = BackgroundTaskService(repository)
    tenant_id = uuid4()
    actor_id = uuid4()
    first = await service.accept_inbox_event(
        tenant_id=tenant_id,
        event_key="web:message:42",
        event_type="message.received",
        payload={"message_id": str(uuid4())},
        job_kind=BackgroundJobKind.REFLECTION,
        created_by=actor_id,
    )
    duplicate = await service.accept_inbox_event(
        tenant_id=tenant_id,
        event_key="web:message:42",
        event_type="message.received",
        payload={"message_id": str(uuid4())},
        job_kind=BackgroundJobKind.REFLECTION,
        created_by=actor_id,
    )
    assert first.created is True
    assert duplicate.created is False
    assert duplicate.job.id == first.job.id
    assert len(repository.jobs) == len(repository.inbox_events) == 1

    # API/Worker 进程重启后只要 PostgreSQL 真相仍在，发布与执行即可继续。
    service = BackgroundTaskService(repository)
    dispatcher = RecordingDispatcher()
    assert await service.publish_due(dispatcher=dispatcher, worker_id="publisher-1") == 1
    assert await service.publish_due(dispatcher=dispatcher, worker_id="publisher-1") == 0
    assert dispatcher.dispatched == [(first.job.id, "reflection")]

    handler = CountingHandler()
    handlers = {BackgroundJobKind.REFLECTION: handler}
    completed = await service.execute(
        job_id=first.job.id,
        worker_id="worker-1",
        handlers=handlers,
    )
    duplicate_delivery = await service.execute(
        job_id=first.job.id,
        worker_id="worker-2",
        handlers=handlers,
    )
    assert completed is not None and completed.status is BackgroundJobStatus.SUCCEEDED
    assert duplicate_delivery is None
    assert handler.calls == 1
    assert len(await service.list_attempts(tenant_id=tenant_id, job_id=first.job.id)) == 1


async def test_outbox_publisher_restart_recovers_expired_lease() -> None:
    repository = InMemoryTaskRepository()
    service = BackgroundTaskService(repository)
    queued = await service.enqueue(
        tenant_id=uuid4(),
        kind=BackgroundJobKind.EPISODE_CONSOLIDATION,
        payload={"episode_id": str(uuid4())},
        deduplication_key="episode:publisher-restart",
        created_by=uuid4(),
    )
    now = datetime.now(UTC)
    claimed = await repository.claim_outbox(
        worker_id="lost-publisher",
        now=now,
        lease_expires_at=now - timedelta(seconds=1),
        limit=10,
    )
    assert len(claimed) == 1
    assert claimed[0].status is OutboxEventStatus.PUBLISHING

    restarted_service = BackgroundTaskService(repository)
    assert await restarted_service.recover_expired() == 1
    dispatcher = RecordingDispatcher()
    assert (
        await restarted_service.publish_due(
            dispatcher=dispatcher,
            worker_id="replacement-publisher",
        )
        == 1
    )
    assert dispatcher.dispatched == [(queued.job.id, "memory")]


async def test_failure_enters_dead_letter_and_replay_preserves_original() -> None:
    repository = InMemoryTaskRepository()
    service = BackgroundTaskService(repository)
    tenant_id = uuid4()
    actor_id = uuid4()
    queued = await service.enqueue(
        tenant_id=tenant_id,
        kind=BackgroundJobKind.MEMORY_EXTRACTION,
        payload={"source_id": "safe-source"},
        deduplication_key="memory:test-dead-letter",
        created_by=actor_id,
        max_attempts=1,
    )
    failed = await service.execute(
        job_id=queued.job.id,
        worker_id="worker-failure",
        handlers={BackgroundJobKind.MEMORY_EXTRACTION: FailingHandler()},
    )
    assert failed is not None and failed.status is BackgroundJobStatus.DEAD_LETTER
    assert failed.last_error_code == "RuntimeError"
    assert "测试故障正文" not in (failed.last_error_summary or "")

    replayed = await service.replay(
        tenant_id=tenant_id,
        job_id=failed.id,
        actor_id=actor_id,
        confirmed=True,
        reason="依赖已恢复",
    )
    assert replayed.id != failed.id
    assert replayed.replayed_from_id == failed.id
    assert replayed.status is BackgroundJobStatus.PENDING
    assert repository.jobs[failed.id].status is BackgroundJobStatus.DEAD_LETTER


async def test_expired_worker_lease_is_recovered_without_concurrent_claim() -> None:
    repository = InMemoryTaskRepository()
    service = BackgroundTaskService(repository)
    queued = await service.enqueue(
        tenant_id=uuid4(),
        kind=BackgroundJobKind.RELATIONSHIP_UPDATE,
        payload={"event_type": "test"},
        deduplication_key="relationship:lease-test",
        created_by=uuid4(),
        max_attempts=2,
        lease_seconds=1,
    )
    now = datetime.now(UTC)
    claim = await repository.claim_job(
        job_id=queued.job.id,
        worker_id="lost-worker",
        now=now,
    )
    assert claim is not None
    assert (
        await repository.claim_job(
            job_id=queued.job.id,
            worker_id="concurrent-worker",
            now=now,
        )
        is None
    )
    assert await repository.recover_expired_leases(now=now + timedelta(seconds=2)) >= 1
    assert repository.jobs[queued.job.id].status is BackgroundJobStatus.RETRYING
    attempts = await service.list_attempts(
        tenant_id=queued.job.tenant_id,
        job_id=queued.job.id,
    )
    assert attempts[0].status.value == "timed_out"


async def test_daily_social_budget_and_policy_suppress_second_action() -> None:
    repository = InMemoryTaskRepository()
    tasks = BackgroundTaskService(repository)
    service = ScheduledActionService(repository, tasks)
    tenant_id, agent_id, user_id, actor_id = (uuid4() for _ in range(4))
    now = datetime.now(UTC)
    actions: list[ScheduledAction] = []
    for index in range(2):
        action, created = await service.create(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            conversation_id=None,
            kind=ScheduledActionKind.PROACTIVE_MESSAGE,
            scheduled_for=now,
            expires_at=now + timedelta(days=1),
            idempotency_key=f"proactive:{index}",
            reason="自然跟进用户明确关心的事项",
            payload={"importance": 1.0, "confidence": 1.0},
            social_cost=1,
            created_by=actor_id,
            max_attempts=3,
            lease_seconds=60,
            retry_base_seconds=2,
        )
        assert created is True
        actions.append(action)
    policy = ProactivePolicy(
        enabled=True,
        minimum_score=0.5,
        daily_budget=1,
        quiet_hours_start=22,
        quiet_hours_end=8,
        require_recent_user_days=30,
    )
    context = ProactiveContext(
        local_hour=12,
        days_since_user_activity=0,
        importance=1,
        confidence=1,
        affinity=1,
        trust=1,
        familiarity=1,
        used_budget=0,
        social_cost=1,
        has_boundary_block=False,
    )
    first = await service.evaluate(
        tenant_id=tenant_id,
        action_id=actions[0].id,
        policy=policy,
        context=context,
        budget_date=now.date(),
    )
    second = await service.evaluate(
        tenant_id=tenant_id,
        action_id=actions[1].id,
        policy=policy,
        context=context,
        budget_date=now.date(),
    )
    assert first.status is ScheduledActionStatus.DISPATCHED
    assert second.status is ScheduledActionStatus.SUPPRESSED
    assert second.decision_reasons == ("daily_social_budget_exhausted",)


def test_proactive_policy_blocks_quiet_hours_and_relationship_boundary() -> None:
    result = ProactivePolicyEvaluator().evaluate(
        ProactivePolicy(True, 0.1, 5, 22, 8, 30),
        ProactiveContext(23, 0, 1, 1, 1, 1, 1, 0, 1, True),
    )
    assert result.approved is False
    assert "quiet_hours" in result.reasons
    assert "relationship_boundary_blocked" in result.reasons


async def test_reflection_handler_is_idempotent_and_keeps_source_traceability() -> None:
    task_repository = InMemoryTaskRepository()
    task_service = BackgroundTaskService(task_repository)
    memory_repository = InMemoryMemoryRepository()
    memory_service = MemoryService(memory_repository)
    tenant_id, agent_id, user_id, conversation_id, actor_id, message_id = (
        uuid4() for _ in range(6)
    )
    occurred_at = datetime.now(UTC)
    queued = await task_service.enqueue(
        tenant_id=tenant_id,
        kind=BackgroundJobKind.REFLECTION,
        payload={
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "trigger_message_id": str(message_id),
            "response_message_id": str(uuid4()),
            "trigger_text": "请记住我喜欢在周末徒步，谢谢。",
            "occurred_at": occurred_at.isoformat(),
            "actor_id": str(actor_id),
            "minimum_importance": 0.2,
        },
        deduplication_key="reflection:idempotent",
        created_by=actor_id,
    )
    handler = ReflectionTaskHandler(memory_service)
    first = await handler.handle(queued.job)
    second = await handler.handle(queued.job)
    assert first["episode_id"] == second["episode_id"]
    assert first["memory_id"] == second["memory_id"]
    assert len(memory_repository.episodes) == 1
    assert len(memory_repository.memories) == 1
    memory = next(iter(memory_repository.memories.values()))
    assert memory.confirmation.value == "unconfirmed"
    assert memory_repository.sources[memory.id][0].source_id == str(message_id)
    relationship = next(iter(memory_repository.relationships.values()))
    assert relationship.version == 1
