"""可恢复后台任务、尝试、死信与主动行为管理接口。"""

from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from cnb_api.dependencies import (
    get_admin_principal,
    get_configuration_service,
    get_request_identity,
    get_scheduled_action_service,
    get_task_service,
    require_permission,
)
from cnb_application import (
    BackgroundTaskService,
    ConfigurationService,
    ScheduledActionService,
    TaskConflictError,
    TaskNotFoundError,
    TaskValidationError,
)
from cnb_contracts import (
    BackgroundJobDetailResponse,
    BackgroundJobListResponse,
    BackgroundJobReplayChainItem,
    BackgroundJobReplayChainResponse,
    BackgroundJobResponse,
    JobAttemptResponse,
    ScheduledActionCreate,
    ScheduledActionListResponse,
    ScheduledActionResponse,
    TaskCancelCommand,
    TaskDashboardResponse,
    TaskReplayCommand,
    WorkerHeartbeatResponse,
)
from cnb_domain import (
    AdminPermission,
    AdminPrincipal,
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    JobAttempt,
    JsonValue,
    ScheduledAction,
    ScheduledActionStatus,
)

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[
        Depends(require_permission(AdminPermission.TASK_READ)),
        Depends(get_request_identity),
    ],
)


def _job_response(item: BackgroundJob) -> BackgroundJobResponse:
    return BackgroundJobResponse(
        id=item.id,
        tenant_id=item.tenant_id,
        kind=item.kind,
        queue=item.queue,
        status=item.status,
        payload_keys=tuple(sorted(item.payload)),
        deduplication_key=item.deduplication_key,
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


def _attempt_response(item: JobAttempt) -> JobAttemptResponse:
    return JobAttemptResponse(
        id=item.id,
        job_id=item.job_id,
        attempt_number=item.attempt_number,
        status=item.status,
        worker_id=item.worker_id,
        started_at=item.started_at,
        completed_at=item.completed_at,
        error_code=item.error_code,
        error_summary=item.error_summary,
    )


def _scheduled_response(item: ScheduledAction) -> ScheduledActionResponse:
    return ScheduledActionResponse(
        id=item.id,
        tenant_id=item.tenant_id,
        agent_id=item.agent_id,
        user_id=item.user_id,
        conversation_id=item.conversation_id,
        kind=item.kind,
        status=item.status,
        scheduled_for=item.scheduled_for,
        expires_at=item.expires_at,
        idempotency_key=item.idempotency_key,
        reason=item.reason,
        payload_keys=tuple(sorted(item.payload)),
        score=item.score,
        social_cost=item.social_cost,
        decision_reasons=item.decision_reasons,
        job_id=item.job_id,
        created_by=item.created_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
        completed_at=item.completed_at,
    )


@router.get("/dashboard", response_model=TaskDashboardResponse)
async def dashboard(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> TaskDashboardResponse:
    """返回任务真相计数和仍处于新鲜时间窗内的任务进程。"""
    counts = await service.counts(tenant_id=principal.tenant_id)
    workers = await service.active_workers()
    return TaskDashboardResponse(
        pending=counts.pending,
        running=counts.running,
        retrying=counts.retrying,
        dead_letters=counts.dead_letters,
        scheduled=counts.scheduled,
        workers=tuple(
            WorkerHeartbeatResponse(
                worker_id=item.worker_id,
                queues=item.queues,
                current_job_id=item.current_job_id,
                started_at=item.started_at,
                last_seen_at=item.last_seen_at,
            )
            for item in workers
        ),
    )


@router.get("/jobs", response_model=BackgroundJobListResponse)
async def list_jobs(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[BackgroundTaskService, Depends(get_task_service)],
    job_status: BackgroundJobStatus | None = None,
    kind: BackgroundJobKind | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> BackgroundJobListResponse:
    """按创建时间倒序查询当前租户任务。"""
    items = await service.list_jobs(
        tenant_id=principal.tenant_id,
        status=job_status,
        kind=kind,
        limit=limit,
    )
    return BackgroundJobListResponse(items=tuple(_job_response(item) for item in items))


@router.get("/dead-letters", response_model=BackgroundJobListResponse)
async def list_dead_letters(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[BackgroundTaskService, Depends(get_task_service)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> BackgroundJobListResponse:
    """仅返回可由管理员检查和安全重放的死信任务。"""
    items = await service.list_jobs(
        tenant_id=principal.tenant_id,
        status=BackgroundJobStatus.DEAD_LETTER,
        kind=None,
        limit=limit,
    )
    return BackgroundJobListResponse(items=tuple(_job_response(item) for item in items))


@router.get("/jobs/{job_id}", response_model=BackgroundJobDetailResponse)
async def get_job(
    job_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> BackgroundJobDetailResponse:
    """返回任务及其全部尝试，但不返回原始业务载荷。"""
    try:
        item = await service.get_job(tenant_id=principal.tenant_id, job_id=job_id)
        attempts = await service.list_attempts(
            tenant_id=principal.tenant_id,
            job_id=job_id,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return BackgroundJobDetailResponse(
        job=_job_response(item),
        attempts=tuple(_attempt_response(attempt) for attempt in attempts),
    )


@router.get(
    "/jobs/{job_id}/replay-chain",
    response_model=BackgroundJobReplayChainResponse,
)
async def replay_chain(
    job_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> BackgroundJobReplayChainResponse:
    """返回重放任务的来源链，不返回任务载荷或出站消息正文。"""
    try:
        chain = await service.replay_chain(tenant_id=principal.tenant_id, job_id=job_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return BackgroundJobReplayChainResponse(
        items=tuple(
            BackgroundJobReplayChainItem(
                id=item.id,
                status=item.status,
                kind=item.kind,
                created_at=item.created_at,
                completed_at=item.completed_at,
                last_error_code=item.last_error_code,
                replayed_from_id=item.replayed_from_id,
            )
            for item in chain
        )
    )


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=BackgroundJobResponse,
    dependencies=[Depends(require_permission(AdminPermission.TASK_MANAGE))],
)
async def cancel_job(
    job_id: UUID,
    command: TaskCancelCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> BackgroundJobResponse:
    """取消未开始任务，或给运行中任务设置协作式取消标记。"""
    try:
        item = await service.cancel(
            tenant_id=principal.tenant_id,
            job_id=job_id,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (TaskConflictError, TaskValidationError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _job_response(item)


@router.post(
    "/jobs/{job_id}/replay",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.TASK_MANAGE))],
)
async def replay_job(
    job_id: UUID,
    command: TaskReplayCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> BackgroundJobResponse:
    """复制失败任务为全新审计实体，不复活或改写原任务。"""
    try:
        item = await service.replay(
            tenant_id=principal.tenant_id,
            job_id=job_id,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
            reason=command.reason,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (TaskConflictError, TaskValidationError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _job_response(item)


@router.post(
    "/maintenance/recover",
    dependencies=[Depends(require_permission(AdminPermission.TASK_MANAGE))],
)
async def recover_expired_leases(
    service: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> dict[str, int]:
    """立即恢复因任务进程或 API 重启而过期的执行和发布租约。"""
    return {"recovered": await service.recover_expired()}


@router.get("/scheduled-actions", response_model=ScheduledActionListResponse)
async def list_scheduled_actions(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[ScheduledActionService, Depends(get_scheduled_action_service)],
    action_status: ScheduledActionStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ScheduledActionListResponse:
    """查看待评估、已抑制和已分发的定时行为。"""
    items = await service.list(
        tenant_id=principal.tenant_id,
        agent_id=request.state.request_identity.agent_id,
        status=action_status,
        limit=limit,
    )
    return ScheduledActionListResponse(items=tuple(_scheduled_response(item) for item in items))


@router.post(
    "/scheduled-actions",
    response_model=ScheduledActionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.PROACTIVE_MANAGE))],
)
async def create_scheduled_action(
    command: ScheduledActionCreate,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[ScheduledActionService, Depends(get_scheduled_action_service)],
    configuration: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> ScheduledActionResponse:
    """创建带 PostgreSQL 真相任务的主动行为候选。"""
    agent_id: UUID = request.state.request_identity.agent_id
    snapshot = await configuration.resolve_effective(
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        user_id=command.user_id,
    )
    max_attempts = snapshot.values.get("tasks.max_attempts", 5)
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="生效配置中的任务最大尝试次数无效",
        )
    try:
        item, _ = await service.create(
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            user_id=command.user_id,
            conversation_id=command.conversation_id,
            kind=command.kind,
            scheduled_for=command.scheduled_for,
            expires_at=command.expires_at,
            idempotency_key=command.idempotency_key,
            reason=command.reason,
            payload=command.payload,
            social_cost=command.social_cost,
            created_by=principal.user_id,
            max_attempts=max_attempts,
            lease_seconds=_integer(snapshot.values, "tasks.lease_seconds"),
            retry_base_seconds=_integer(snapshot.values, "tasks.retry_base_seconds"),
        )
    except TaskValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _scheduled_response(item)


def _integer(values: Mapping[str, JsonValue], key: str) -> int:
    value = values.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"生效配置中的整数参数无效：{key}",
        )
    return value


@router.post(
    "/scheduled-actions/{action_id}/cancel",
    response_model=ScheduledActionResponse,
    dependencies=[Depends(require_permission(AdminPermission.PROACTIVE_MANAGE))],
)
async def cancel_scheduled_action(
    action_id: UUID,
    command: TaskCancelCommand,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[ScheduledActionService, Depends(get_scheduled_action_service)],
) -> ScheduledActionResponse:
    """原子取消行为和它尚未执行的后台任务。"""
    try:
        item = await service.cancel(
            tenant_id=principal.tenant_id,
            agent_id=request.state.request_identity.agent_id,
            action_id=action_id,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except TaskValidationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _scheduled_response(item)
