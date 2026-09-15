"""通用告警通知任务重放前的抑制状态复核。"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from cnb_application.observability_service import ObservabilityRepository
from cnb_application.task_service import TaskConflictError, TaskReplayGuard
from cnb_domain import BackgroundJob, BackgroundJobKind, ObservabilityAlertDispositionStatus


class ObservabilityNotificationReplayGuard(TaskReplayGuard):
    """阻止仍处于通用告警抑制期的通知任务被管理员重放。"""

    def __init__(self, repository: ObservabilityRepository) -> None:
        self._repository = repository

    async def check(self, *, job: BackgroundJob, now: datetime) -> None:
        if job.kind is not BackgroundJobKind.NOTIFICATION_DELIVERY:
            return
        payload = job.payload.get("delivery_payload")
        if not isinstance(payload, Mapping):
            return
        event = payload.get("event")
        if not isinstance(event, str) or not event.startswith("observability.alerts."):
            return
        source_type = payload.get("source_type")
        source_key = payload.get("source_key")
        if (
            not isinstance(source_type, str)
            or not isinstance(source_key, str)
            or not source_type.strip()
            or not source_key.strip()
        ):
            raise TaskConflictError("通用告警通知缺少可复核来源，不能安全重放")
        payload_agent_id = job.payload.get("agent_id")
        try:
            parsed_agent_id = UUID(str(payload_agent_id))
        except (TypeError, ValueError) as error:
            raise TaskConflictError("通用告警通知缺少 Agent 归属，不能安全重放") from error
        if job.agent_id is not None and parsed_agent_id != job.agent_id:
            raise TaskConflictError("通用告警通知 Agent 归属不一致，不能安全重放")
        agent_id = job.agent_id or parsed_agent_id
        disposition = await self._repository.get_observability_alert_disposition(
            tenant_id=job.tenant_id,
            agent_id=agent_id,
            source_type=source_type.strip(),
            source_key=source_key.strip(),
        )
        if (
            disposition is not None
            and disposition.status is ObservabilityAlertDispositionStatus.SUPPRESSED
            and disposition.expires_at is not None
            and disposition.expires_at > now
        ):
            raise TaskConflictError("当前告警仍处于抑制期，不能重放通知任务")


__all__ = ["ObservabilityNotificationReplayGuard"]
