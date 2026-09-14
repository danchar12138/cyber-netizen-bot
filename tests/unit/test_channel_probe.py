"""渠道连接探测调度与任务处理测试。"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cnb_application import (
    BackgroundTaskService,
    ChannelConnectionProbeScheduler,
    ChannelConnectionProbeTaskHandler,
    ChannelInstanceView,
    ConfigurationService,
    PermanentTaskError,
    build_default_registry,
)
from cnb_domain import (
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    ChannelCapabilities,
    ChannelHealthStatus,
    ChannelInstance,
    ChannelInstanceStatus,
    ChannelPlatform,
    JsonValue,
)
from cnb_infrastructure import (
    InMemoryTaskRepository,
    MemoryChannelRepository,
    MemoryConfigurationRepository,
)


def _channel(
    *,
    status: ChannelInstanceStatus = ChannelInstanceStatus.ENABLED,
    last_checked_at: datetime | None = None,
) -> ChannelInstance:
    now = datetime.now(UTC)
    return ChannelInstance(
        id=uuid4(),
        tenant_id=uuid4(),
        agent_id=uuid4(),
        name="探测渠道",
        platform=ChannelPlatform.TELEGRAM,
        status=status,
        rate_limit_per_minute=60,
        settings={},
        health_status=ChannelHealthStatus.NOT_CONFIGURED,
        health_detail=None,
        last_checked_at=last_checked_at,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )


def _configuration() -> ConfigurationService:
    return ConfigurationService(build_default_registry(), MemoryConfigurationRepository())


async def test_scheduler_creates_one_job_for_each_enabled_channel() -> None:
    channel = _channel()
    repository = MemoryChannelRepository()
    repository.instances[channel.id] = channel
    tasks = BackgroundTaskService(InMemoryTaskRepository())
    scheduler = ChannelConnectionProbeScheduler(repository, tasks, _configuration())
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)

    assert await scheduler.schedule(now=now) == 1
    jobs = await tasks.list_jobs(tenant_id=channel.tenant_id, status=None, kind=None, limit=10)
    assert len(jobs) == 1
    assert jobs[0].kind is BackgroundJobKind.CHANNEL_CONNECTION_TEST
    assert jobs[0].queue == "channel"
    assert jobs[0].payload == {
        "agent_id": str(channel.agent_id),
        "channel_id": str(channel.id),
        "actor_id": str(channel.created_by),
    }


async def test_scheduler_deduplicates_bucket_and_skips_recent_or_disabled_channels() -> None:
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    recent = _channel(last_checked_at=now - timedelta(minutes=1))
    disabled = _channel(status=ChannelInstanceStatus.DISABLED)
    due = _channel(last_checked_at=now - timedelta(minutes=16))
    repository = MemoryChannelRepository()
    repository.instances.update({item.id: item for item in (recent, disabled, due)})
    tasks = BackgroundTaskService(InMemoryTaskRepository())
    scheduler = ChannelConnectionProbeScheduler(repository, tasks, _configuration())

    assert await scheduler.schedule(now=now) == 1
    assert await scheduler.schedule(now=now) == 0
    jobs = await tasks.list_jobs(tenant_id=due.tenant_id, status=None, kind=None, limit=10)
    assert len(jobs) == 1
    assert jobs[0].payload["channel_id"] == str(due.id)


class _FixedConfiguration:
    async def resolve_effective(self, **_: object) -> object:
        return type(
            "Snapshot",
            (),
            {"values": {"channel.connection_probe_interval_minutes": "invalid"}},
        )()


async def test_scheduler_rejects_invalid_interval() -> None:
    channel = _channel()
    repository = MemoryChannelRepository()
    repository.instances[channel.id] = channel
    scheduler = ChannelConnectionProbeScheduler(
        repository,
        BackgroundTaskService(InMemoryTaskRepository()),
        _FixedConfiguration(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="探测间隔配置无效"):
        await scheduler.schedule(now=datetime.now(UTC))


class _RecordingChannelService:
    def __init__(self, result: ChannelInstanceView) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def test_connection(self, **kwargs: object) -> ChannelInstanceView:
        self.calls.append(kwargs)
        return self.result


class _RecordingNotifications:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def enqueue_if_active(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _job(channel: ChannelInstance, payload: Mapping[str, JsonValue]) -> BackgroundJob:
    now = datetime.now(UTC)
    return BackgroundJob(
        id=uuid4(),
        tenant_id=channel.tenant_id,
        kind=BackgroundJobKind.CHANNEL_CONNECTION_TEST,
        queue="channel",
        status=BackgroundJobStatus.PENDING,
        payload=dict(payload),
        deduplication_key="probe:test",
        source_inbox_id=None,
        correlation_id=None,
        attempt_count=0,
        max_attempts=1,
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
        created_by=channel.created_by,
        created_at=now,
        started_at=None,
        completed_at=None,
        updated_at=now,
    )


@pytest.mark.parametrize(
    "health",
    [ChannelHealthStatus.HEALTHY, ChannelHealthStatus.NOT_CONFIGURED],
)
async def test_handler_returns_safe_health_summary(health: ChannelHealthStatus) -> None:
    channel = _channel()
    checked_at = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    instance = replace(channel, health_status=health, last_checked_at=checked_at)
    view = ChannelInstanceView(
        instance=instance,
        display_name="Telegram",
        implementation_status="ready",
        credential_configured=health is not ChannelHealthStatus.NOT_CONFIGURED,
        inbound_webhook_configured=False,
        capabilities=ChannelCapabilities(text=True),
    )
    service = _RecordingChannelService(view)
    notifications = _RecordingNotifications()
    handler = ChannelConnectionProbeTaskHandler(
        service,  # type: ignore[arg-type]
        notifications,  # type: ignore[arg-type]
    )
    result = await handler.handle(
        _job(
            channel,
            {
                "agent_id": str(channel.agent_id),
                "channel_id": str(channel.id),
                "actor_id": str(channel.created_by),
            },
        )
    )

    assert result == {
        "channel_id": str(channel.id),
        "health_status": health.value,
        "checked_at": checked_at.isoformat(),
    }
    assert "credential" not in repr(result).lower()
    assert "secret" not in repr(result).lower()
    assert notifications.calls[0]["agent_id"] == channel.agent_id


async def test_handler_rejects_malformed_payload_permanently() -> None:
    channel = _channel()
    view = ChannelInstanceView(
        instance=channel,
        display_name="Telegram",
        implementation_status="ready",
        credential_configured=False,
        inbound_webhook_configured=False,
        capabilities=ChannelCapabilities(),
    )
    handler = ChannelConnectionProbeTaskHandler(_RecordingChannelService(view))  # type: ignore[arg-type]

    with pytest.raises(PermanentTaskError, match="载荷无效"):
        await handler.handle(_job(channel, {"channel_id": str(channel.id)}))
