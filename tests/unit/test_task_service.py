"""可靠异步任务、反思与主动行为策略测试。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_application import (
    BackgroundTaskService,
    ConfigurationService,
    MemoryService,
    ObservabilityNotificationReplayGuard,
    ObservabilityService,
    ReflectionTaskHandler,
    ScheduledActionService,
    TaskConflictError,
    build_default_registry,
)
from cnb_cognition import ProactiveContext, ProactivePolicy, ProactivePolicyEvaluator
from cnb_domain import (
    ActiveAlert,
    AlertSeverity,
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    ConfigEntry,
    ConfigScope,
    DevelopmentIdentity,
    JsonValue,
    ObservabilityAlertDispositionStatus,
    OutboxEventStatus,
    ScheduledAction,
    ScheduledActionKind,
    ScheduledActionStatus,
)
from cnb_infrastructure import (
    InMemoryMemoryRepository,
    InMemoryTaskRepository,
    MemoryConfigurationRepository,
    MemoryConversationRepository,
    MemoryObservabilityRepository,
)


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


class BlockingHandler:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        self.started.set()
        await self.release.wait()
        return {"handled_job_id": str(job.id)}


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


async def test_long_running_handler_renews_lease_until_completion() -> None:
    repository = InMemoryTaskRepository()
    service = BackgroundTaskService(repository)
    queued = await service.enqueue(
        tenant_id=uuid4(),
        kind=BackgroundJobKind.REFLECTION,
        payload={"conversation_id": str(uuid4())},
        deduplication_key="reflection:lease-renewal",
        created_by=uuid4(),
        lease_seconds=1,
    )
    handler = BlockingHandler()
    execution = asyncio.create_task(
        service.execute(
            job_id=queued.job.id,
            worker_id="slow-worker",
            handlers={BackgroundJobKind.REFLECTION: handler},
        )
    )
    await handler.started.wait()
    await asyncio.sleep(1.1)

    assert await service.recover_expired() == 0
    handler.release.set()
    completed = await execution
    assert completed is not None
    assert completed.status is BackgroundJobStatus.SUCCEEDED


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


async def test_observability_notification_replay_rechecks_current_suppression() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    observability_repository = MemoryObservabilityRepository()
    observability_service = ObservabilityService(
        observability_repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    reconciliation = await observability_repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(
            ActiveAlert(
                code="notification_delivery_dead_letters",
                severity=AlertSeverity.CRITICAL,
                title="通知死信",
                summary="安全摘要",
                current_value=1,
                threshold_value=0,
                unit="jobs",
                source_type="notification",
                source_key="notification_delivery_dead_letters",
            ),
        ),
        observed_at=now,
    )
    lifecycle = reconciliation.active_lifecycles[0]
    suppressed = await observability_service.suppress_alert(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=lifecycle.id,
        reason="计划内维护",
        expires_at=now + timedelta(hours=1),
        actor_id=actor_id,
        confirmed=True,
        now=now,
    )
    assert suppressed.status is ObservabilityAlertDispositionStatus.SUPPRESSED

    task_repository = InMemoryTaskRepository()
    replay_guard = ObservabilityNotificationReplayGuard(observability_repository)
    task_service = BackgroundTaskService(
        task_repository,
        replay_guard=replay_guard,
    )
    queued = await task_service.enqueue(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=BackgroundJobKind.NOTIFICATION_DELIVERY,
        payload={
            "agent_id": str(agent_id),
            "delivery_payload": {
                "event": "observability.alerts.active",
                "source_type": lifecycle.source_type,
                "source_key": lifecycle.source_key,
            },
        },
        deduplication_key="notification:replay-suppression-test",
        created_by=actor_id,
    )
    task_repository.jobs[queued.job.id] = replace(
        queued.job, status=BackgroundJobStatus.DEAD_LETTER
    )

    with pytest.raises(TaskConflictError, match="仍处于抑制期"):
        await task_service.replay(
            tenant_id=tenant_id,
            job_id=queued.job.id,
            actor_id=actor_id,
            confirmed=True,
            reason="尝试重放",
        )
    assert len(task_repository.jobs) == 1
    await replay_guard.check(job=queued.job, now=now + timedelta(hours=2))

    await observability_service.clear_alert_disposition(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=lifecycle.id,
        actor_id=actor_id,
        confirmed=True,
    )
    replayed = await task_service.replay(
        tenant_id=tenant_id,
        job_id=queued.job.id,
        actor_id=actor_id,
        confirmed=True,
        reason="抑制已解除",
    )
    assert replayed.replayed_from_id == queued.job.id


async def test_replay_chain_returns_oldest_source_first() -> None:
    repository = InMemoryTaskRepository()
    service = BackgroundTaskService(repository)
    tenant_id = uuid4()
    actor_id = uuid4()
    original = await service.enqueue(
        tenant_id=tenant_id,
        kind=BackgroundJobKind.MEMORY_EXTRACTION,
        payload={"source_id": "safe-source"},
        deduplication_key="replay-chain:original",
        created_by=actor_id,
    )
    repository.jobs[original.job.id] = replace(original.job, status=BackgroundJobStatus.DEAD_LETTER)
    first_replay = await service.replay(
        tenant_id=tenant_id,
        job_id=original.job.id,
        actor_id=actor_id,
        confirmed=True,
        reason="第一次重放",
    )
    repository.jobs[first_replay.id] = replace(first_replay, status=BackgroundJobStatus.DEAD_LETTER)
    second_replay = await service.replay(
        tenant_id=tenant_id,
        job_id=first_replay.id,
        actor_id=actor_id,
        confirmed=True,
        reason="第二次重放",
    )

    chain = await service.replay_chain(tenant_id=tenant_id, job_id=second_replay.id)

    assert [item.id for item in chain] == [original.job.id, first_replay.id, second_replay.id]


async def test_replay_chain_does_not_follow_cross_tenant_source() -> None:
    repository = InMemoryTaskRepository()
    service = BackgroundTaskService(repository)
    source_tenant, current_tenant = uuid4(), uuid4()
    source = await service.enqueue(
        tenant_id=source_tenant,
        kind=BackgroundJobKind.REFLECTION,
        payload={},
        deduplication_key="replay-chain:foreign-source",
        created_by=None,
    )
    current = await service.enqueue(
        tenant_id=current_tenant,
        kind=BackgroundJobKind.REFLECTION,
        payload={},
        deduplication_key="replay-chain:current",
        created_by=None,
    )
    repository.jobs[current.job.id] = replace(current.job, replayed_from_id=source.job.id)

    chain = await service.replay_chain(tenant_id=current_tenant, job_id=current.job.id)

    assert [item.id for item in chain] == [current.job.id]


async def test_replay_chain_stops_on_cycles() -> None:
    repository = InMemoryTaskRepository()
    service = BackgroundTaskService(repository)
    tenant_id = uuid4()
    first = await service.enqueue(
        tenant_id=tenant_id,
        kind=BackgroundJobKind.REFLECTION,
        payload={},
        deduplication_key="replay-chain:cycle-a",
        created_by=None,
    )
    second = await service.enqueue(
        tenant_id=tenant_id,
        kind=BackgroundJobKind.REFLECTION,
        payload={},
        deduplication_key="replay-chain:cycle-b",
        created_by=None,
    )
    repository.jobs[first.job.id] = replace(first.job, replayed_from_id=second.job.id)
    repository.jobs[second.job.id] = replace(second.job, replayed_from_id=first.job.id)

    chain = await service.replay_chain(tenant_id=tenant_id, job_id=first.job.id)

    assert [item.id for item in chain] == [second.job.id, first.job.id]


async def test_replay_chain_is_limited_to_twenty_levels() -> None:
    repository = InMemoryTaskRepository()
    service = BackgroundTaskService(repository)
    tenant_id = uuid4()
    jobs: list[BackgroundJob] = []
    previous_id = None
    for index in range(21):
        result = await service.enqueue(
            tenant_id=tenant_id,
            kind=BackgroundJobKind.REFLECTION,
            payload={},
            deduplication_key=f"replay-chain:depth:{index}",
            created_by=None,
        )
        job = replace(result.job, replayed_from_id=previous_id)
        repository.jobs[job.id] = job
        jobs.append(job)
        previous_id = job.id

    chain = await service.replay_chain(tenant_id=tenant_id, job_id=jobs[-1].id)

    assert len(chain) == 20
    assert [item.id for item in chain] == [item.id for item in jobs[1:]]


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
    configuration = ConfigurationService(build_default_registry(), MemoryConfigurationRepository())
    conversation_repository = MemoryConversationRepository()
    identity = DevelopmentIdentity(
        tenant_id=uuid4(),
        agent_id=uuid4(),
        user_id=uuid4(),
        user_name="反思测试用户",
        agent_name="反思测试 Agent",
    )
    await conversation_repository.ensure_development_identity(identity)
    conversation = await conversation_repository.create_conversation(
        identity=identity,
        title="反思幂等测试",
    )
    pending = await conversation_repository.begin_agent_run(
        identity=identity,
        conversation_id=conversation.id,
        client_message_id=uuid4(),
        content="请记住我喜欢在周末徒步，谢谢。",
        attachments=(),
        configuration_version=0,
        persona_version=0,
        prompt_version=0,
        policy_version=0,
        model_route_version=0,
        model_profile="测试模型",
    )
    await conversation_repository.mark_run_started(pending.run.id)
    await conversation_repository.complete_run(pending.run.id, None)
    stable_payload = {
        "run_id": str(pending.run.id),
        "agent_id": str(identity.agent_id),
        "user_id": str(identity.user_id),
        "conversation_id": str(conversation.id),
        "trigger_message_id": str(pending.trigger_message.id),
        "response_message_id": str(pending.response_message.id),
    }
    legacy = await task_service.enqueue(
        tenant_id=identity.tenant_id,
        kind=BackgroundJobKind.REFLECTION,
        payload={key: value for key, value in stable_payload.items() if key != "run_id"}
        | {
            "trigger_text": "不可信旧正文不应被采用",
            "occurred_at": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
            "actor_id": str(uuid4()),
            "minimum_importance": 1.0,
        },
        deduplication_key="reflection:legacy",
        created_by=identity.user_id,
    )
    replay = await task_service.enqueue(
        tenant_id=identity.tenant_id,
        kind=BackgroundJobKind.REFLECTION,
        payload=stable_payload,
        deduplication_key="reflection:manual-replay",
        created_by=identity.user_id,
    )
    handler = ReflectionTaskHandler(
        memory_service,
        conversation_repository,
        configuration,
    )
    first = await handler.handle(legacy.job)
    second = await handler.handle(replay.job)
    assert first["episode_id"] == second["episode_id"]
    assert first["memory_id"] == second["memory_id"]
    assert first["memory_decision"] == "write"
    assert first["memory_kind"] == "semantic"
    assert len(memory_repository.episodes) == 1
    assert len(memory_repository.memories) == 1
    memory = next(iter(memory_repository.memories.values()))
    assert memory.content is not None and "周末徒步" in memory.content
    assert "不可信旧正文" not in memory.content
    assert memory.confirmation.value == "unconfirmed"
    assert memory_repository.sources[memory.id][0].source_id == str(pending.trigger_message.id)
    relationship = next(iter(memory_repository.relationships.values()))
    assert relationship.version == 1
    assert len(memory_repository.relationship_events[relationship.id]) == 1


async def test_reflection_replay_uses_the_agent_run_configuration_version() -> None:
    configuration_repository = MemoryConfigurationRepository()
    configuration = ConfigurationService(build_default_registry(), configuration_repository)
    frozen = await configuration.create_draft(
        note="反思运行冻结配置",
        values=(
            ConfigEntry(
                key="cognition.reflection.minimum_importance",
                scope_type=ConfigScope.SYSTEM,
                value=0.95,
            ),
            ConfigEntry(
                key="cognition.reflection.relationship_positive_step",
                scope_type=ConfigScope.SYSTEM,
                value=0.0,
            ),
        ),
    )
    frozen = await configuration.publish(frozen.id)

    conversation_repository = MemoryConversationRepository()
    identity = DevelopmentIdentity(
        tenant_id=uuid4(),
        agent_id=uuid4(),
        user_id=uuid4(),
        user_name="配置冻结测试用户",
        agent_name="配置冻结测试 Agent",
    )
    await conversation_repository.ensure_development_identity(identity)
    conversation = await conversation_repository.create_conversation(
        identity=identity,
        title="反思配置冻结测试",
    )
    pending = await conversation_repository.begin_agent_run(
        identity=identity,
        conversation_id=conversation.id,
        client_message_id=uuid4(),
        content="请记住我喜欢周末徒步，谢谢。",
        attachments=(),
        configuration_version=frozen.version,
        persona_version=0,
        prompt_version=0,
        policy_version=0,
        model_route_version=0,
        model_profile="测试模型",
    )
    await conversation_repository.mark_run_started(pending.run.id)
    await conversation_repository.complete_run(pending.run.id, None)

    latest = await configuration.create_draft(
        note="反思运行后发布的新配置",
        values=(
            ConfigEntry(
                key="cognition.reflection.minimum_importance",
                scope_type=ConfigScope.SYSTEM,
                value=0.1,
            ),
            ConfigEntry(
                key="cognition.reflection.relationship_positive_step",
                scope_type=ConfigScope.SYSTEM,
                value=0.2,
            ),
        ),
    )
    await configuration.publish(latest.id)
    task_service = BackgroundTaskService(InMemoryTaskRepository())
    queued = await task_service.enqueue(
        tenant_id=identity.tenant_id,
        kind=BackgroundJobKind.REFLECTION,
        payload={
            "run_id": str(pending.run.id),
            "agent_id": str(identity.agent_id),
            "user_id": str(identity.user_id),
            "conversation_id": str(conversation.id),
            "trigger_message_id": str(pending.trigger_message.id),
            "response_message_id": str(pending.response_message.id),
        },
        deduplication_key="reflection:frozen-config",
        created_by=identity.user_id,
    )
    memory_repository = InMemoryMemoryRepository()
    result = await ReflectionTaskHandler(
        MemoryService(memory_repository),
        conversation_repository,
        configuration,
    ).handle(queued.job)

    assert result["memory_created"] is False
    assert memory_repository.memories == {}
    relationship = next(iter(memory_repository.relationships.values()))
    assert relationship.affinity == 0


async def test_reflection_source_rejects_cross_scope_and_unfinished_run() -> None:
    repository = MemoryConversationRepository()
    identity = DevelopmentIdentity(
        tenant_id=uuid4(),
        agent_id=uuid4(),
        user_id=uuid4(),
        user_name="隔离测试用户",
        agent_name="隔离测试 Agent",
    )
    await repository.ensure_development_identity(identity)
    conversation = await repository.create_conversation(identity=identity, title="反思隔离测试")
    pending = await repository.begin_agent_run(
        identity=identity,
        conversation_id=conversation.id,
        client_message_id=uuid4(),
        content="我喜欢清晨散步。",
        attachments=(),
        configuration_version=0,
        persona_version=0,
        prompt_version=0,
        policy_version=0,
        model_route_version=0,
        model_profile="测试模型",
    )

    query = {
        "tenant_id": identity.tenant_id,
        "agent_id": identity.agent_id,
        "user_id": identity.user_id,
        "conversation_id": conversation.id,
        "trigger_message_id": pending.trigger_message.id,
        "response_message_id": pending.response_message.id,
        "run_id": pending.run.id,
    }
    assert await repository.get_reflection_source(**query) is None
    await repository.mark_run_started(pending.run.id)
    await repository.complete_run(pending.run.id, None)
    assert await repository.get_reflection_source(**query) is not None
    for key in (
        "tenant_id",
        "agent_id",
        "user_id",
        "conversation_id",
        "trigger_message_id",
        "response_message_id",
        "run_id",
    ):
        mismatched = dict(query)
        mismatched[key] = uuid4()
        assert await repository.get_reflection_source(**mismatched) is None

    await repository.soft_delete_conversation(
        conversation_id=conversation.id,
        user_id=identity.user_id,
    )
    assert await repository.get_reflection_source(**query) is None
