"""渠道连接探测的周期调度与安全任务处理。"""

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from cnb_application.channel_service import ChannelService
from cnb_application.configuration_service import ConfigurationService
from cnb_application.task_service import BackgroundTaskService, PermanentTaskError
from cnb_domain import BackgroundJob, BackgroundJobKind, ChannelInstance, JsonValue


class ChannelProbeRepository(Protocol):
    """Worker 使用的启用渠道候选查询端口。"""

    async def list_probe_candidates(self) -> tuple[ChannelInstance, ...]: ...


class ChannelConnectionProbeScheduler:
    """按渠道配置间隔创建去重的连接探测任务。"""

    def __init__(
        self,
        repository: ChannelProbeRepository,
        task_service: BackgroundTaskService,
        configuration: ConfigurationService,
    ) -> None:
        self._repository = repository
        self._task_service = task_service
        self._configuration = configuration

    async def schedule(self, *, now: datetime | None = None) -> int:
        ended_at = (now or datetime.now(UTC)).astimezone(UTC)
        scheduled = 0
        for channel in await self._repository.list_probe_candidates():
            snapshot = await self._configuration.resolve_effective(
                tenant_id=channel.tenant_id,
                agent_id=channel.agent_id,
                channel_id=channel.id,
            )
            interval = snapshot.values.get("channel.connection_probe_interval_minutes", 15)
            if (
                not isinstance(interval, int)
                or isinstance(interval, bool)
                or not 1 <= interval <= 1440
            ):
                raise ValueError("渠道连接探测间隔配置无效")
            if channel.last_checked_at is not None:
                checked_at = channel.last_checked_at.astimezone(UTC)
                if ended_at - checked_at < timedelta(minutes=interval):
                    continue
            bucket = int(ended_at.timestamp()) // (interval * 60)
            result = await self._task_service.enqueue(
                tenant_id=channel.tenant_id,
                kind=BackgroundJobKind.CHANNEL_CONNECTION_TEST,
                payload={
                    "agent_id": str(channel.agent_id),
                    "channel_id": str(channel.id),
                    "actor_id": str(channel.created_by),
                },
                deduplication_key=f"channel-probe:{channel.id}:{bucket}",
                created_by=channel.created_by,
            )
            scheduled += int(result.created)
        return scheduled


class ChannelConnectionProbeTaskHandler:
    """执行一次连接探测并仅返回健康状态摘要。"""

    def __init__(self, channel_service: ChannelService) -> None:
        self._channel_service = channel_service

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        try:
            agent_id = UUID(self._required(job.payload, "agent_id"))
            channel_id = UUID(self._required(job.payload, "channel_id"))
            actor_id = UUID(self._required(job.payload, "actor_id"))
        except (ValueError, TypeError) as error:
            raise PermanentTaskError("连接探测任务载荷无效") from error
        result = await self._channel_service.test_connection(
            tenant_id=job.tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            actor_id=actor_id,
        )
        return {
            "channel_id": str(channel_id),
            "health_status": result.instance.health_status.value,
            "checked_at": result.instance.last_checked_at.isoformat()
            if result.instance.last_checked_at is not None
            else None,
        }

    @staticmethod
    def _required(payload: dict[str, JsonValue], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(key)
        return value


__all__ = [
    "ChannelConnectionProbeScheduler",
    "ChannelConnectionProbeTaskHandler",
    "ChannelProbeRepository",
]
