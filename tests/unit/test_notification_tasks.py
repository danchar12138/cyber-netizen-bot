"""通知队列任务的安全载荷与执行闭环测试。"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_adapters import (
    NotificationAdapterError,
    NotificationAdapterRegistry,
    NotificationDeliveryCommand,
    NotificationDeliveryResult,
)
from cnb_application import (
    AlertEscalationPolicyDecision,
    AlertEscalationPolicyStep,
    AlertNotificationService,
    BackgroundTaskService,
    ChannelAlertEscalationCandidate,
    ChannelAlertLifecycleEvaluation,
    ConfigurationService,
    NotificationDeliveryTaskHandler,
    build_default_registry,
)
from cnb_domain import (
    ActiveAlert,
    AlertSeverity,
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    ChannelAlert,
    ChannelAlertLifecycle,
    ChannelAlertLifecycleStatus,
    ConfigEntry,
    ConfigScope,
    JsonValue,
    ObservabilityAlertLifecycle,
    ObservabilityAlertLifecycleStatus,
)
from cnb_infrastructure import (
    InMemoryTaskRepository,
    MemoryConfigurationRepository,
    MemoryObservabilityRepository,
    MemorySecretStore,
)


class RecordingAdapter:
    key = "webhook"
    display_name = "测试 Webhook"

    def __init__(self) -> None:
        self.commands: list[NotificationDeliveryCommand] = []

    async def deliver(
        self,
        *,
        command: NotificationDeliveryCommand,
        secret: str | None,
    ) -> NotificationDeliveryResult:
        self.commands.append(command)
        assert secret == "runtime-secret"
        return NotificationDeliveryResult(
            delivered=True,
            attempts=1,
            status_code=204,
            idempotency_key=command.idempotency_key,
            elapsed_ms=1,
        )

    async def aclose(self) -> None:
        return None


class FailingAdapter(RecordingAdapter):
    async def deliver(
        self,
        *,
        command: NotificationDeliveryCommand,
        secret: str | None,
    ) -> NotificationDeliveryResult:
        del command, secret
        raise NotificationAdapterError("transport_error", "测试投递失败", retryable=True)


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, JsonValue]] = []

    async def record_audit(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: str | None,
        detail: Mapping[str, JsonValue],
    ) -> None:
        del tenant_id, actor_id, action, resource_type, resource_id
        self.events.append(dict(detail))


class EmptyAlerts:
    async def alerts(self, **_: object) -> tuple[object, ...]:
        return ()

    async def reconcile_alert_lifecycles(self, **_: object) -> ChannelAlertLifecycleEvaluation:
        return ChannelAlertLifecycleEvaluation(
            alerts=(),
            active_lifecycles=(),
            due_escalations=(),
        )

    async def mark_alert_lifecycles_escalated(self, **_: object) -> tuple[object, ...]:
        return ()


class ActiveAlerts:
    def __init__(self, *, escalation_due: bool = False) -> None:
        self.escalation_due = escalation_due
        self.lifecycle_id = uuid4()
        self.escalation_calls: list[dict[str, object]] = []

    async def alerts(self, **_: object) -> tuple[ChannelAlert, ...]:
        now = datetime.now(UTC)
        return (
            ChannelAlert(
                channel_id=uuid4(),
                code="channel_error_rate",
                error_code="telegram_http_error",
                severity=AlertSeverity.CRITICAL,
                title="渠道异常",
                summary="安全摘要",
                occurrences=3,
                current_value=100.0,
                threshold_value=5.0,
                unit="%",
                first_occurred_at=now,
                last_occurred_at=now,
                cooldown_until=now,
            ),
        )

    async def reconcile_alert_lifecycles(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        now: datetime | None = None,
        **_: object,
    ) -> ChannelAlertLifecycleEvaluation:
        observed_at = now or datetime.now(UTC)
        alerts = await self.alerts()
        alert = alerts[0]
        lifecycle = ChannelAlertLifecycle(
            id=self.lifecycle_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=alert.channel_id,
            alert_key=alert.alert_key,
            code=alert.code,
            error_code=alert.error_code,
            status=ChannelAlertLifecycleStatus.ACTIVE,
            severity=alert.severity,
            occurrences=alert.occurrences,
            current_value=alert.current_value,
            threshold_value=alert.threshold_value,
            unit=alert.unit,
            first_occurred_at=observed_at - timedelta(hours=1),
            last_occurred_at=observed_at,
            last_evaluated_at=observed_at,
            escalated_at=None,
            resolved_at=None,
            recovery_duration_seconds=None,
            created_at=observed_at - timedelta(hours=1),
            updated_at=observed_at,
        )
        decision = AlertEscalationPolicyDecision(
            enabled=True,
            severity=alert.severity,
            duration_minutes=60,
            current_level=0,
            maximum_level=3,
            matched_level=1,
            target_level=1,
            adapter="webhook",
            on_call=True,
            evaluated_at=observed_at,
            local_time=observed_at,
            timezone="UTC",
            reason_code="eligible_for_escalation",
            reason="已达到下一升级等级并命中通知路由",
            steps=(
                AlertEscalationPolicyStep(
                    level=1,
                    threshold_minutes=30,
                    adapter="webhook",
                    eligible=True,
                    reached=True,
                    completed=False,
                ),
            ),
        )
        return ChannelAlertLifecycleEvaluation(
            alerts=alerts,
            active_lifecycles=(lifecycle,),
            due_escalations=(
                (ChannelAlertEscalationCandidate(lifecycle=lifecycle, decision=decision),)
                if self.escalation_due
                else ()
            ),
        )

    async def mark_alert_lifecycles_escalated(self, **values: object) -> tuple[object, ...]:
        self.escalation_calls.append(values)
        return ()


class FailingTasks:
    async def enqueue(self, **_: object) -> object:
        raise RuntimeError("任务真相源暂不可用")


def _notification_job(
    *,
    tenant_id: UUID,
    agent_id: UUID,
    status: BackgroundJobStatus,
    event: str,
    created_at: datetime,
    adapter: str = "webhook",
    alert_count: int = 1,
    alert_keys: tuple[str, ...] = (),
) -> BackgroundJob:
    job_id = uuid4()
    terminal = status in {
        BackgroundJobStatus.SUCCEEDED,
        BackgroundJobStatus.FAILED,
        BackgroundJobStatus.DEAD_LETTER,
        BackgroundJobStatus.CANCELED,
    }
    payload: dict[str, JsonValue] = {
        "agent_id": str(agent_id),
        "adapter": adapter,
        "delivery_payload": {
            "event": event,
            "alert_count": alert_count,
            "alerts": [{"alert_key": key} for key in alert_keys],
        },
        "idempotency_key": f"notification-{job_id}",
    }
    return BackgroundJob(
        id=job_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=BackgroundJobKind.NOTIFICATION_DELIVERY,
        queue="notification",
        status=status,
        payload=payload,
        deduplication_key=f"notification:{job_id}",
        source_inbox_id=None,
        correlation_id=None,
        attempt_count=2 if status is not BackgroundJobStatus.PENDING else 0,
        max_attempts=5,
        lease_seconds=120,
        retry_base_seconds=5,
        available_at=created_at,
        lease_owner=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        last_error_code=(
            "notification_transport_error"
            if status in {BackgroundJobStatus.FAILED, BackgroundJobStatus.DEAD_LETTER}
            else None
        ),
        last_error_summary=None,
        result_summary=(
            {"delivered": True, "status_code": 204, "elapsed_ms": 8}
            if status is BackgroundJobStatus.SUCCEEDED
            else {}
        ),
        replayed_from_id=None,
        created_by=None,
        created_at=created_at,
        started_at=created_at if status is not BackgroundJobStatus.PENDING else None,
        completed_at=created_at + timedelta(seconds=1) if terminal else None,
        updated_at=created_at + timedelta(seconds=1),
    )


async def _configuration(
    tenant_id: UUID,
    agent_id: UUID,
    *,
    recovery_enabled: bool | None = None,
    escalation_enabled: bool | None = None,
    escalation_after_minutes: int | None = None,
) -> ConfigurationService:
    del tenant_id
    repository = MemoryConfigurationRepository()
    service = ConfigurationService(build_default_registry(), repository)
    values = [
        ConfigEntry("alerts.notification.enabled", ConfigScope.AGENT, True, agent_id),
        ConfigEntry(
            "alerts.notification.webhook_url",
            ConfigScope.AGENT,
            "https://notify.example.invalid/hook",
            agent_id,
        ),
    ]
    if recovery_enabled is not None:
        values.append(
            ConfigEntry(
                "alerts.notification.recovery_enabled",
                ConfigScope.AGENT,
                recovery_enabled,
                agent_id,
            )
        )
    if escalation_enabled is not None:
        values.append(
            ConfigEntry(
                "alerts.notification.escalation_enabled",
                ConfigScope.AGENT,
                escalation_enabled,
                agent_id,
            )
        )
    if escalation_after_minutes is not None:
        values.append(
            ConfigEntry(
                "alerts.notification.escalation_after_minutes",
                ConfigScope.AGENT,
                escalation_after_minutes,
                agent_id,
            )
        )
    draft = await service.create_draft(
        note="通知队列测试",
        values=tuple(values),
    )
    await service.publish(draft.id)
    return service


@pytest.mark.asyncio
async def test_alert_notification_enqueue_does_not_store_target_or_secret() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=actor_id,
    )
    tasks = InMemoryTaskRepository()
    audit = RecordingAudit()
    service = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=audit,
        task_service=BackgroundTaskService(tasks),
    )
    result = await service.enqueue(
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_id=actor_id,
        confirmed=True,
    )
    assert result.created is True
    payload = result.job.payload
    assert "target" not in payload
    assert "secret" not in str(payload).casefold()
    assert "notify.example.invalid" not in str(payload)
    assert payload["adapter"] == "webhook"
    assert result.job.kind is BackgroundJobKind.NOTIFICATION_DELIVERY
    assert audit.events[0]["job_id"] == str(result.job.id)


@pytest.mark.asyncio
async def test_alert_notification_enqueue_if_active_skips_empty_alerts() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=actor_id,
    )
    service = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(InMemoryTaskRepository()),
    )

    assert (
        await service.enqueue_if_active(
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_alert_notification_enqueue_if_active_queues_active_alerts() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=actor_id,
    )
    tasks = InMemoryTaskRepository()
    service = AlertNotificationService(
        channel_service=ActiveAlerts(),  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(tasks),
    )

    result = await service.enqueue_if_active(
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )

    assert result is not None
    assert result.job.kind is BackgroundJobKind.NOTIFICATION_DELIVERY


@pytest.mark.asyncio
async def test_alert_notification_marks_escalation_only_after_task_is_enqueued() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    alerts = ActiveAlerts(escalation_due=True)
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=actor_id,
    )
    tasks = InMemoryTaskRepository()
    service = AlertNotificationService(
        channel_service=alerts,  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(tasks),
    )
    observed_at = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)

    result = await service.enqueue_if_active(
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_id=actor_id,
        now=observed_at,
    )

    assert result is not None
    delivery_payload = result.job.payload["delivery_payload"]
    assert isinstance(delivery_payload, dict)
    assert delivery_payload["event"] == "channel.alerts.escalated"
    assert len(tasks.jobs) == 1
    assert len(alerts.escalation_calls) == 1
    escalation_call = alerts.escalation_calls[0]
    assert escalation_call["tenant_id"] == tenant_id
    assert escalation_call["agent_id"] == agent_id
    assert escalation_call["lifecycle_ids"] == (alerts.lifecycle_id,)
    assert escalation_call["escalation_level"] == 1
    assert isinstance(escalation_call["escalated_at"], datetime)


@pytest.mark.asyncio
async def test_alert_notification_does_not_mark_escalation_when_enqueue_fails() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    alerts = ActiveAlerts(escalation_due=True)
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=actor_id,
    )
    service = AlertNotificationService(
        channel_service=alerts,  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=FailingTasks(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="真相源"):
        await service.enqueue_if_active(
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )

    assert alerts.escalation_calls == []


@pytest.mark.asyncio
async def test_alert_notification_timeline_is_isolated_and_counts_consecutive_failures() -> None:
    tenant_id, agent_id, other_agent_id = uuid4(), uuid4(), uuid4()
    other_tenant_id = uuid4()
    tasks = InMemoryTaskRepository()
    started_at = datetime(2026, 9, 14, 2, 0, tzinfo=UTC)
    statuses = (
        BackgroundJobStatus.SUCCEEDED,
        BackgroundJobStatus.FAILED,
        BackgroundJobStatus.RETRYING,
        BackgroundJobStatus.DEAD_LETTER,
    )
    events = (
        "observability.alerts.active",
        "channel.alerts.active",
        "observability.alerts.escalated",
        "observability.alerts.recovered",
    )
    own_jobs = [
        _notification_job(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=job_status,
            event=events[index],
            created_at=started_at + timedelta(minutes=index),
        )
        for index, job_status in enumerate(statuses)
    ]
    ignored_jobs = (
        _notification_job(
            tenant_id=tenant_id,
            agent_id=other_agent_id,
            status=BackgroundJobStatus.FAILED,
            event="channel.alerts.active",
            created_at=started_at + timedelta(minutes=10),
        ),
        _notification_job(
            tenant_id=other_tenant_id,
            agent_id=agent_id,
            status=BackgroundJobStatus.FAILED,
            event="channel.alerts.active",
            created_at=started_at + timedelta(minutes=11),
        ),
    )
    for job in (*own_jobs, *ignored_jobs):
        tasks.jobs[job.id] = job
    service = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=await _configuration(tenant_id, agent_id),
        secret_store=MemorySecretStore(),
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(tasks),
    )

    timeline = await service.timeline(tenant_id=tenant_id, agent_id=agent_id)

    assert timeline.total == 4
    assert timeline.succeeded == 1
    assert timeline.failed == 1
    assert timeline.retrying == 1
    assert timeline.dead_letters == 1
    assert timeline.current_consecutive_failures == 2
    assert [item.consecutive_failures for item in timeline.items] == [2, 1, 1, 0]
    assert [item.event for item in timeline.items] == [
        "recovery",
        "escalation",
        "active",
        "active",
    ]
    assert timeline.last_succeeded_at == own_jobs[0].completed_at
    assert not hasattr(timeline.items[0], "payload")

    filtered = await service.timeline(
        tenant_id=tenant_id,
        agent_id=agent_id,
        status=BackgroundJobStatus.FAILED,
        adapter="WEBHOOK",
        event="active",
    )
    assert [item.job_id for item in filtered.items] == [own_jobs[1].id]


@pytest.mark.asyncio
async def test_alert_notification_queues_a_single_recovery_for_last_successful_active_event() -> (
    None
):
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=actor_id,
    )
    tasks = InMemoryTaskRepository()
    active_job = _notification_job(
        tenant_id=tenant_id,
        agent_id=agent_id,
        status=BackgroundJobStatus.SUCCEEDED,
        event="channel.alerts.active",
        created_at=datetime(2026, 9, 14, 2, 0, tzinfo=UTC),
        alert_count=2,
        alert_keys=("channel-a:error-rate", "channel-b:latency"),
    )
    tasks.jobs[active_job.id] = active_job
    service = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(tasks),
    )
    detected_at = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)

    first = await service.enqueue_if_active(
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_id=actor_id,
        now=detected_at,
    )
    second = await service.enqueue_if_active(
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_id=actor_id,
        now=detected_at + timedelta(minutes=1),
    )

    assert first is not None
    assert first.created is True
    assert second is not None
    assert second.created is False
    assert second.job.id == first.job.id
    assert first.job.payload["delivery_payload"] == {
        "schema_version": "1",
        "event": "channel.alerts.recovered",
        "generated_at": detected_at.isoformat(),
        "alert_count": 2,
        "recovered_alert_keys": ["channel-a:error-rate", "channel-b:latency"],
    }
    assert str(active_job.id) not in str(first.job.payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recovery_enabled", "latest_event"),
    (
        (False, "channel.alerts.active"),
        (True, "channel.alerts.recovered"),
        (True, "unknown.event"),
    ),
)
async def test_alert_notification_skips_recovery_when_disabled_or_already_closed(
    recovery_enabled: bool,
    latest_event: str,
) -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    configuration = await _configuration(
        tenant_id,
        agent_id,
        recovery_enabled=recovery_enabled,
    )
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=actor_id,
    )
    tasks = InMemoryTaskRepository()
    previous = _notification_job(
        tenant_id=tenant_id,
        agent_id=agent_id,
        status=BackgroundJobStatus.SUCCEEDED,
        event=latest_event,
        created_at=datetime(2026, 9, 14, 2, 0, tzinfo=UTC),
    )
    tasks.jobs[previous.id] = previous
    service = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(tasks),
    )

    result = await service.enqueue_if_active(
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )

    assert result is None
    assert tuple(tasks.jobs.values()) == (previous,)


@pytest.mark.asyncio
async def test_notification_task_resolves_runtime_secret_and_completes() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=None,
    )
    adapter = RecordingAdapter()
    handler = NotificationDeliveryTaskHandler(
        NotificationAdapterRegistry((adapter,)),
        configuration,
        secrets,
    )
    now = datetime.now(UTC)
    job = BackgroundJob(
        id=uuid4(),
        tenant_id=tenant_id,
        kind=BackgroundJobKind.NOTIFICATION_DELIVERY,
        queue="notification",
        status=BackgroundJobStatus.PENDING,
        payload={
            "agent_id": str(agent_id),
            "adapter": "webhook",
            "delivery_payload": {"event": "safe", "alert_count": 0},
            "idempotency_key": "safe-idempotency",
            "timeout_seconds": 5,
            "max_retries": 1,
        },
        deduplication_key="test-notification",
        source_inbox_id=None,
        correlation_id=None,
        attempt_count=0,
        max_attempts=3,
        lease_seconds=60,
        retry_base_seconds=1,
        available_at=now,
        lease_owner=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        last_error_code=None,
        last_error_summary=None,
        result_summary={},
        replayed_from_id=None,
        created_by=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        updated_at=now,
    )
    result = await handler.handle(job)
    assert result["delivered"] is True
    assert adapter.commands[0].target == "https://notify.example.invalid/hook"
    assert adapter.commands[0].payload == {"event": "safe", "alert_count": 0}


async def _observability_lifecycle(
    repository: MemoryObservabilityRepository,
    *,
    tenant_id: UUID,
    agent_id: UUID,
    now: datetime,
) -> ObservabilityAlertLifecycle:
    result = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(_observability_alert(),),
        observed_at=now,
    )
    return result.active_lifecycles[0]


def _observability_alert() -> ActiveAlert:
    return ActiveAlert(
        code="api_error_rate",
        severity=AlertSeverity.CRITICAL,
        title="API 错误率",
        summary="安全摘要",
        current_value=20.0,
        threshold_value=1.0,
        unit="%",
        source_type="api",
        source_key="api_error_rate",
    )


async def test_observability_notifications_cover_one_complete_reactivation_cycle() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=None,
    )
    tasks = InMemoryTaskRepository()
    repository = MemoryObservabilityRepository()
    service = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(tasks),
    )
    started_at = datetime(2026, 9, 14, 12, tzinfo=UTC)
    lifecycle = await _observability_lifecycle(
        repository,
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=started_at,
    )

    first = await service.enqueue_observability_active(lifecycle=lifecycle, now=started_at)
    continued = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(_observability_alert(),),
        observed_at=started_at + timedelta(minutes=1),
    )
    duplicate = await service.enqueue_observability_active(
        lifecycle=continued.active_lifecycles[0],
        now=started_at + timedelta(minutes=1),
    )
    escalation = await service.enqueue_observability_escalation(
        lifecycle=continued.active_lifecycles[0],
        target_level=1,
        adapter_key="webhook",
        now=started_at + timedelta(minutes=1),
    )
    resolution = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(),
        observed_at=started_at + timedelta(minutes=2),
    )
    recovered = await service.enqueue_observability_recovery(
        lifecycle=resolution.recovered_lifecycles[0],
        now=started_at + timedelta(minutes=2),
    )
    duplicate_recovery = await service.enqueue_observability_recovery(
        lifecycle=resolution.recovered_lifecycles[0],
        now=started_at + timedelta(minutes=3),
    )
    reopened = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(_observability_alert(),),
        observed_at=started_at + timedelta(minutes=4),
    )
    reopened_notification = await service.enqueue_observability_active(
        lifecycle=reopened.activated_lifecycles[0],
        now=started_at + timedelta(minutes=4),
    )

    assert first is not None and first.created is True
    assert duplicate is not None and duplicate.created is False
    assert escalation is not None and escalation.created is True
    assert recovered is not None and recovered.created is True
    assert duplicate_recovery is not None and duplicate_recovery.created is False
    assert reopened_notification is not None and reopened_notification.created is True
    assert reopened.activated_lifecycles[0].id != lifecycle.id
    history = await repository.list_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
    )
    assert {item.id for item in history} == {lifecycle.id, reopened.activated_lifecycles[0].id}
    assert len(tasks.jobs) == 4
    events: set[object] = set()
    for job in tasks.jobs.values():
        delivery_payload = job.payload["delivery_payload"]
        assert isinstance(delivery_payload, dict)
        events.add(delivery_payload["event"])
    assert events == {
        "observability.alerts.active",
        "observability.alerts.escalated",
        "observability.alerts.recovered",
    }
    assert all("target" not in job.payload for job in tasks.jobs.values())
    assert all("secret" not in str(job.payload).casefold() for job in tasks.jobs.values())
    assert all("notify.example.invalid" not in str(job.payload) for job in tasks.jobs.values())


async def test_observability_recovery_notification_honors_runtime_setting() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    started_at = datetime(2026, 9, 14, 12, tzinfo=UTC)
    await _observability_lifecycle(
        repository,
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=started_at,
    )
    resolution = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(),
        observed_at=started_at + timedelta(minutes=1),
    )
    tasks = InMemoryTaskRepository()
    service = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=await _configuration(tenant_id, agent_id, recovery_enabled=False),
        secret_store=MemorySecretStore(),
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(tasks),
    )

    result = await service.enqueue_observability_recovery(
        lifecycle=resolution.recovered_lifecycles[0],
        now=started_at + timedelta(minutes=1),
    )

    assert result is None
    assert tasks.jobs == {}


async def test_observability_notification_idempotency_is_agent_isolated() -> None:
    tenant_id, agent_id, other_agent_id = uuid4(), uuid4(), uuid4()
    tasks = InMemoryTaskRepository()
    lifecycle = await _observability_lifecycle(
        MemoryObservabilityRepository(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=datetime(2026, 9, 14, 12, tzinfo=UTC),
    )

    async def enqueue_for(selected_lifecycle: ObservabilityAlertLifecycle) -> object:
        configuration = await _configuration(tenant_id, selected_lifecycle.agent_id)
        secrets = MemorySecretStore()
        await secrets.set_secret(
            key="alerts.notification.webhook_signing_secret",
            scope_type=ConfigScope.AGENT,
            scope_id=selected_lifecycle.agent_id,
            plaintext="runtime-secret",
            actor_id=None,
        )
        service = AlertNotificationService(
            channel_service=EmptyAlerts(),  # type: ignore[arg-type]
            configuration_service=configuration,
            secret_store=secrets,
            adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
            audit_recorder=RecordingAudit(),
            task_service=BackgroundTaskService(tasks),
        )
        return await service.enqueue_observability_active(lifecycle=selected_lifecycle)

    first = await enqueue_for(lifecycle)
    second = await enqueue_for(replace(lifecycle, agent_id=other_agent_id))

    assert first is not None
    assert second is not None
    assert len(tasks.jobs) == 2
    assert len({job.deduplication_key for job in tasks.jobs.values()}) == 2
    assert {job.agent_id for job in tasks.jobs.values()} == {agent_id, other_agent_id}


async def test_observability_escalation_enqueue_is_idempotent_and_redacts_delivery_details() -> (
    None
):
    tenant_id, agent_id = uuid4(), uuid4()
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=None,
    )
    tasks = InMemoryTaskRepository()
    lifecycle = await _observability_lifecycle(
        MemoryObservabilityRepository(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=datetime(2026, 9, 14, 12, tzinfo=UTC),
    )
    service = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(tasks),
    )

    first = await service.enqueue_observability_escalation(
        lifecycle=lifecycle,
        target_level=1,
        adapter_key="webhook",
    )
    second = await service.enqueue_observability_escalation(
        lifecycle=lifecycle,
        target_level=1,
        adapter_key="webhook",
    )

    assert first is not None and first.created is True
    assert second is not None and second.created is False
    assert len(tasks.jobs) == 1
    payload = first.job.payload
    assert "target" not in payload
    assert "secret" not in str(payload).casefold()
    assert "notify.example.invalid" not in str(payload)
    assert payload["observability_escalation_level"] == 1


async def test_observability_escalation_advances_only_after_successful_delivery() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    configuration = await _configuration(tenant_id, agent_id)
    secrets = MemorySecretStore()
    await secrets.set_secret(
        key="alerts.notification.webhook_signing_secret",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="runtime-secret",
        actor_id=None,
    )
    repository = MemoryObservabilityRepository()
    lifecycle = await _observability_lifecycle(
        repository,
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=datetime(2026, 9, 14, 12, tzinfo=UTC),
    )

    success_tasks = InMemoryTaskRepository()
    enqueue_service = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((RecordingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(success_tasks),
    )
    success = await enqueue_service.enqueue_observability_escalation(
        lifecycle=lifecycle,
        target_level=1,
        adapter_key="webhook",
    )
    assert success is not None
    handler = NotificationDeliveryTaskHandler(
        NotificationAdapterRegistry((RecordingAdapter(),)),
        configuration,
        secrets,
        repository,
    )
    await handler.handle(success.job)
    escalated = await repository.list_observability_alert_lifecycles(
        tenant_id=tenant_id, agent_id=agent_id
    )
    assert escalated[0].escalation_level == 1

    failing_tasks = InMemoryTaskRepository()
    failing_enqueue = AlertNotificationService(
        channel_service=EmptyAlerts(),  # type: ignore[arg-type]
        configuration_service=configuration,
        secret_store=secrets,
        adapter_registry=NotificationAdapterRegistry((FailingAdapter(),)),
        audit_recorder=RecordingAudit(),
        task_service=BackgroundTaskService(failing_tasks),
    )
    retry = await failing_enqueue.enqueue_observability_escalation(
        lifecycle=lifecycle,
        target_level=2,
        adapter_key="webhook",
    )
    assert retry is not None
    failing_handler = NotificationDeliveryTaskHandler(
        NotificationAdapterRegistry((FailingAdapter(),)),
        configuration,
        secrets,
        repository,
    )
    with pytest.raises(RuntimeError):
        await failing_handler.handle(retry.job)
    unchanged = await repository.list_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        status=ObservabilityAlertLifecycleStatus.ACTIVE,
    )
    assert unchanged[0].escalation_level == 1
