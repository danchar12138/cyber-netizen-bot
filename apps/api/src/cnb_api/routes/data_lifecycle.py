"""用户数据导出、遗忘、保留期、MinIO 清理与备份演练接口。"""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from cnb_api.dependencies import get_data_lifecycle_service, require_permission
from cnb_application import (
    DataLifecycleNotFoundError,
    DataLifecycleOperationError,
    DataLifecycleService,
    DataLifecycleValidationError,
)
from cnb_contracts import (
    BackupRestoreDrillCommand,
    ConfirmedLifecycleCommand,
    DataLifecycleOverviewResponse,
    LifecyclePolicyResponse,
    LifecycleRunResponse,
    ObservabilityAlertHistoryExportCommand,
    UserDataExportCommand,
    UserDataForgetCommand,
)
from cnb_domain import AdminPermission, LifecycleRun

router = APIRouter(prefix="/data-lifecycle", tags=["data-lifecycle"])


def _run_response(run: LifecycleRun) -> LifecycleRunResponse:
    return LifecycleRunResponse.model_validate(run, from_attributes=True)


def _raise_http_error(error: Exception) -> NoReturn:
    if isinstance(error, DataLifecycleNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if isinstance(error, DataLifecycleValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(error),
    ) from error


@router.get(
    "/overview",
    response_model=DataLifecycleOverviewResponse,
    dependencies=[Depends(require_permission(AdminPermission.DATA_LIFECYCLE_READ))],
)
async def lifecycle_overview(
    service: Annotated[DataLifecycleService, Depends(get_data_lifecycle_service)],
) -> DataLifecycleOverviewResponse:
    """返回生效保留策略和最近安全运行证据。"""
    policy = await service.policy()
    runs = await service.list_runs(limit=20)
    return DataLifecycleOverviewResponse(
        policy=LifecyclePolicyResponse.model_validate(policy, from_attributes=True),
        runs=tuple(_run_response(item) for item in runs),
    )


@router.get(
    "/runs",
    response_model=tuple[LifecycleRunResponse, ...],
    dependencies=[Depends(require_permission(AdminPermission.DATA_LIFECYCLE_READ))],
)
async def list_lifecycle_runs(
    service: Annotated[DataLifecycleService, Depends(get_data_lifecycle_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> tuple[LifecycleRunResponse, ...]:
    return tuple(_run_response(item) for item in await service.list_runs(limit=limit))


@router.post(
    "/exports",
    response_class=Response,
    dependencies=[Depends(require_permission(AdminPermission.DATA_EXPORT))],
)
async def export_user_data(
    command: UserDataExportCommand,
    service: Annotated[DataLifecycleService, Depends(get_data_lifecycle_service)],
) -> Response:
    """生成一次性 JSON 下载；服务端不落盘，也不导出内部安全字段。"""
    try:
        artifact = await service.export_user_data(command.user_id)
    except (
        DataLifecycleNotFoundError,
        DataLifecycleOperationError,
        DataLifecycleValidationError,
    ) as error:
        _raise_http_error(error)
    return Response(
        content=artifact.content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Content-SHA256": artifact.sha256,
            "X-Export-Run-ID": str(artifact.run.id),
        },
    )


@router.post(
    "/observability-alert-history/exports",
    response_class=Response,
    dependencies=[Depends(require_permission(AdminPermission.DATA_EXPORT))],
)
async def export_observability_alert_history(
    command: ObservabilityAlertHistoryExportCommand,
    service: Annotated[DataLifecycleService, Depends(get_data_lifecycle_service)],
) -> Response:
    """生成当前 Agent 的一次性安全告警运营历史 JSON 下载。"""
    try:
        artifact = await service.export_observability_alert_history(
            window_minutes=command.window_minutes
        )
    except (DataLifecycleOperationError, DataLifecycleValidationError) as error:
        _raise_http_error(error)
    return Response(
        content=artifact.content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Content-SHA256": artifact.sha256,
            "X-Export-Run-ID": str(artifact.run.id),
        },
    )


@router.post(
    "/forget",
    response_model=LifecycleRunResponse,
    dependencies=[Depends(require_permission(AdminPermission.DATA_FORGET))],
)
async def forget_user_data(
    command: UserDataForgetCommand,
    service: Annotated[DataLifecycleService, Depends(get_data_lifecycle_service)],
) -> LifecycleRunResponse:
    """清除用户正文、来源、向量、关系、会话副本和身份绑定。"""
    try:
        return _run_response(
            await service.forget_user_data(command.user_id, confirmation=command.confirmation)
        )
    except (
        DataLifecycleNotFoundError,
        DataLifecycleOperationError,
        DataLifecycleValidationError,
    ) as error:
        _raise_http_error(error)


@router.post(
    "/retention/cleanup",
    response_model=LifecycleRunResponse,
    dependencies=[Depends(require_permission(AdminPermission.DATA_RETENTION_MANAGE))],
)
async def retention_cleanup(
    command: ConfirmedLifecycleCommand,
    service: Annotated[DataLifecycleService, Depends(get_data_lifecycle_service)],
) -> LifecycleRunResponse:
    try:
        return _run_response(await service.run_retention_cleanup(confirmed=command.confirmed))
    except (
        DataLifecycleNotFoundError,
        DataLifecycleOperationError,
        DataLifecycleValidationError,
    ) as error:
        _raise_http_error(error)


@router.post(
    "/objects/orphans/cleanup",
    response_model=LifecycleRunResponse,
    dependencies=[Depends(require_permission(AdminPermission.DATA_RETENTION_MANAGE))],
)
async def orphan_cleanup(
    command: ConfirmedLifecycleCommand,
    service: Annotated[DataLifecycleService, Depends(get_data_lifecycle_service)],
) -> LifecycleRunResponse:
    try:
        return _run_response(await service.run_orphan_cleanup(confirmed=command.confirmed))
    except (
        DataLifecycleNotFoundError,
        DataLifecycleOperationError,
        DataLifecycleValidationError,
    ) as error:
        _raise_http_error(error)


@router.post(
    "/backup-drills",
    response_model=LifecycleRunResponse,
    dependencies=[Depends(require_permission(AdminPermission.BACKUP_DRILL_RECORD))],
)
async def record_backup_restore_drill(
    command: BackupRestoreDrillCommand,
    service: Annotated[DataLifecycleService, Depends(get_data_lifecycle_service)],
) -> LifecycleRunResponse:
    """登记隔离恢复后的清单摘要、计数与三项完整性证据。"""
    try:
        return _run_response(await service.record_backup_restore_drill(**command.model_dump()))
    except (DataLifecycleNotFoundError, DataLifecycleValidationError) as error:
        _raise_http_error(error)
