"""通知队列任务的安全载荷与执行闭环测试。"""

from collections.abc import Mapping
from datetime import UTC, datetime
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
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
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


async def _configuration(
    tenant_id: UUID,
    agent_id: UUID,
) -> ConfigurationService:
    repository = MemoryConfigurationRepository()
    service = ConfigurationService(build_default_registry(), repository)
    draft = await service.create_draft(
        note="通知队列测试",
        values=(
            ConfigEntry("alerts.notification.enabled", ConfigScope.AGENT, True, agent_id),
            ConfigEntry(
                "alerts.notification.webhook_url",
                ConfigScope.AGENT,
                "https://notify.example.invalid/hook",
                agent_id,
            ),
        ),
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
