"""可恢复后台任务、事务事件、重放和主动行为调度用例。"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from cnb_cognition import (
    ProactiveContext,
    ProactiveDecision,
    ProactivePolicy,
    ProactivePolicyEvaluator,
)
from cnb_domain import (
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    InboxEvent,
    InboxEventStatus,
    JobAttempt,
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


class TaskValidationError(ValueError):
    """任务载荷、时间或重放命令不符合安全约束时抛出。"""


class TaskNotFoundError(LookupError):
    """任务或定时行为不属于当前租户时抛出。"""


class TaskConflictError(RuntimeError):
    """任务当前状态不允许取消、重放或执行时抛出。"""


class PermanentTaskError(RuntimeError):
    """处理器发现重试无法恢复的确定性错误。"""


class TaskDispatcher(Protocol):
    """至少一次向消息代理投递任务 ID 的端口。"""

    async def dispatch(self, *, job_id: UUID, queue: str) -> None: ...


class BackgroundJobHandler(Protocol):
    """一个任务种类的无框架执行器。"""

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]: ...


class TaskRepository(Protocol):
    """任务真相、事务 Inbox/Outbox 与调度数据的持久化端口。"""

    async def enqueue(
        self,
        *,
        job: BackgroundJob,
        outbox: OutboxEvent,
        inbox: InboxEvent | None,
    ) -> tuple[BackgroundJob, bool]: ...

    async def get_job(self, *, tenant_id: UUID, job_id: UUID) -> BackgroundJob | None: ...

    async def get_inbox_event(self, *, tenant_id: UUID, inbox_id: UUID) -> InboxEvent | None: ...

    async def list_inbox_events(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: InboxEventStatus | None,
        channel_id: UUID | None,
        limit: int,
    ) -> tuple[InboxEvent, ...]: ...

    async def list_jobs(
        self,
        *,
        tenant_id: UUID,
        status: BackgroundJobStatus | None,
        kind: BackgroundJobKind | None,
        limit: int,
    ) -> tuple[BackgroundJob, ...]: ...

    async def list_attempts(self, *, tenant_id: UUID, job_id: UUID) -> tuple[JobAttempt, ...]: ...

    async def get_counts(self, *, tenant_id: UUID) -> TaskCounts: ...

    async def request_cancel(
        self, *, tenant_id: UUID, job_id: UUID, actor_id: UUID, now: datetime
    ) -> BackgroundJob | None: ...

    async def replay(
        self,
        *,
        tenant_id: UUID,
        source_job_id: UUID,
        job: BackgroundJob,
        outbox: OutboxEvent,
        actor_id: UUID,
        reason: str,
    ) -> BackgroundJob | None: ...

    async def claim_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> JobClaim | None: ...

    async def complete_job(
        self,
        *,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        result_summary: dict[str, JsonValue],
        now: datetime,
    ) -> BackgroundJob | None: ...

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
    ) -> BackgroundJob | None: ...

    async def claim_outbox(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[OutboxEvent, ...]: ...

    async def mark_outbox_published(
        self, *, event_id: UUID, worker_id: str, now: datetime
    ) -> None: ...

    async def mark_outbox_failed(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> None: ...

    async def recover_expired_leases(self, *, now: datetime) -> int: ...

    async def create_scheduled_action(
        self,
        *,
        action: ScheduledAction,
        job: BackgroundJob,
        outbox: OutboxEvent,
    ) -> tuple[ScheduledAction, bool]: ...

    async def get_scheduled_action(
        self, *, tenant_id: UUID, action_id: UUID
    ) -> ScheduledAction | None: ...

    async def list_scheduled_actions(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ScheduledActionStatus | None,
        limit: int,
    ) -> tuple[ScheduledAction, ...]: ...

    async def cancel_scheduled_action(
        self, *, tenant_id: UUID, action_id: UUID, actor_id: UUID, now: datetime
    ) -> ScheduledAction | None: ...

    async def social_budget_used(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        budget_date: date,
    ) -> int: ...

    async def decide_scheduled_action(
        self,
        *,
        tenant_id: UUID,
        action_id: UUID,
        decision: ProactiveDecision,
        budget_usage: SocialBudgetUsage | None,
        daily_budget: int,
        now: datetime,
    ) -> ScheduledAction | None: ...

    async def upsert_worker_heartbeat(self, heartbeat: WorkerHeartbeat) -> WorkerHeartbeat: ...

    async def list_worker_heartbeats(self, *, since: datetime) -> tuple[WorkerHeartbeat, ...]: ...


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """显式报告去重命中，便于 API 与调用者安全重试。"""

    job: BackgroundJob
    created: bool


_QUEUE_BY_KIND = {
    BackgroundJobKind.REFLECTION: "reflection",
    BackgroundJobKind.EPISODE_CONSOLIDATION: "memory",
    BackgroundJobKind.MEMORY_EXTRACTION: "memory",
    BackgroundJobKind.EMBEDDING_REBUILD: "memory",
    BackgroundJobKind.RELATIONSHIP_UPDATE: "memory",
    BackgroundJobKind.SCHEDULED_ACTION: "proactive",
    BackgroundJobKind.INBOUND_MESSAGE: "inbound",
}
_TERMINAL_REPLAYABLE = {
    BackgroundJobStatus.FAILED,
    BackgroundJobStatus.DEAD_LETTER,
    BackgroundJobStatus.CANCELED,
}
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "credential")


class BackgroundTaskService:
    """以 PostgreSQL 真相表吸收 Redis 丢失和至少一次重复投递。"""

    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    async def enqueue(
        self,
        *,
        tenant_id: UUID,
        kind: BackgroundJobKind,
        payload: Mapping[str, JsonValue],
        deduplication_key: str,
        created_by: UUID | None,
        max_attempts: int = 5,
        lease_seconds: int = 120,
        retry_base_seconds: int = 5,
        available_at: datetime | None = None,
        correlation_id: str | None = None,
        source_inbox: InboxEvent | None = None,
        job_id: UUID | None = None,
    ) -> EnqueueResult:
        now = datetime.now(UTC)
        normalized_payload = self._payload(payload)
        normalized_key = self._required_text(deduplication_key, "任务去重键", 255)
        if not 1 <= max_attempts <= 20:
            raise TaskValidationError("任务最大尝试次数必须位于 1 到 20 之间")
        self._positive(lease_seconds, "任务租约秒数")
        self._positive(retry_base_seconds, "失败退避基数")
        due_at = available_at or now
        self._aware(due_at, "任务可执行时间")
        resolved_job_id = job_id or uuid4()
        job = BackgroundJob(
            id=resolved_job_id,
            tenant_id=tenant_id,
            kind=kind,
            queue=_QUEUE_BY_KIND[kind],
            status=BackgroundJobStatus.PENDING,
            payload=normalized_payload,
            deduplication_key=normalized_key,
            source_inbox_id=source_inbox.id if source_inbox else None,
            correlation_id=self._optional_text(correlation_id, 255),
            attempt_count=0,
            max_attempts=max_attempts,
            lease_seconds=lease_seconds,
            retry_base_seconds=retry_base_seconds,
            available_at=due_at,
            lease_owner=None,
            lease_expires_at=None,
            cancel_requested_at=None,
            last_error_code=None,
            last_error_summary=None,
            result_summary={},
            replayed_from_id=None,
            created_by=created_by,
            created_at=now,
            started_at=None,
            completed_at=None,
            updated_at=now,
        )
        outbox = self._outbox(job, now=now, available_at=due_at)
        stored, created = await self._repository.enqueue(
            job=job,
            outbox=outbox,
            inbox=source_inbox,
        )
        return EnqueueResult(stored, created)

    async def accept_inbox_event(
        self,
        *,
        tenant_id: UUID,
        event_key: str,
        event_type: str,
        payload: Mapping[str, JsonValue],
        job_kind: BackgroundJobKind,
        created_by: UUID | None,
        max_attempts: int = 5,
        lease_seconds: int = 120,
        retry_base_seconds: int = 5,
    ) -> EnqueueResult:
        """在一个事务中写入 Inbox、任务与 Outbox，重复事件返回原任务。"""
        now = datetime.now(UTC)
        normalized_key = self._required_text(event_key, "Inbox 事件键", 255)
        normalized_type = self._required_text(event_type, "Inbox 事件类型", 120)
        normalized_payload = self._payload(payload)
        job_id = uuid4()
        inbox = InboxEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            event_key=normalized_key,
            event_type=normalized_type,
            payload=normalized_payload,
            status=InboxEventStatus.PENDING,
            job_id=job_id,
            received_at=now,
            processed_at=None,
            last_error_code=None,
        )
        return await self.enqueue(
            tenant_id=tenant_id,
            kind=job_kind,
            payload=normalized_payload,
            deduplication_key=f"inbox:{normalized_key}",
            created_by=created_by,
            max_attempts=max_attempts,
            lease_seconds=lease_seconds,
            retry_base_seconds=retry_base_seconds,
            correlation_id=normalized_key,
            source_inbox=inbox,
            job_id=job_id,
        )

    async def get_job(self, *, tenant_id: UUID, job_id: UUID) -> BackgroundJob:
        item = await self._repository.get_job(tenant_id=tenant_id, job_id=job_id)
        if item is None:
            raise TaskNotFoundError(f"任务不存在：{job_id}")
        return item

    async def get_inbox_by_id(self, *, tenant_id: UUID, inbox_id: UUID) -> InboxEvent:
        item = await self._repository.get_inbox_event(
            tenant_id=tenant_id,
            inbox_id=inbox_id,
        )
        if item is None:
            raise TaskNotFoundError(f"Inbox 事件不存在：{inbox_id}")
        return item

    async def get_inbox(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        inbox_id: UUID,
    ) -> InboxEvent:
        item = await self.get_inbox_by_id(tenant_id=tenant_id, inbox_id=inbox_id)
        if item.agent_id != agent_id:
            raise TaskNotFoundError(f"Inbox 事件不存在：{inbox_id}")
        return item

    async def list_inbox(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: InboxEventStatus | None,
        channel_id: UUID | None,
        limit: int,
    ) -> tuple[InboxEvent, ...]:
        return await self._repository.list_inbox_events(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=status,
            channel_id=channel_id,
            limit=self._limit(limit),
        )

    async def list_jobs(
        self,
        *,
        tenant_id: UUID,
        status: BackgroundJobStatus | None,
        kind: BackgroundJobKind | None,
        limit: int,
    ) -> tuple[BackgroundJob, ...]:
        return await self._repository.list_jobs(
            tenant_id=tenant_id,
            status=status,
            kind=kind,
            limit=self._limit(limit),
        )

    async def list_attempts(self, *, tenant_id: UUID, job_id: UUID) -> tuple[JobAttempt, ...]:
        await self.get_job(tenant_id=tenant_id, job_id=job_id)
        return await self._repository.list_attempts(tenant_id=tenant_id, job_id=job_id)

    async def counts(self, *, tenant_id: UUID) -> TaskCounts:
        return await self._repository.get_counts(tenant_id=tenant_id)

    async def cancel(
        self, *, tenant_id: UUID, job_id: UUID, actor_id: UUID, confirmed: bool
    ) -> BackgroundJob:
        if not confirmed:
            raise TaskValidationError("取消任务必须明确确认")
        item = await self._repository.request_cancel(
            tenant_id=tenant_id,
            job_id=job_id,
            actor_id=actor_id,
            now=datetime.now(UTC),
        )
        if item is None:
            raise TaskNotFoundError(f"任务不存在：{job_id}")
        if item.status in {BackgroundJobStatus.SUCCEEDED, BackgroundJobStatus.FAILED}:
            raise TaskConflictError("已结束任务不能取消")
        return item

    async def replay(
        self,
        *,
        tenant_id: UUID,
        job_id: UUID,
        actor_id: UUID,
        confirmed: bool,
        reason: str,
    ) -> BackgroundJob:
        if not confirmed:
            raise TaskValidationError("重放任务必须明确确认")
        normalized_reason = self._required_text(reason, "重放原因", 500)
        source = await self.get_job(tenant_id=tenant_id, job_id=job_id)
        if source.status not in _TERMINAL_REPLAYABLE:
            raise TaskConflictError("只有失败、死信或已取消任务可以安全重放")
        now = datetime.now(UTC)
        replay_id = uuid4()
        replayed = BackgroundJob(
            id=replay_id,
            tenant_id=source.tenant_id,
            kind=source.kind,
            queue=source.queue,
            status=BackgroundJobStatus.PENDING,
            payload=source.payload,
            deduplication_key=f"replay:{source.id}:{replay_id}",
            source_inbox_id=None,
            correlation_id=source.correlation_id,
            attempt_count=0,
            max_attempts=source.max_attempts,
            lease_seconds=source.lease_seconds,
            retry_base_seconds=source.retry_base_seconds,
            available_at=now,
            lease_owner=None,
            lease_expires_at=None,
            cancel_requested_at=None,
            last_error_code=None,
            last_error_summary=None,
            result_summary={},
            replayed_from_id=source.id,
            created_by=actor_id,
            created_at=now,
            started_at=None,
            completed_at=None,
            updated_at=now,
        )
        result = await self._repository.replay(
            tenant_id=tenant_id,
            source_job_id=job_id,
            job=replayed,
            outbox=self._outbox(replayed, now=now, available_at=now),
            actor_id=actor_id,
            reason=normalized_reason,
        )
        if result is None:
            raise TaskNotFoundError(f"任务不存在：{job_id}")
        return result

    async def publish_due(
        self,
        *,
        dispatcher: TaskDispatcher,
        worker_id: str,
        lease_seconds: int = 30,
        retry_base_seconds: int = 5,
        limit: int = 100,
    ) -> int:
        now = datetime.now(UTC)
        events = await self._repository.claim_outbox(
            worker_id=self._required_text(worker_id, "发布器 ID", 160),
            now=now,
            lease_expires_at=now + timedelta(seconds=self._positive(lease_seconds, "租约秒数")),
            limit=self._limit(limit),
        )
        published = 0
        for event in events:
            queue = event.payload.get("queue")
            if not isinstance(queue, str) or queue not in set(_QUEUE_BY_KIND.values()):
                await self._repository.mark_outbox_failed(
                    event_id=event.id,
                    worker_id=worker_id,
                    error_code="InvalidQueue",
                    retry_at=now,
                    now=datetime.now(UTC),
                )
                continue
            try:
                await dispatcher.dispatch(job_id=event.job_id, queue=queue)
            except Exception as error:
                job = await self._repository.get_job(
                    tenant_id=event.tenant_id,
                    job_id=event.job_id,
                )
                base_seconds = job.retry_base_seconds if job is not None else retry_base_seconds
                delay = base_seconds * 2 ** min(event.attempt_count, 8)
                await self._repository.mark_outbox_failed(
                    event_id=event.id,
                    worker_id=worker_id,
                    error_code=type(error).__name__[:120],
                    retry_at=datetime.now(UTC) + timedelta(seconds=delay),
                    now=datetime.now(UTC),
                )
                continue
            await self._repository.mark_outbox_published(
                event_id=event.id,
                worker_id=worker_id,
                now=datetime.now(UTC),
            )
            published += 1
        return published

    async def recover_expired(self) -> int:
        return await self._repository.recover_expired_leases(now=datetime.now(UTC))

    async def execute(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        handlers: Mapping[BackgroundJobKind, BackgroundJobHandler],
    ) -> BackgroundJob | None:
        """租约认领后执行一次；重复消息在认领阶段被安全忽略。"""
        now = datetime.now(UTC)
        claim = await self._repository.claim_job(
            job_id=job_id,
            worker_id=self._required_text(worker_id, "Worker ID", 160),
            now=now,
        )
        if claim is None:
            return None
        handler = handlers.get(claim.job.kind)
        try:
            if handler is None:
                raise PermanentTaskError(f"未注册任务处理器：{claim.job.kind.value}")
            result = self._result(await handler.handle(claim.job))
        except Exception as error:
            permanent = isinstance(error, PermanentTaskError)
            timed_out = isinstance(error, TimeoutError)
            delay = claim.job.retry_base_seconds * 2 ** min(max(0, claim.job.attempt_count - 1), 8)
            failed = await self._repository.fail_job(
                job_id=claim.job.id,
                attempt_id=claim.attempt.id,
                worker_id=worker_id,
                error_code=type(error).__name__[:120],
                error_summary="任务执行失败；详细异常仅保留在受控日志中。",
                permanent=permanent,
                timed_out=timed_out,
                retry_at=datetime.now(UTC) + timedelta(seconds=delay),
                retry_outbox=self._outbox(
                    claim.job,
                    now=datetime.now(UTC),
                    available_at=datetime.now(UTC) + timedelta(seconds=delay),
                ),
                now=datetime.now(UTC),
            )
            return failed
        return await self._repository.complete_job(
            job_id=claim.job.id,
            attempt_id=claim.attempt.id,
            worker_id=worker_id,
            result_summary=result,
            now=datetime.now(UTC),
        )

    async def heartbeat(
        self,
        *,
        worker_id: str,
        queues: tuple[str, ...],
        started_at: datetime,
        current_job_id: UUID | None = None,
    ) -> WorkerHeartbeat:
        self._aware(started_at, "Worker 启动时间")
        if not queues:
            raise TaskValidationError("Worker 至少声明一个队列")
        now = datetime.now(UTC)
        return await self._repository.upsert_worker_heartbeat(
            WorkerHeartbeat(
                worker_id=self._required_text(worker_id, "Worker ID", 160),
                queues=tuple(
                    dict.fromkeys(self._required_text(item, "队列名称", 80) for item in queues)
                ),
                current_job_id=current_job_id,
                started_at=started_at,
                last_seen_at=now,
            )
        )

    async def active_workers(self, *, stale_after_seconds: int = 45) -> tuple[WorkerHeartbeat, ...]:
        seconds = self._positive(stale_after_seconds, "心跳过期秒数")
        return await self._repository.list_worker_heartbeats(
            since=datetime.now(UTC) - timedelta(seconds=seconds)
        )

    @staticmethod
    def _outbox(job: BackgroundJob, *, now: datetime, available_at: datetime) -> OutboxEvent:
        return OutboxEvent(
            id=uuid4(),
            tenant_id=job.tenant_id,
            job_id=job.id,
            event_type="background_job.dispatch_requested",
            payload={"job_id": str(job.id), "queue": job.queue},
            status=OutboxEventStatus.PENDING,
            attempt_count=0,
            max_attempts=max(5, job.max_attempts),
            available_at=available_at,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            created_at=now,
            published_at=None,
            updated_at=now,
        )

    @staticmethod
    def _payload(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        payload = dict(value)
        BackgroundTaskService._reject_sensitive_keys(payload)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise TaskValidationError("任务载荷不能超过 64 KiB")
        return payload

    @staticmethod
    def _result(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        result = dict(value)
        BackgroundTaskService._reject_sensitive_keys(result)
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 8 * 1024:
            raise TaskValidationError("任务结果摘要不能超过 8 KiB")
        return result

    @staticmethod
    def _reject_sensitive_keys(value: Mapping[str, JsonValue]) -> None:
        pending: list[Mapping[str, JsonValue]] = [value]
        while pending:
            current = pending.pop()
            for key, item in current.items():
                folded = key.casefold()
                if any(part in folded for part in _SENSITIVE_KEY_PARTS):
                    raise TaskValidationError(f"任务载荷禁止包含疑似密钥字段：{key}")
                if isinstance(item, dict):
                    pending.append(item)
                elif isinstance(item, list):
                    pending.extend(entry for entry in item if isinstance(entry, dict))

    @staticmethod
    def _required_text(value: str, label: str, maximum: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise TaskValidationError(f"{label}不能为空")
        if len(normalized) > maximum:
            raise TaskValidationError(f"{label}不能超过 {maximum} 个字符")
        return normalized

    @staticmethod
    def _optional_text(value: str | None, maximum: int) -> str | None:
        if value is None or not value.strip():
            return None
        return BackgroundTaskService._required_text(value, "文本", maximum)

    @staticmethod
    def _aware(value: datetime, label: str) -> None:
        if value.tzinfo is None:
            raise TaskValidationError(f"{label}必须包含时区")

    @staticmethod
    def _positive(value: int, label: str) -> int:
        if not 1 <= value <= 86400:
            raise TaskValidationError(f"{label}必须位于 1 到 86400 之间")
        return value

    @staticmethod
    def _limit(value: int) -> int:
        if not 1 <= value <= 500:
            raise TaskValidationError("分页数量必须位于 1 到 500 之间")
        return value

    validate_aware = _aware
    normalize_payload = _payload
    require_text = _required_text
    make_outbox = _outbox
    validate_limit = _limit
    validate_positive = _positive


class ScheduledActionService:
    """创建定时行为，并在执行时按最新配置与关系状态重新评分。"""

    def __init__(
        self,
        repository: TaskRepository,
        task_service: BackgroundTaskService,
        *,
        evaluator: ProactivePolicyEvaluator | None = None,
    ) -> None:
        self._repository = repository
        self._task_service = task_service
        self._evaluator = evaluator or ProactivePolicyEvaluator()

    async def create(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None,
        kind: ScheduledActionKind,
        scheduled_for: datetime,
        expires_at: datetime | None,
        idempotency_key: str,
        reason: str,
        payload: Mapping[str, JsonValue],
        social_cost: int,
        created_by: UUID,
        max_attempts: int,
        lease_seconds: int,
        retry_base_seconds: int,
    ) -> tuple[ScheduledAction, bool]:
        BackgroundTaskService.validate_aware(scheduled_for, "计划执行时间")
        if expires_at is not None:
            BackgroundTaskService.validate_aware(expires_at, "计划过期时间")
            if expires_at <= scheduled_for:
                raise TaskValidationError("计划过期时间必须晚于执行时间")
        if not 0 <= social_cost <= 20:
            raise TaskValidationError("社交预算成本必须位于 0 到 20 之间")
        if not 1 <= max_attempts <= 20:
            raise TaskValidationError("任务最大尝试次数必须位于 1 到 20 之间")
        BackgroundTaskService.validate_positive(lease_seconds, "任务租约秒数")
        BackgroundTaskService.validate_positive(retry_base_seconds, "失败退避基数")
        now = datetime.now(UTC)
        action_id = uuid4()
        normalized_payload = BackgroundTaskService.normalize_payload(payload)
        action = ScheduledAction(
            id=action_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            conversation_id=conversation_id,
            kind=kind,
            status=ScheduledActionStatus.PENDING,
            scheduled_for=scheduled_for,
            expires_at=expires_at,
            idempotency_key=BackgroundTaskService.require_text(
                idempotency_key, "定时行为幂等键", 255
            ),
            reason=BackgroundTaskService.require_text(reason, "定时行为原因", 500),
            payload=normalized_payload,
            score=None,
            social_cost=social_cost,
            decision_reasons=(),
            job_id=None,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        job_id = uuid4()
        job = BackgroundJob(
            id=job_id,
            tenant_id=tenant_id,
            kind=BackgroundJobKind.SCHEDULED_ACTION,
            queue=_QUEUE_BY_KIND[BackgroundJobKind.SCHEDULED_ACTION],
            status=BackgroundJobStatus.PENDING,
            payload={"scheduled_action_id": str(action_id)},
            deduplication_key=f"scheduled-action:{action.idempotency_key}",
            source_inbox_id=None,
            correlation_id=str(action_id),
            attempt_count=0,
            max_attempts=max_attempts,
            lease_seconds=lease_seconds,
            retry_base_seconds=retry_base_seconds,
            available_at=scheduled_for,
            lease_owner=None,
            lease_expires_at=None,
            cancel_requested_at=None,
            last_error_code=None,
            last_error_summary=None,
            result_summary={},
            replayed_from_id=None,
            created_by=created_by,
            created_at=now,
            started_at=None,
            completed_at=None,
            updated_at=now,
        )
        action = replace(action, job_id=job_id)
        return await self._repository.create_scheduled_action(
            action=action,
            job=job,
            outbox=BackgroundTaskService.make_outbox(job, now=now, available_at=scheduled_for),
        )

    async def list(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        status: ScheduledActionStatus | None,
        limit: int,
    ) -> tuple[ScheduledAction, ...]:
        return await self._repository.list_scheduled_actions(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=status,
            limit=BackgroundTaskService.validate_limit(limit),
        )

    async def cancel(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        action_id: UUID,
        actor_id: UUID,
        confirmed: bool,
    ) -> ScheduledAction:
        if not confirmed:
            raise TaskValidationError("取消定时行为必须明确确认")
        current = await self._repository.get_scheduled_action(
            tenant_id=tenant_id,
            action_id=action_id,
        )
        if current is None or current.agent_id != agent_id:
            raise TaskNotFoundError(f"定时行为不存在：{action_id}")
        item = await self._repository.cancel_scheduled_action(
            tenant_id=tenant_id,
            action_id=action_id,
            actor_id=actor_id,
            now=datetime.now(UTC),
        )
        if item is None:
            raise TaskNotFoundError(f"定时行为不存在：{action_id}")
        return item

    async def evaluate(
        self,
        *,
        tenant_id: UUID,
        action_id: UUID,
        policy: ProactivePolicy,
        context: ProactiveContext,
        budget_date: date,
    ) -> ScheduledAction:
        action = await self._repository.get_scheduled_action(
            tenant_id=tenant_id, action_id=action_id
        )
        if action is None:
            raise PermanentTaskError(f"定时行为不存在：{action_id}")
        if action.status is not ScheduledActionStatus.PENDING:
            return action
        now = datetime.now(UTC)
        if action.expires_at is not None and now >= action.expires_at:
            decision = ProactiveDecision(False, 0.0, ("action_expired",))
            updated = await self._repository.decide_scheduled_action(
                tenant_id=tenant_id,
                action_id=action_id,
                decision=decision,
                budget_usage=None,
                daily_budget=policy.daily_budget,
                now=now,
            )
        else:
            used = await self._repository.social_budget_used(
                tenant_id=tenant_id,
                agent_id=action.agent_id,
                user_id=action.user_id,
                budget_date=budget_date,
            )
            effective_context = ProactiveContext(
                local_hour=context.local_hour,
                days_since_user_activity=context.days_since_user_activity,
                importance=context.importance,
                confidence=context.confidence,
                affinity=context.affinity,
                trust=context.trust,
                familiarity=context.familiarity,
                used_budget=used,
                social_cost=action.social_cost,
                has_boundary_block=context.has_boundary_block,
            )
            decision = self._evaluator.evaluate(policy, effective_context)
            usage = (
                SocialBudgetUsage(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    agent_id=action.agent_id,
                    user_id=action.user_id,
                    budget_date=budget_date,
                    scheduled_action_id=action.id,
                    cost=action.social_cost,
                    created_at=now,
                )
                if decision.approved and action.social_cost
                else None
            )
            updated = await self._repository.decide_scheduled_action(
                tenant_id=tenant_id,
                action_id=action_id,
                decision=decision,
                budget_usage=usage,
                daily_budget=policy.daily_budget,
                now=now,
            )
        if updated is None:
            raise TaskNotFoundError(f"定时行为不存在：{action_id}")
        return updated
