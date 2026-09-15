"""通用告警通知任务重放前的抑制状态复核。"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID, uuid4

from cnb_application.observability_service import ObservabilityRepository
from cnb_application.task_service import TaskConflictError, TaskReplayGuard
from cnb_domain import (
    BackgroundJob,
    BackgroundJobKind,
    ObservabilityAlertDispositionStatus,
    ObservabilityAlertReplayDecision,
    ObservabilityAlertReplayReason,
    ObservabilityAlertReplayReview,
)


class ObservabilityNotificationReplayGuard(TaskReplayGuard):
    """阻止仍处于通用告警抑制期的通知任务被管理员重放。"""

    def __init__(self, repository: ObservabilityRepository) -> None:
        self._repository = repository

    async def check(self, *, job: BackgroundJob, actor_id: UUID, now: datetime) -> None:
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
            or len(source_type.strip()) > 80
            or len(source_key.strip()) > 255
        ):
            await self._record(
                job=job,
                actor_id=actor_id,
                agent_id=job.agent_id,
                source_type=(
                    source_type.strip()
                    if isinstance(source_type, str) and len(source_type.strip()) <= 80
                    else None
                ),
                source_key=(
                    source_key.strip()
                    if isinstance(source_key, str) and len(source_key.strip()) <= 255
                    else None
                ),
                decision=ObservabilityAlertReplayDecision.BLOCKED,
                reason_code=ObservabilityAlertReplayReason.BLOCKED_MISSING_SOURCE,
                suppression_expires_at=None,
                now=now,
            )
            raise TaskConflictError("通用告警通知缺少可复核来源，不能安全重放")
        normalized_source_type = source_type.strip()
        normalized_source_key = source_key.strip()
        payload_agent_id = job.payload.get("agent_id")
        try:
            parsed_agent_id = UUID(str(payload_agent_id))
        except (TypeError, ValueError) as error:
            await self._record(
                job=job,
                actor_id=actor_id,
                agent_id=job.agent_id,
                source_type=normalized_source_type,
                source_key=normalized_source_key,
                decision=ObservabilityAlertReplayDecision.BLOCKED,
                reason_code=ObservabilityAlertReplayReason.BLOCKED_INVALID_AGENT,
                suppression_expires_at=None,
                now=now,
            )
            raise TaskConflictError("通用告警通知缺少 Agent 归属，不能安全重放") from error
        if job.agent_id is not None and parsed_agent_id != job.agent_id:
            await self._record(
                job=job,
                actor_id=actor_id,
                agent_id=job.agent_id,
                source_type=normalized_source_type,
                source_key=normalized_source_key,
                decision=ObservabilityAlertReplayDecision.BLOCKED,
                reason_code=ObservabilityAlertReplayReason.BLOCKED_AGENT_MISMATCH,
                suppression_expires_at=None,
                now=now,
            )
            raise TaskConflictError("通用告警通知 Agent 归属不一致，不能安全重放")
        agent_id = job.agent_id or parsed_agent_id
        disposition = await self._repository.get_observability_alert_disposition(
            tenant_id=job.tenant_id,
            agent_id=agent_id,
            source_type=normalized_source_type,
            source_key=normalized_source_key,
        )
        if (
            disposition is not None
            and disposition.status is ObservabilityAlertDispositionStatus.SUPPRESSED
            and disposition.expires_at is not None
            and disposition.expires_at > now
        ):
            await self._record(
                job=job,
                actor_id=actor_id,
                agent_id=agent_id,
                source_type=normalized_source_type,
                source_key=normalized_source_key,
                decision=ObservabilityAlertReplayDecision.BLOCKED,
                reason_code=ObservabilityAlertReplayReason.BLOCKED_ACTIVE_SUPPRESSION,
                suppression_expires_at=disposition.expires_at,
                now=now,
            )
            raise TaskConflictError("当前告警仍处于抑制期，不能重放通知任务")
        reason_code = (
            ObservabilityAlertReplayReason.ALLOWED_SUPPRESSION_EXPIRED
            if disposition is not None
            and disposition.status is ObservabilityAlertDispositionStatus.SUPPRESSED
            and disposition.expires_at is not None
            else ObservabilityAlertReplayReason.ALLOWED_NO_SUPPRESSION
        )
        await self._record(
            job=job,
            actor_id=actor_id,
            agent_id=agent_id,
            source_type=normalized_source_type,
            source_key=normalized_source_key,
            decision=ObservabilityAlertReplayDecision.ALLOWED,
            reason_code=reason_code,
            suppression_expires_at=(disposition.expires_at if disposition is not None else None),
            now=now,
        )

    async def _record(
        self,
        *,
        job: BackgroundJob,
        actor_id: UUID,
        agent_id: UUID | None,
        source_type: str | None,
        source_key: str | None,
        decision: ObservabilityAlertReplayDecision,
        reason_code: ObservabilityAlertReplayReason,
        suppression_expires_at: datetime | None,
        now: datetime,
    ) -> None:
        await self._repository.record_observability_alert_replay_review(
            ObservabilityAlertReplayReview(
                id=uuid4(),
                tenant_id=job.tenant_id,
                agent_id=agent_id,
                source_job_id=job.id,
                source_type=source_type,
                source_key=source_key,
                decision=decision,
                reason_code=reason_code,
                actor_id=actor_id,
                suppression_expires_at=suppression_expires_at,
                reviewed_at=now,
            )
        )


__all__ = ["ObservabilityNotificationReplayGuard"]
