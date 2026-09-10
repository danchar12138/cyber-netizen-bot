"""可恢复任务、Inbox/Outbox、调度与 Worker 心跳仓储。"""

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_cognition import ProactiveDecision
from cnb_domain import (
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    InboxEvent,
    InboxEventStatus,
    JobAttempt,
    JobAttemptStatus,
    JobClaim,
    JsonValue,
    OutboxEvent,
    OutboxEventStatus,
    ScheduledAction,
    ScheduledActionKind,
    ScheduledActionStatus,
    SocialBudgetUsage,
    TaskCounts,
    WorkerHeartbeat,
)
from cnb_infrastructure.models import (
    AuditLog,
    BackgroundJobModel,
    InboxEventModel,
    JobAttemptModel,
    OutboxEventModel,
    ScheduledActionModel,
    SocialBudgetUsageModel,
    WorkerHeartbeatModel,
)

_JOB_TERMINAL = {
    BackgroundJobStatus.SUCCEEDED,
    BackgroundJobStatus.FAILED,
    BackgroundJobStatus.DEAD_LETTER,
    BackgroundJobStatus.CANCELED,
}
_OUTBOX_CLAIMABLE = {OutboxEventStatus.PENDING, OutboxEventStatus.RETRYING}


class InMemoryTaskRepository:
    """无需 Redis/PostgreSQL 的并发安全任务仓储，用于测试和前端联调。"""

    def __init__(self) -> None:
        self.inbox_events: dict[UUID, InboxEvent] = {}
        self.jobs: dict[UUID, BackgroundJob] = {}
        self.outbox_events: dict[UUID, OutboxEvent] = {}
        self.attempts: dict[UUID, JobAttempt] = {}
        self.scheduled_actions: dict[UUID, ScheduledAction] = {}
        self.budget_usages: dict[UUID, SocialBudgetUsage] = {}
        self.heartbeats: dict[str, WorkerHeartbeat] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        *,
        job: BackgroundJob,
        outbox: OutboxEvent,
        inbox: InboxEvent | None,
    ) -> tuple[BackgroundJob, bool]:
        async with self._lock:
            existing = self._job_by_deduplication(job.tenant_id, job.deduplication_key)
            if existing is not None:
                return existing, False
            if inbox is not None:
                duplicate = next(
                    (
                        item
                        for item in self.inbox_events.values()
                        if item.tenant_id == inbox.tenant_id and item.event_key == inbox.event_key
                    ),
                    None,
                )
                if duplicate is not None:
                    return self.jobs[duplicate.job_id], False
                self.inbox_events[inbox.id] = inbox
            self.jobs[job.id] = job
            self.outbox_events[outbox.id] = outbox
            return job, True

    async def get_job(self, *, tenant_id: UUID, job_id: UUID) -> BackgroundJob | None:
        async with self._lock:
            item = self.jobs.get(job_id)
            return item if item is not None and item.tenant_id == tenant_id else None

    async def get_inbox_event(self, *, tenant_id: UUID, inbox_id: UUID) -> InboxEvent | None:
        async with self._lock:
            item = self.inbox_events.get(inbox_id)
            return item if item is not None and item.tenant_id == tenant_id else None

    async def list_inbox_events(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: InboxEventStatus | None,
        channel_id: UUID | None,
        limit: int,
    ) -> tuple[InboxEvent, ...]:
        async with self._lock:
            rows = [
                item
                for item in self.inbox_events.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (status is None or item.status is status)
                and (channel_id is None or item.channel_id == channel_id)
            ]
            rows.sort(key=lambda item: (item.received_at, str(item.id)), reverse=True)
            return tuple(rows[:limit])

    async def list_jobs(
        self,
        *,
        tenant_id: UUID,
        status: BackgroundJobStatus | None,
        kind: BackgroundJobKind | None,
        limit: int,
    ) -> tuple[BackgroundJob, ...]:
        async with self._lock:
            rows = [
                item
                for item in self.jobs.values()
                if item.tenant_id == tenant_id
                and (status is None or item.status is status)
                and (kind is None or item.kind is kind)
            ]
            rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
            return tuple(rows[:limit])

    async def list_attempts(self, *, tenant_id: UUID, job_id: UUID) -> tuple[JobAttempt, ...]:
        async with self._lock:
            rows = [
                item
                for item in self.attempts.values()
                if item.tenant_id == tenant_id and item.job_id == job_id
            ]
            rows.sort(key=lambda item: item.attempt_number)
            return tuple(rows)

    async def get_counts(self, *, tenant_id: UUID) -> TaskCounts:
        async with self._lock:
            jobs = tuple(item for item in self.jobs.values() if item.tenant_id == tenant_id)
            return TaskCounts(
                pending=sum(item.status is BackgroundJobStatus.PENDING for item in jobs),
                running=sum(item.status is BackgroundJobStatus.RUNNING for item in jobs),
                retrying=sum(item.status is BackgroundJobStatus.RETRYING for item in jobs),
                dead_letters=sum(item.status is BackgroundJobStatus.DEAD_LETTER for item in jobs),
                scheduled=sum(
                    item.tenant_id == tenant_id and item.status is ScheduledActionStatus.PENDING
                    for item in self.scheduled_actions.values()
                ),
            )

    async def request_cancel(
        self, *, tenant_id: UUID, job_id: UUID, actor_id: UUID, now: datetime
    ) -> BackgroundJob | None:
        del actor_id
        async with self._lock:
            item = self.jobs.get(job_id)
            if item is None or item.tenant_id != tenant_id:
                return None
            if item.status in _JOB_TERMINAL:
                return item
            status = (
                BackgroundJobStatus.RUNNING
                if item.status is BackgroundJobStatus.RUNNING
                else BackgroundJobStatus.CANCELED
            )
            updated = replace(
                item,
                status=status,
                cancel_requested_at=now,
                completed_at=now if status is BackgroundJobStatus.CANCELED else None,
                updated_at=now,
            )
            self.jobs[job_id] = updated
            if status is BackgroundJobStatus.CANCELED:
                self._cancel_outbox(job_id, now)
                self._set_inbox_status(updated, InboxEventStatus.CANCELED, now=now)
            return updated

    async def replay(
        self,
        *,
        tenant_id: UUID,
        source_job_id: UUID,
        job: BackgroundJob,
        outbox: OutboxEvent,
        actor_id: UUID,
        reason: str,
    ) -> BackgroundJob | None:
        del actor_id, reason
        async with self._lock:
            source = self.jobs.get(source_job_id)
            if source is None or source.tenant_id != tenant_id:
                return None
            self.jobs[job.id] = job
            self.outbox_events[outbox.id] = outbox
            return job

    async def claim_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> JobClaim | None:
        async with self._lock:
            item = self.jobs.get(job_id)
            if (
                item is None
                or item.status not in {BackgroundJobStatus.PENDING, BackgroundJobStatus.RETRYING}
                or item.available_at > now
                or item.attempt_count >= item.max_attempts
            ):
                return None
            if item.cancel_requested_at is not None:
                canceled = replace(
                    item,
                    status=BackgroundJobStatus.CANCELED,
                    completed_at=now,
                    updated_at=now,
                )
                self.jobs[job_id] = canceled
                self._set_inbox_status(canceled, InboxEventStatus.CANCELED, now=now)
                return None
            attempt_number = item.attempt_count + 1
            running = replace(
                item,
                status=BackgroundJobStatus.RUNNING,
                attempt_count=attempt_number,
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=item.lease_seconds),
                started_at=item.started_at or now,
                updated_at=now,
            )
            attempt = JobAttempt(
                id=uuid4(),
                tenant_id=item.tenant_id,
                job_id=item.id,
                attempt_number=attempt_number,
                status=JobAttemptStatus.RUNNING,
                worker_id=worker_id,
                started_at=now,
                completed_at=None,
                error_code=None,
                error_summary=None,
            )
            self.jobs[job_id] = running
            self.attempts[attempt.id] = attempt
            self._set_inbox_status(running, InboxEventStatus.PROCESSING, now=now)
            return JobClaim(running, attempt)

    async def renew_job_lease(
        self,
        *,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> bool:
        async with self._lock:
            job = self.jobs.get(job_id)
            attempt = self.attempts.get(attempt_id)
            if (
                job is None
                or attempt is None
                or job.status is not BackgroundJobStatus.RUNNING
                or job.lease_owner != worker_id
                or attempt.status is not JobAttemptStatus.RUNNING
                or attempt.worker_id != worker_id
            ):
                return False
            self.jobs[job_id] = replace(
                job,
                lease_expires_at=now + timedelta(seconds=job.lease_seconds),
                updated_at=now,
            )
            return True

    async def complete_job(
        self,
        *,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        result_summary: dict[str, JsonValue],
        now: datetime,
    ) -> BackgroundJob | None:
        async with self._lock:
            item = self.jobs.get(job_id)
            attempt = self.attempts.get(attempt_id)
            if (
                item is None
                or attempt is None
                or item.status is not BackgroundJobStatus.RUNNING
                or item.lease_owner != worker_id
                or attempt.status is not JobAttemptStatus.RUNNING
            ):
                return None
            canceled = item.cancel_requested_at is not None
            final_status = (
                BackgroundJobStatus.CANCELED if canceled else BackgroundJobStatus.SUCCEEDED
            )
            attempt_status = JobAttemptStatus.CANCELED if canceled else JobAttemptStatus.SUCCEEDED
            updated = replace(
                item,
                status=final_status,
                result_summary=result_summary if not canceled else {},
                lease_owner=None,
                lease_expires_at=None,
                completed_at=now,
                updated_at=now,
            )
            self.jobs[job_id] = updated
            self.attempts[attempt_id] = replace(attempt, status=attempt_status, completed_at=now)
            self._set_inbox_status(
                updated,
                InboxEventStatus.CANCELED if canceled else InboxEventStatus.COMPLETED,
                now=now,
            )
            return updated

    async def fail_job(
        self,
        *,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        error_code: str,
        error_summary: str,
        permanent: bool,
        timed_out: bool,
        retry_at: datetime,
        retry_outbox: OutboxEvent,
        now: datetime,
    ) -> BackgroundJob | None:
        async with self._lock:
            item = self.jobs.get(job_id)
            attempt = self.attempts.get(attempt_id)
            if (
                item is None
                or attempt is None
                or item.status is not BackgroundJobStatus.RUNNING
                or item.lease_owner != worker_id
                or attempt.status is not JobAttemptStatus.RUNNING
            ):
                return None
            if item.cancel_requested_at is not None:
                status = BackgroundJobStatus.CANCELED
            elif permanent:
                status = BackgroundJobStatus.FAILED
            elif item.attempt_count >= item.max_attempts:
                status = BackgroundJobStatus.DEAD_LETTER
            else:
                status = BackgroundJobStatus.RETRYING
            updated = replace(
                item,
                status=status,
                available_at=retry_at
                if status is BackgroundJobStatus.RETRYING
                else item.available_at,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=error_code,
                last_error_summary=error_summary,
                completed_at=now if status in _JOB_TERMINAL else None,
                updated_at=now,
            )
            self.jobs[job_id] = updated
            attempt_status = (
                JobAttemptStatus.CANCELED
                if status is BackgroundJobStatus.CANCELED
                else JobAttemptStatus.TIMED_OUT
                if timed_out
                else JobAttemptStatus.FAILED
            )
            self.attempts[attempt_id] = replace(
                attempt,
                status=attempt_status,
                completed_at=now,
                error_code=error_code,
                error_summary=error_summary,
            )
            if status is BackgroundJobStatus.RETRYING:
                self.outbox_events[retry_outbox.id] = retry_outbox
                self._set_inbox_status(updated, InboxEventStatus.PENDING, now=now)
            elif status in {BackgroundJobStatus.FAILED, BackgroundJobStatus.DEAD_LETTER}:
                self._set_inbox_status(
                    updated,
                    InboxEventStatus.DEAD_LETTER,
                    now=now,
                    error_code=error_code,
                )
            else:
                self._set_inbox_status(updated, InboxEventStatus.CANCELED, now=now)
            return updated

    async def claim_outbox(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[OutboxEvent, ...]:
        async with self._lock:
            rows = sorted(
                (
                    item
                    for item in self.outbox_events.values()
                    if item.status in _OUTBOX_CLAIMABLE
                    and item.available_at <= now
                    and item.attempt_count < item.max_attempts
                ),
                key=lambda item: (item.available_at, item.created_at, str(item.id)),
            )[:limit]
            claimed: list[OutboxEvent] = []
            for item in rows:
                updated = replace(
                    item,
                    status=OutboxEventStatus.PUBLISHING,
                    attempt_count=item.attempt_count + 1,
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
                self.outbox_events[item.id] = updated
                claimed.append(updated)
            return tuple(claimed)

    async def mark_outbox_published(self, *, event_id: UUID, worker_id: str, now: datetime) -> None:
        async with self._lock:
            item = self.outbox_events.get(event_id)
            if (
                item is None
                or item.status is not OutboxEventStatus.PUBLISHING
                or item.lease_owner != worker_id
            ):
                return
            self.outbox_events[event_id] = replace(
                item,
                status=OutboxEventStatus.PUBLISHED,
                lease_owner=None,
                lease_expires_at=None,
                published_at=now,
                updated_at=now,
            )

    async def mark_outbox_failed(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> None:
        async with self._lock:
            item = self.outbox_events.get(event_id)
            if (
                item is None
                or item.status is not OutboxEventStatus.PUBLISHING
                or item.lease_owner != worker_id
            ):
                return
            exhausted = item.attempt_count >= item.max_attempts
            self.outbox_events[event_id] = replace(
                item,
                status=(OutboxEventStatus.DEAD_LETTER if exhausted else OutboxEventStatus.RETRYING),
                available_at=retry_at,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=error_code,
                updated_at=now,
            )

    async def recover_expired_leases(self, *, now: datetime) -> int:
        async with self._lock:
            recovered = 0
            for job_id, item in tuple(self.jobs.items()):
                if (
                    item.status is BackgroundJobStatus.RUNNING
                    and item.lease_expires_at is not None
                    and item.lease_expires_at <= now
                ):
                    status = (
                        BackgroundJobStatus.DEAD_LETTER
                        if item.attempt_count >= item.max_attempts
                        else BackgroundJobStatus.RETRYING
                    )
                    updated = replace(
                        item,
                        status=status,
                        available_at=now,
                        lease_owner=None,
                        lease_expires_at=None,
                        last_error_code="LeaseExpired",
                        last_error_summary="Worker 租约过期，任务已由恢复器接管。",
                        completed_at=now if status is BackgroundJobStatus.DEAD_LETTER else None,
                        updated_at=now,
                    )
                    self.jobs[job_id] = updated
                    for attempt_id, attempt in tuple(self.attempts.items()):
                        if attempt.job_id == job_id and attempt.status is JobAttemptStatus.RUNNING:
                            self.attempts[attempt_id] = replace(
                                attempt,
                                status=JobAttemptStatus.TIMED_OUT,
                                completed_at=now,
                                error_code="LeaseExpired",
                                error_summary="Worker 租约过期。",
                            )
                    if status is BackgroundJobStatus.RETRYING:
                        outbox = self.retry_outbox(updated, now)
                        self.outbox_events[outbox.id] = outbox
                        self._set_inbox_status(updated, InboxEventStatus.PENDING, now=now)
                    else:
                        self._set_inbox_status(
                            updated,
                            InboxEventStatus.DEAD_LETTER,
                            now=now,
                            error_code="LeaseExpired",
                        )
                    recovered += 1
            for event_id, item in tuple(self.outbox_events.items()):
                if (
                    item.status is OutboxEventStatus.PUBLISHING
                    and item.lease_expires_at is not None
                    and item.lease_expires_at <= now
                ):
                    self.outbox_events[event_id] = replace(
                        item,
                        status=(
                            OutboxEventStatus.DEAD_LETTER
                            if item.attempt_count >= item.max_attempts
                            else OutboxEventStatus.RETRYING
                        ),
                        available_at=now,
                        lease_owner=None,
                        lease_expires_at=None,
                        last_error_code="LeaseExpired",
                        updated_at=now,
                    )
                    recovered += 1
            return recovered

    async def create_scheduled_action(
        self,
        *,
        action: ScheduledAction,
        job: BackgroundJob,
        outbox: OutboxEvent,
    ) -> tuple[ScheduledAction, bool]:
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self.scheduled_actions.values()
                    if item.tenant_id == action.tenant_id
                    and item.idempotency_key == action.idempotency_key
                ),
                None,
            )
            if existing is not None:
                return existing, False
            self.scheduled_actions[action.id] = action
            self.jobs[job.id] = job
            self.outbox_events[outbox.id] = outbox
            return action, True

    async def get_scheduled_action(
        self, *, tenant_id: UUID, action_id: UUID
    ) -> ScheduledAction | None:
        async with self._lock:
            item = self.scheduled_actions.get(action_id)
            return item if item is not None and item.tenant_id == tenant_id else None

    async def list_scheduled_actions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ScheduledActionStatus | None,
        limit: int,
    ) -> tuple[ScheduledAction, ...]:
        async with self._lock:
            rows = [
                item
                for item in self.scheduled_actions.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and (status is None or item.status is status)
            ]
            rows.sort(key=lambda item: (item.scheduled_for, str(item.id)), reverse=True)
            return tuple(rows[:limit])

    async def cancel_scheduled_action(
        self, *, tenant_id: UUID, action_id: UUID, actor_id: UUID, now: datetime
    ) -> ScheduledAction | None:
        del actor_id
        async with self._lock:
            item = self.scheduled_actions.get(action_id)
            if item is None or item.tenant_id != tenant_id:
                return None
            if item.status is not ScheduledActionStatus.PENDING:
                return item
            updated = replace(
                item,
                status=ScheduledActionStatus.CANCELED,
                updated_at=now,
                completed_at=now,
            )
            self.scheduled_actions[action_id] = updated
            if item.job_id is not None and (job := self.jobs.get(item.job_id)) is not None:
                self.jobs[job.id] = replace(
                    job,
                    status=BackgroundJobStatus.CANCELED,
                    cancel_requested_at=now,
                    completed_at=now,
                    updated_at=now,
                )
                self._cancel_outbox(job.id, now)
            return updated

    async def social_budget_used(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        budget_date: date,
    ) -> int:
        async with self._lock:
            return sum(
                item.cost
                for item in self.budget_usages.values()
                if item.tenant_id == tenant_id
                and item.agent_id == agent_id
                and item.user_id == user_id
                and item.budget_date == budget_date
            )

    async def decide_scheduled_action(
        self,
        *,
        tenant_id: UUID,
        action_id: UUID,
        decision: ProactiveDecision,
        budget_usage: SocialBudgetUsage | None,
        daily_budget: int,
        now: datetime,
    ) -> ScheduledAction | None:
        async with self._lock:
            item = self.scheduled_actions.get(action_id)
            if item is None or item.tenant_id != tenant_id:
                return None
            if item.status is not ScheduledActionStatus.PENDING:
                return item
            effective = decision
            if budget_usage is not None:
                used = sum(
                    entry.cost
                    for entry in self.budget_usages.values()
                    if entry.tenant_id == budget_usage.tenant_id
                    and entry.agent_id == budget_usage.agent_id
                    and entry.user_id == budget_usage.user_id
                    and entry.budget_date == budget_usage.budget_date
                )
                if used + budget_usage.cost > daily_budget:
                    effective = ProactiveDecision(
                        False,
                        decision.score,
                        ("daily_social_budget_exhausted",),
                    )
                elif budget_usage.scheduled_action_id not in {
                    entry.scheduled_action_id for entry in self.budget_usages.values()
                }:
                    self.budget_usages[budget_usage.id] = budget_usage
            expired = "action_expired" in effective.reasons
            updated = replace(
                item,
                status=(
                    ScheduledActionStatus.EXPIRED
                    if expired
                    else ScheduledActionStatus.DISPATCHED
                    if effective.approved
                    else ScheduledActionStatus.SUPPRESSED
                ),
                score=effective.score,
                decision_reasons=effective.reasons,
                updated_at=now,
                completed_at=now,
            )
            self.scheduled_actions[action_id] = updated
            return updated

    async def upsert_worker_heartbeat(self, heartbeat: WorkerHeartbeat) -> WorkerHeartbeat:
        async with self._lock:
            existing = self.heartbeats.get(heartbeat.worker_id)
            stored = (
                replace(heartbeat, started_at=existing.started_at)
                if existing is not None
                else heartbeat
            )
            self.heartbeats[heartbeat.worker_id] = stored
            return stored

    async def list_worker_heartbeats(self, *, since: datetime) -> tuple[WorkerHeartbeat, ...]:
        async with self._lock:
            rows = [item for item in self.heartbeats.values() if item.last_seen_at >= since]
            rows.sort(key=lambda item: item.last_seen_at, reverse=True)
            return tuple(rows)

    def _job_by_deduplication(self, tenant_id: UUID, key: str) -> BackgroundJob | None:
        return next(
            (
                item
                for item in self.jobs.values()
                if item.tenant_id == tenant_id and item.deduplication_key == key
            ),
            None,
        )

    def _cancel_outbox(self, job_id: UUID, now: datetime) -> None:
        for event_id, event in tuple(self.outbox_events.items()):
            if event.job_id == job_id and event.status in _OUTBOX_CLAIMABLE:
                self.outbox_events[event_id] = replace(
                    event,
                    status=OutboxEventStatus.CANCELED,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )

    def _set_inbox_status(
        self,
        job: BackgroundJob,
        status: InboxEventStatus,
        *,
        now: datetime,
        error_code: str | None = None,
    ) -> None:
        if job.source_inbox_id is None:
            return
        inbox = self.inbox_events.get(job.source_inbox_id)
        if inbox is not None:
            self.inbox_events[inbox.id] = replace(
                inbox,
                status=status,
                processed_at=now
                if status
                in {
                    InboxEventStatus.COMPLETED,
                    InboxEventStatus.DEAD_LETTER,
                    InboxEventStatus.CANCELED,
                }
                else None,
                last_error_code=error_code,
            )

    @staticmethod
    def _retry_outbox(job: BackgroundJob, now: datetime) -> OutboxEvent:
        return OutboxEvent(
            id=uuid4(),
            tenant_id=job.tenant_id,
            job_id=job.id,
            event_type="background_job.recovered",
            payload={"job_id": str(job.id), "queue": job.queue},
            status=OutboxEventStatus.PENDING,
            attempt_count=0,
            max_attempts=max(5, job.max_attempts),
            available_at=now,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            created_at=now,
            published_at=None,
            updated_at=now,
        )

    retry_outbox = _retry_outbox


class SqlAlchemyTaskRepository:
    """使用行锁、唯一键和租约实现至少一次投递下的幂等执行。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def enqueue(
        self,
        *,
        job: BackgroundJob,
        outbox: OutboxEvent,
        inbox: InboxEvent | None,
    ) -> tuple[BackgroundJob, bool]:
        try:
            async with self._session_factory.begin() as session:
                if inbox is not None:
                    session.add(self._inbox_model(inbox))
                session.add(self._job_model(job))
                session.add(self._outbox_model(outbox))
        except IntegrityError:
            async with self._session_factory() as session:
                existing = await session.scalar(
                    select(BackgroundJobModel).where(
                        BackgroundJobModel.tenant_id == job.tenant_id,
                        BackgroundJobModel.deduplication_key == job.deduplication_key,
                    )
                )
                if existing is None and inbox is not None:
                    duplicate_inbox = await session.scalar(
                        select(InboxEventModel).where(
                            InboxEventModel.tenant_id == inbox.tenant_id,
                            InboxEventModel.event_key == inbox.event_key,
                        )
                    )
                    if duplicate_inbox is not None:
                        existing = await session.get(BackgroundJobModel, duplicate_inbox.job_id)
                if existing is None:
                    raise
                return self._job(existing), False
        return job, True

    async def get_job(self, *, tenant_id: UUID, job_id: UUID) -> BackgroundJob | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(BackgroundJobModel).where(
                    BackgroundJobModel.id == job_id,
                    BackgroundJobModel.tenant_id == tenant_id,
                )
            )
        return self._job(row) if row is not None else None

    async def get_inbox_event(self, *, tenant_id: UUID, inbox_id: UUID) -> InboxEvent | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(InboxEventModel).where(
                    InboxEventModel.tenant_id == tenant_id,
                    InboxEventModel.id == inbox_id,
                )
            )
        return self._inbox(row) if row is not None else None

    async def list_inbox_events(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: InboxEventStatus | None,
        channel_id: UUID | None,
        limit: int,
    ) -> tuple[InboxEvent, ...]:
        statement = select(InboxEventModel).where(
            InboxEventModel.tenant_id == tenant_id,
            InboxEventModel.agent_id == agent_id,
        )
        if status is not None:
            statement = statement.where(InboxEventModel.status == status.value)
        if channel_id is not None:
            statement = statement.where(InboxEventModel.channel_id == channel_id)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(
                        InboxEventModel.received_at.desc(),
                        InboxEventModel.id.desc(),
                    ).limit(limit)
                )
            ).all()
        return tuple(self._inbox(row) for row in rows)

    async def list_jobs(
        self,
        *,
        tenant_id: UUID,
        status: BackgroundJobStatus | None,
        kind: BackgroundJobKind | None,
        limit: int,
    ) -> tuple[BackgroundJob, ...]:
        statement = select(BackgroundJobModel).where(BackgroundJobModel.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(BackgroundJobModel.status == status.value)
        if kind is not None:
            statement = statement.where(BackgroundJobModel.kind == kind.value)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(BackgroundJobModel.created_at.desc()).limit(limit)
                )
            ).all()
        return tuple(self._job(row) for row in rows)

    async def list_attempts(self, *, tenant_id: UUID, job_id: UUID) -> tuple[JobAttempt, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(JobAttemptModel)
                    .where(
                        JobAttemptModel.tenant_id == tenant_id,
                        JobAttemptModel.job_id == job_id,
                    )
                    .order_by(JobAttemptModel.attempt_number)
                )
            ).all()
        return tuple(self._attempt(row) for row in rows)

    async def get_counts(self, *, tenant_id: UUID) -> TaskCounts:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(BackgroundJobModel.status, func.count())
                    .where(BackgroundJobModel.tenant_id == tenant_id)
                    .group_by(BackgroundJobModel.status)
                )
            ).all()
            counts = {str(status): int(count) for status, count in rows}
            scheduled = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ScheduledActionModel)
                    .where(
                        ScheduledActionModel.tenant_id == tenant_id,
                        ScheduledActionModel.status == ScheduledActionStatus.PENDING.value,
                    )
                )
                or 0
            )
        return TaskCounts(
            pending=counts.get(BackgroundJobStatus.PENDING.value, 0),
            running=counts.get(BackgroundJobStatus.RUNNING.value, 0),
            retrying=counts.get(BackgroundJobStatus.RETRYING.value, 0),
            dead_letters=counts.get(BackgroundJobStatus.DEAD_LETTER.value, 0),
            scheduled=scheduled,
        )

    async def request_cancel(
        self, *, tenant_id: UUID, job_id: UUID, actor_id: UUID, now: datetime
    ) -> BackgroundJob | None:
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(BackgroundJobModel)
                .where(
                    BackgroundJobModel.id == job_id,
                    BackgroundJobModel.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if BackgroundJobStatus(row.status) in _JOB_TERMINAL:
                return self._job(row)
            row.cancel_requested_at = now
            row.updated_at = now
            if row.status != BackgroundJobStatus.RUNNING.value:
                row.status = BackgroundJobStatus.CANCELED.value
                row.completed_at = now
                await self._cancel_outbox_rows(session, job_id, now)
                await self._set_inbox_row(session, row, InboxEventStatus.CANCELED, now)
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="background_job.cancel_requested",
                resource_type="background_job",
                resource_id=job_id,
                detail={"status": row.status},
            )
            return self._job(row)

    async def replay(
        self,
        *,
        tenant_id: UUID,
        source_job_id: UUID,
        job: BackgroundJob,
        outbox: OutboxEvent,
        actor_id: UUID,
        reason: str,
    ) -> BackgroundJob | None:
        async with self._session_factory.begin() as session:
            source = await session.scalar(
                select(BackgroundJobModel)
                .where(
                    BackgroundJobModel.id == source_job_id,
                    BackgroundJobModel.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if source is None:
                return None
            session.add(self._job_model(job))
            session.add(self._outbox_model(outbox))
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="background_job.replayed",
                resource_type="background_job",
                resource_id=job.id,
                detail={"source_job_id": str(source_job_id), "reason": reason},
            )
        return job

    async def claim_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> JobClaim | None:
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(BackgroundJobModel)
                .where(BackgroundJobModel.id == job_id)
                .with_for_update(skip_locked=True)
            )
            if (
                row is None
                or row.status
                not in {
                    BackgroundJobStatus.PENDING.value,
                    BackgroundJobStatus.RETRYING.value,
                }
                or row.available_at > now
                or row.attempt_count >= row.max_attempts
            ):
                return None
            if row.cancel_requested_at is not None:
                row.status = BackgroundJobStatus.CANCELED.value
                row.completed_at = now
                row.updated_at = now
                await self._set_inbox_row(session, row, InboxEventStatus.CANCELED, now)
                return None
            row.status = BackgroundJobStatus.RUNNING.value
            row.attempt_count += 1
            row.lease_owner = worker_id
            row.lease_expires_at = now + timedelta(seconds=row.lease_seconds)
            row.started_at = row.started_at or now
            row.updated_at = now
            attempt = JobAttempt(
                id=uuid4(),
                tenant_id=row.tenant_id,
                job_id=row.id,
                attempt_number=row.attempt_count,
                status=JobAttemptStatus.RUNNING,
                worker_id=worker_id,
                started_at=now,
                completed_at=None,
                error_code=None,
                error_summary=None,
            )
            session.add(self._attempt_model(attempt))
            await self._set_inbox_row(session, row, InboxEventStatus.PROCESSING, now)
            return JobClaim(self._job(row), attempt)

    async def renew_job_lease(
        self,
        *,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(BackgroundJobModel).where(BackgroundJobModel.id == job_id).with_for_update()
            )
            attempt = await session.get(JobAttemptModel, attempt_id)
            if (
                row is None
                or attempt is None
                or row.status != BackgroundJobStatus.RUNNING.value
                or row.lease_owner != worker_id
                or attempt.status != JobAttemptStatus.RUNNING.value
                or attempt.worker_id != worker_id
            ):
                return False
            row.lease_expires_at = now + timedelta(seconds=row.lease_seconds)
            row.updated_at = now
            return True

    async def complete_job(
        self,
        *,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        result_summary: dict[str, JsonValue],
        now: datetime,
    ) -> BackgroundJob | None:
        async with self._session_factory.begin() as session:
            row, attempt = await self._locked_execution(session, job_id, attempt_id, worker_id)
            if row is None or attempt is None:
                return None
            canceled = row.cancel_requested_at is not None
            row.status = (
                BackgroundJobStatus.CANCELED.value
                if canceled
                else BackgroundJobStatus.SUCCEEDED.value
            )
            row.result_summary = {} if canceled else result_summary
            row.lease_owner = None
            row.lease_expires_at = None
            row.completed_at = now
            row.updated_at = now
            attempt.status = (
                JobAttemptStatus.CANCELED.value if canceled else JobAttemptStatus.SUCCEEDED.value
            )
            attempt.completed_at = now
            await self._set_inbox_row(
                session,
                row,
                InboxEventStatus.CANCELED if canceled else InboxEventStatus.COMPLETED,
                now,
            )
            return self._job(row)

    async def fail_job(
        self,
        *,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        error_code: str,
        error_summary: str,
        permanent: bool,
        timed_out: bool,
        retry_at: datetime,
        retry_outbox: OutboxEvent,
        now: datetime,
    ) -> BackgroundJob | None:
        async with self._session_factory.begin() as session:
            row, attempt = await self._locked_execution(session, job_id, attempt_id, worker_id)
            if row is None or attempt is None:
                return None
            if row.cancel_requested_at is not None:
                status = BackgroundJobStatus.CANCELED
            elif permanent:
                status = BackgroundJobStatus.FAILED
            elif row.attempt_count >= row.max_attempts:
                status = BackgroundJobStatus.DEAD_LETTER
            else:
                status = BackgroundJobStatus.RETRYING
            row.status = status.value
            row.available_at = (
                retry_at if status is BackgroundJobStatus.RETRYING else row.available_at
            )
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error_code = error_code
            row.last_error_summary = error_summary
            row.completed_at = now if status in _JOB_TERMINAL else None
            row.updated_at = now
            attempt.status = (
                JobAttemptStatus.CANCELED.value
                if status is BackgroundJobStatus.CANCELED
                else JobAttemptStatus.TIMED_OUT.value
                if timed_out
                else JobAttemptStatus.FAILED.value
            )
            attempt.completed_at = now
            attempt.error_code = error_code
            attempt.error_summary = error_summary
            if status is BackgroundJobStatus.RETRYING:
                session.add(self._outbox_model(retry_outbox))
                await self._set_inbox_row(session, row, InboxEventStatus.PENDING, now)
            elif status in {BackgroundJobStatus.FAILED, BackgroundJobStatus.DEAD_LETTER}:
                await self._set_inbox_row(
                    session,
                    row,
                    InboxEventStatus.DEAD_LETTER,
                    now,
                    error_code=error_code,
                )
            else:
                await self._set_inbox_row(session, row, InboxEventStatus.CANCELED, now)
            return self._job(row)

    async def claim_outbox(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[OutboxEvent, ...]:
        async with self._session_factory.begin() as session:
            rows = (
                await session.scalars(
                    select(OutboxEventModel)
                    .where(
                        OutboxEventModel.status.in_(item.value for item in _OUTBOX_CLAIMABLE),
                        OutboxEventModel.available_at <= now,
                        OutboxEventModel.attempt_count < OutboxEventModel.max_attempts,
                    )
                    .order_by(OutboxEventModel.available_at, OutboxEventModel.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                row.status = OutboxEventStatus.PUBLISHING.value
                row.attempt_count += 1
                row.lease_owner = worker_id
                row.lease_expires_at = lease_expires_at
                row.updated_at = now
            return tuple(self._outbox(row) for row in rows)

    async def mark_outbox_published(self, *, event_id: UUID, worker_id: str, now: datetime) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(OutboxEventModel)
                .where(
                    OutboxEventModel.id == event_id,
                    OutboxEventModel.status == OutboxEventStatus.PUBLISHING.value,
                    OutboxEventModel.lease_owner == worker_id,
                )
                .values(
                    status=OutboxEventStatus.PUBLISHED.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    published_at=now,
                    updated_at=now,
                )
            )

    async def mark_outbox_failed(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(OutboxEventModel)
                .where(
                    OutboxEventModel.id == event_id,
                    OutboxEventModel.status == OutboxEventStatus.PUBLISHING.value,
                    OutboxEventModel.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if row is None:
                return
            row.status = (
                OutboxEventStatus.DEAD_LETTER.value
                if row.attempt_count >= row.max_attempts
                else OutboxEventStatus.RETRYING.value
            )
            row.available_at = retry_at
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error_code = error_code
            row.updated_at = now

    async def recover_expired_leases(self, *, now: datetime) -> int:
        recovered = 0
        async with self._session_factory.begin() as session:
            jobs = (
                await session.scalars(
                    select(BackgroundJobModel)
                    .where(
                        BackgroundJobModel.status == BackgroundJobStatus.RUNNING.value,
                        BackgroundJobModel.lease_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in jobs:
                status = (
                    BackgroundJobStatus.DEAD_LETTER
                    if row.attempt_count >= row.max_attempts
                    else BackgroundJobStatus.RETRYING
                )
                row.status = status.value
                row.available_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                row.last_error_code = "LeaseExpired"
                row.last_error_summary = "Worker 租约过期，任务已由恢复器接管。"
                row.completed_at = now if status is BackgroundJobStatus.DEAD_LETTER else None
                row.updated_at = now
                attempts = (
                    await session.scalars(
                        select(JobAttemptModel).where(
                            JobAttemptModel.job_id == row.id,
                            JobAttemptModel.status == JobAttemptStatus.RUNNING.value,
                        )
                    )
                ).all()
                for attempt in attempts:
                    attempt.status = JobAttemptStatus.TIMED_OUT.value
                    attempt.completed_at = now
                    attempt.error_code = "LeaseExpired"
                    attempt.error_summary = "Worker 租约过期。"
                if status is BackgroundJobStatus.RETRYING:
                    session.add(self._outbox_model(self.retry_outbox(self._job(row), now)))
                    await self._set_inbox_row(session, row, InboxEventStatus.PENDING, now)
                else:
                    await self._set_inbox_row(
                        session,
                        row,
                        InboxEventStatus.DEAD_LETTER,
                        now,
                        error_code="LeaseExpired",
                    )
                recovered += 1
            outboxes = (
                await session.scalars(
                    select(OutboxEventModel)
                    .where(
                        OutboxEventModel.status == OutboxEventStatus.PUBLISHING.value,
                        OutboxEventModel.lease_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in outboxes:
                row.status = (
                    OutboxEventStatus.DEAD_LETTER.value
                    if row.attempt_count >= row.max_attempts
                    else OutboxEventStatus.RETRYING.value
                )
                row.available_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                row.last_error_code = "LeaseExpired"
                row.updated_at = now
                recovered += 1
        return recovered

    async def create_scheduled_action(
        self,
        *,
        action: ScheduledAction,
        job: BackgroundJob,
        outbox: OutboxEvent,
    ) -> tuple[ScheduledAction, bool]:
        try:
            async with self._session_factory.begin() as session:
                session.add(self._job_model(job))
                session.add(self._scheduled_model(action))
                session.add(self._outbox_model(outbox))
                self._audit(
                    session,
                    tenant_id=action.tenant_id,
                    actor_id=action.created_by,
                    action="scheduled_action.created",
                    resource_type="scheduled_action",
                    resource_id=action.id,
                    detail={
                        "kind": action.kind.value,
                        "scheduled_for": action.scheduled_for.isoformat(),
                    },
                )
        except IntegrityError:
            async with self._session_factory() as session:
                existing = await session.scalar(
                    select(ScheduledActionModel).where(
                        ScheduledActionModel.tenant_id == action.tenant_id,
                        ScheduledActionModel.idempotency_key == action.idempotency_key,
                    )
                )
                if existing is None:
                    raise
                return self._scheduled(existing), False
        return action, True

    async def get_scheduled_action(
        self, *, tenant_id: UUID, action_id: UUID
    ) -> ScheduledAction | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ScheduledActionModel).where(
                    ScheduledActionModel.id == action_id,
                    ScheduledActionModel.tenant_id == tenant_id,
                )
            )
        return self._scheduled(row) if row is not None else None

    async def list_scheduled_actions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ScheduledActionStatus | None,
        limit: int,
    ) -> tuple[ScheduledAction, ...]:
        statement = select(ScheduledActionModel).where(
            ScheduledActionModel.tenant_id == tenant_id,
            ScheduledActionModel.agent_id == agent_id,
        )
        if status is not None:
            statement = statement.where(ScheduledActionModel.status == status.value)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    statement.order_by(ScheduledActionModel.scheduled_for.desc()).limit(limit)
                )
            ).all()
        return tuple(self._scheduled(row) for row in rows)

    async def cancel_scheduled_action(
        self, *, tenant_id: UUID, action_id: UUID, actor_id: UUID, now: datetime
    ) -> ScheduledAction | None:
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(ScheduledActionModel)
                .where(
                    ScheduledActionModel.id == action_id,
                    ScheduledActionModel.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if row.status != ScheduledActionStatus.PENDING.value:
                return self._scheduled(row)
            row.status = ScheduledActionStatus.CANCELED.value
            row.updated_at = now
            row.completed_at = now
            job = await session.get(BackgroundJobModel, row.job_id, with_for_update=True)
            if job is not None and BackgroundJobStatus(job.status) not in _JOB_TERMINAL:
                job.status = BackgroundJobStatus.CANCELED.value
                job.cancel_requested_at = now
                job.completed_at = now
                job.updated_at = now
                await self._cancel_outbox_rows(session, job.id, now)
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="scheduled_action.canceled",
                resource_type="scheduled_action",
                resource_id=action_id,
                detail={},
            )
            return self._scheduled(row)

    async def social_budget_used(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        budget_date: date,
    ) -> int:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(func.coalesce(func.sum(SocialBudgetUsageModel.cost), 0)).where(
                    SocialBudgetUsageModel.tenant_id == tenant_id,
                    SocialBudgetUsageModel.agent_id == agent_id,
                    SocialBudgetUsageModel.user_id == user_id,
                    SocialBudgetUsageModel.budget_date == budget_date,
                )
            )
        return int(result or 0)

    async def decide_scheduled_action(
        self,
        *,
        tenant_id: UUID,
        action_id: UUID,
        decision: ProactiveDecision,
        budget_usage: SocialBudgetUsage | None,
        daily_budget: int,
        now: datetime,
    ) -> ScheduledAction | None:
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(ScheduledActionModel)
                .where(
                    ScheduledActionModel.id == action_id,
                    ScheduledActionModel.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if row.status != ScheduledActionStatus.PENDING.value:
                return self._scheduled(row)
            effective = decision
            if budget_usage is not None:
                lock_key = (
                    f"{budget_usage.tenant_id}:{budget_usage.agent_id}:"
                    f"{budget_usage.user_id}:{budget_usage.budget_date.isoformat()}"
                )
                await session.execute(
                    select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
                )
                usages = (
                    await session.scalars(
                        select(SocialBudgetUsageModel)
                        .where(
                            SocialBudgetUsageModel.tenant_id == budget_usage.tenant_id,
                            SocialBudgetUsageModel.agent_id == budget_usage.agent_id,
                            SocialBudgetUsageModel.user_id == budget_usage.user_id,
                            SocialBudgetUsageModel.budget_date == budget_usage.budget_date,
                        )
                        .with_for_update()
                    )
                ).all()
                if sum(item.cost for item in usages) + budget_usage.cost > daily_budget:
                    effective = ProactiveDecision(
                        False,
                        decision.score,
                        ("daily_social_budget_exhausted",),
                    )
                elif all(
                    item.scheduled_action_id != budget_usage.scheduled_action_id for item in usages
                ):
                    session.add(self._budget_model(budget_usage))
            expired = "action_expired" in effective.reasons
            row.status = (
                ScheduledActionStatus.EXPIRED.value
                if expired
                else ScheduledActionStatus.DISPATCHED.value
                if effective.approved
                else ScheduledActionStatus.SUPPRESSED.value
            )
            row.score = effective.score
            row.decision_reasons = list(effective.reasons)
            row.updated_at = now
            row.completed_at = now
            return self._scheduled(row)

    async def upsert_worker_heartbeat(self, heartbeat: WorkerHeartbeat) -> WorkerHeartbeat:
        async with self._session_factory.begin() as session:
            row = await session.get(WorkerHeartbeatModel, heartbeat.worker_id, with_for_update=True)
            if row is None:
                session.add(self._heartbeat_model(heartbeat))
                return heartbeat
            row.queues = list(heartbeat.queues)
            row.current_job_id = heartbeat.current_job_id
            row.last_seen_at = heartbeat.last_seen_at
            return replace(heartbeat, started_at=row.started_at)

    async def list_worker_heartbeats(self, *, since: datetime) -> tuple[WorkerHeartbeat, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(WorkerHeartbeatModel)
                    .where(WorkerHeartbeatModel.last_seen_at >= since)
                    .order_by(WorkerHeartbeatModel.last_seen_at.desc())
                )
            ).all()
        return tuple(self._heartbeat(row) for row in rows)

    @staticmethod
    async def _locked_execution(
        session: AsyncSession, job_id: UUID, attempt_id: UUID, worker_id: str
    ) -> tuple[BackgroundJobModel | None, JobAttemptModel | None]:
        row = await session.scalar(
            select(BackgroundJobModel)
            .where(
                BackgroundJobModel.id == job_id,
                BackgroundJobModel.status == BackgroundJobStatus.RUNNING.value,
                BackgroundJobModel.lease_owner == worker_id,
            )
            .with_for_update()
        )
        attempt = await session.scalar(
            select(JobAttemptModel)
            .where(
                JobAttemptModel.id == attempt_id,
                JobAttemptModel.job_id == job_id,
                JobAttemptModel.status == JobAttemptStatus.RUNNING.value,
            )
            .with_for_update()
        )
        return row, attempt

    @staticmethod
    async def _cancel_outbox_rows(session: AsyncSession, job_id: UUID, now: datetime) -> None:
        await session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.job_id == job_id,
                OutboxEventModel.status.in_(item.value for item in _OUTBOX_CLAIMABLE),
            )
            .values(
                status=OutboxEventStatus.CANCELED.value,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )

    @staticmethod
    async def _set_inbox_row(
        session: AsyncSession,
        job: BackgroundJobModel,
        status: InboxEventStatus,
        now: datetime,
        error_code: str | None = None,
    ) -> None:
        if job.source_inbox_id is None:
            return
        await session.execute(
            update(InboxEventModel)
            .where(InboxEventModel.id == job.source_inbox_id)
            .values(
                status=status.value,
                processed_at=(
                    now
                    if status
                    in {
                        InboxEventStatus.COMPLETED,
                        InboxEventStatus.DEAD_LETTER,
                        InboxEventStatus.CANCELED,
                    }
                    else None
                ),
                last_error_code=error_code,
            )
        )

    @staticmethod
    def _retry_outbox(job: BackgroundJob, now: datetime) -> OutboxEvent:
        return InMemoryTaskRepository.retry_outbox(job, now)

    retry_outbox = _retry_outbox

    @staticmethod
    def _inbox_model(item: InboxEvent) -> InboxEventModel:
        return InboxEventModel(
            id=item.id,
            tenant_id=item.tenant_id,
            event_key=item.event_key,
            event_type=item.event_type,
            payload=item.payload,
            status=item.status.value,
            job_id=item.job_id,
            received_at=item.received_at,
            processed_at=item.processed_at,
            last_error_code=item.last_error_code,
            agent_id=item.agent_id,
            channel_id=item.channel_id,
            schema_version=item.schema_version,
            platform=item.platform,
            external_event_digest=item.external_event_digest,
            external_subject_digest=item.external_subject_digest,
            external_conversation_digest=item.external_conversation_digest,
            external_thread_digest=item.external_thread_digest,
            external_message_digest=item.external_message_digest,
            user_id=item.user_id,
            conversation_id=item.conversation_id,
            content_kinds=list(item.content_kinds),
            content_block_count=item.content_block_count,
        )

    @staticmethod
    def _inbox(row: InboxEventModel) -> InboxEvent:
        return InboxEvent(
            id=row.id,
            tenant_id=row.tenant_id,
            event_key=row.event_key,
            event_type=row.event_type,
            payload=cast(dict[str, JsonValue], row.payload),
            status=InboxEventStatus(row.status),
            job_id=row.job_id,
            received_at=row.received_at,
            processed_at=row.processed_at,
            last_error_code=row.last_error_code,
            agent_id=row.agent_id,
            channel_id=row.channel_id,
            schema_version=row.schema_version,
            platform=row.platform,
            external_event_digest=row.external_event_digest,
            external_subject_digest=row.external_subject_digest,
            external_conversation_digest=row.external_conversation_digest,
            external_thread_digest=row.external_thread_digest,
            external_message_digest=row.external_message_digest,
            user_id=row.user_id,
            conversation_id=row.conversation_id,
            content_kinds=tuple(row.content_kinds),
            content_block_count=row.content_block_count,
        )

    @staticmethod
    def _job_model(item: BackgroundJob) -> BackgroundJobModel:
        return BackgroundJobModel(
            id=item.id,
            tenant_id=item.tenant_id,
            kind=item.kind.value,
            queue=item.queue,
            status=item.status.value,
            payload=item.payload,
            deduplication_key=item.deduplication_key,
            source_inbox_id=item.source_inbox_id,
            correlation_id=item.correlation_id,
            attempt_count=item.attempt_count,
            max_attempts=item.max_attempts,
            lease_seconds=item.lease_seconds,
            retry_base_seconds=item.retry_base_seconds,
            available_at=item.available_at,
            lease_owner=item.lease_owner,
            lease_expires_at=item.lease_expires_at,
            cancel_requested_at=item.cancel_requested_at,
            last_error_code=item.last_error_code,
            last_error_summary=item.last_error_summary,
            result_summary=item.result_summary,
            replayed_from_id=item.replayed_from_id,
            created_by=item.created_by,
            created_at=item.created_at,
            started_at=item.started_at,
            completed_at=item.completed_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _outbox_model(item: OutboxEvent) -> OutboxEventModel:
        return OutboxEventModel(
            id=item.id,
            tenant_id=item.tenant_id,
            job_id=item.job_id,
            event_type=item.event_type,
            payload=item.payload,
            status=item.status.value,
            attempt_count=item.attempt_count,
            max_attempts=item.max_attempts,
            available_at=item.available_at,
            lease_owner=item.lease_owner,
            lease_expires_at=item.lease_expires_at,
            last_error_code=item.last_error_code,
            created_at=item.created_at,
            published_at=item.published_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _attempt_model(item: JobAttempt) -> JobAttemptModel:
        return JobAttemptModel(
            id=item.id,
            tenant_id=item.tenant_id,
            job_id=item.job_id,
            attempt_number=item.attempt_number,
            status=item.status.value,
            worker_id=item.worker_id,
            started_at=item.started_at,
            completed_at=item.completed_at,
            error_code=item.error_code,
            error_summary=item.error_summary,
        )

    @staticmethod
    def _scheduled_model(item: ScheduledAction) -> ScheduledActionModel:
        if item.job_id is None:
            raise ValueError("持久化定时行为必须绑定后台任务")
        return ScheduledActionModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            user_id=item.user_id,
            conversation_id=item.conversation_id,
            kind=item.kind.value,
            status=item.status.value,
            scheduled_for=item.scheduled_for,
            expires_at=item.expires_at,
            idempotency_key=item.idempotency_key,
            reason=item.reason,
            payload=item.payload,
            score=item.score,
            social_cost=item.social_cost,
            decision_reasons=list(item.decision_reasons),
            job_id=item.job_id,
            created_by=item.created_by,
            created_at=item.created_at,
            updated_at=item.updated_at,
            completed_at=item.completed_at,
        )

    @staticmethod
    def _budget_model(item: SocialBudgetUsage) -> SocialBudgetUsageModel:
        return SocialBudgetUsageModel(
            id=item.id,
            tenant_id=item.tenant_id,
            agent_id=item.agent_id,
            user_id=item.user_id,
            budget_date=item.budget_date,
            scheduled_action_id=item.scheduled_action_id,
            cost=item.cost,
            created_at=item.created_at,
        )

    @staticmethod
    def _heartbeat_model(item: WorkerHeartbeat) -> WorkerHeartbeatModel:
        return WorkerHeartbeatModel(
            worker_id=item.worker_id,
            queues=list(item.queues),
            current_job_id=item.current_job_id,
            started_at=item.started_at,
            last_seen_at=item.last_seen_at,
        )

    @staticmethod
    def _job(row: BackgroundJobModel) -> BackgroundJob:
        return BackgroundJob(
            id=row.id,
            tenant_id=row.tenant_id,
            kind=BackgroundJobKind(row.kind),
            queue=row.queue,
            status=BackgroundJobStatus(row.status),
            payload=cast(dict[str, JsonValue], row.payload),
            deduplication_key=row.deduplication_key,
            source_inbox_id=row.source_inbox_id,
            correlation_id=row.correlation_id,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            lease_seconds=row.lease_seconds,
            retry_base_seconds=row.retry_base_seconds,
            available_at=row.available_at,
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
            cancel_requested_at=row.cancel_requested_at,
            last_error_code=row.last_error_code,
            last_error_summary=row.last_error_summary,
            result_summary=cast(dict[str, JsonValue], row.result_summary),
            replayed_from_id=row.replayed_from_id,
            created_by=row.created_by,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _outbox(row: OutboxEventModel) -> OutboxEvent:
        return OutboxEvent(
            id=row.id,
            tenant_id=row.tenant_id,
            job_id=row.job_id,
            event_type=row.event_type,
            payload=cast(dict[str, JsonValue], row.payload),
            status=OutboxEventStatus(row.status),
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            available_at=row.available_at,
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
            last_error_code=row.last_error_code,
            created_at=row.created_at,
            published_at=row.published_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _attempt(row: JobAttemptModel) -> JobAttempt:
        return JobAttempt(
            id=row.id,
            tenant_id=row.tenant_id,
            job_id=row.job_id,
            attempt_number=row.attempt_number,
            status=JobAttemptStatus(row.status),
            worker_id=row.worker_id,
            started_at=row.started_at,
            completed_at=row.completed_at,
            error_code=row.error_code,
            error_summary=row.error_summary,
        )

    @staticmethod
    def _scheduled(row: ScheduledActionModel) -> ScheduledAction:
        return ScheduledAction(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            user_id=row.user_id,
            conversation_id=row.conversation_id,
            kind=ScheduledActionKind(row.kind),
            status=ScheduledActionStatus(row.status),
            scheduled_for=row.scheduled_for,
            expires_at=row.expires_at,
            idempotency_key=row.idempotency_key,
            reason=row.reason,
            payload=cast(dict[str, JsonValue], row.payload),
            score=row.score,
            social_cost=row.social_cost,
            decision_reasons=tuple(row.decision_reasons),
            job_id=row.job_id,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    def _heartbeat(row: WorkerHeartbeatModel) -> WorkerHeartbeat:
        return WorkerHeartbeat(
            worker_id=row.worker_id,
            queues=tuple(row.queues),
            current_job_id=row.current_job_id,
            started_at=row.started_at,
            last_seen_at=row.last_seen_at,
        )

    @staticmethod
    def _audit(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        detail: dict[str, JsonValue],
    ) -> None:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                detail=detail,
            )
        )
