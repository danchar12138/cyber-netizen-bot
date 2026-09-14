"""通知队列任务的安全载荷与执行闭环测试。"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_adapters import (
    NotificationAdapterRegistry,
    NotificationDeliveryCommand,
    NotificationDeliveryResult,
)
from cnb_application import (
    AlertNotificationService,
    BackgroundTaskService,
    ConfigurationService,
    NotificationDeliveryTaskHandler,
    build_default_registry,
)
from cnb_domain import (
    AlertSeverity,
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    ChannelAlert,
    ConfigEntry,
    ConfigScope,
    JsonValue,
)
from cnb_infrastructure import (
    InMemoryTaskRepository,
    MemoryConfigurationRepository,
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


class ActiveAlerts:
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
                last_occurred_at=now,
                cooldown_until=now,
            ),
        )


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
    own_jobs = [
        _notification_job(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=job_status,
            event="channel.alerts.active",
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
