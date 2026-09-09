"""管理后台聚合总览接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from cnb_api import __version__
from cnb_api.dependencies import (
    get_admin_principal,
    get_administration_service,
    get_configuration_registry,
    get_task_service,
    require_permission,
)
from cnb_application import AdministrationService, BackgroundTaskService, ConfigurationRegistry
from cnb_contracts import (
    BootstrapSettingsResponse,
    ComponentHealth,
    SystemOverviewResponse,
    TaskStatusResponse,
)
from cnb_domain import AdminPermission, AdminPrincipal
from cnb_infrastructure import Settings

router = APIRouter(
    prefix="/system",
    tags=["system"],
    dependencies=[Depends(require_permission(AdminPermission.DASHBOARD_READ))],
)


@router.get("/overview", response_model=SystemOverviewResponse)
async def overview(
    request: Request,
    registry: Annotated[ConfigurationRegistry, Depends(get_configuration_registry)],
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    administration: Annotated[AdministrationService, Depends(get_administration_service)],
    tasks: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> SystemOverviewResponse:
    """返回首版管理总览所需的安全聚合数据。"""
    settings: Settings = request.app.state.settings
    counts = await administration.get_overview(tenant_id=principal.tenant_id)
    task_counts = await tasks.counts(tenant_id=principal.tenant_id)
    return SystemOverviewResponse(
        environment=settings.environment,
        version=__version__,
        active_agents=counts.active_agents,
        active_conversations=counts.active_conversations,
        pending_jobs=task_counts.pending + task_counts.retrying,
        configuration_definitions=len(registry),
        components=(
            ComponentHealth(name="api", status="healthy"),
            ComponentHealth(name="postgresql", status="not_checked"),
            ComponentHealth(name="redis", status="not_checked"),
            ComponentHealth(name="object_storage", status="not_checked"),
        ),
    )


@router.get("/settings", response_model=BootstrapSettingsResponse)
async def bootstrap_settings(request: Request) -> BootstrapSettingsResponse:
    """展示安全的启动设置摘要，不返回数据库、Redis、MinIO 或主密钥明文。"""
    settings: Settings = request.app.state.settings
    master_key = settings.config_master_key.get_secret_value()
    return BootstrapSettingsResponse(
        environment=settings.environment,
        log_level=settings.log_level,
        cors_origins=settings.cors_origins,
        readiness_deep_checks=settings.readiness_deep_checks,
        minio_endpoint_url=settings.minio_endpoint_url,
        minio_bucket=settings.minio_bucket,
        database_configured=bool(settings.database_url.get_secret_value()),
        redis_configured=bool(settings.redis_url.get_secret_value()),
        minio_credentials_configured=bool(
            settings.minio_access_key.get_secret_value()
            and settings.minio_secret_key.get_secret_value()
        ),
        config_master_key_status=(
            "development_placeholder"
            if master_key == "development-only-placeholder"
            else "configured"
        ),
    )


@router.get("/tasks/status", response_model=TaskStatusResponse)
async def task_status(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    tasks: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> TaskStatusResponse:
    """返回 PostgreSQL 任务真相计数和可验证的 Worker 心跳。"""
    counts = await tasks.counts(tenant_id=principal.tenant_id)
    workers = await tasks.active_workers()
    return TaskStatusResponse(
        pending_jobs=counts.pending,
        running_jobs=counts.running,
        retrying_jobs=counts.retrying,
        dead_letter_jobs=counts.dead_letters,
        scheduled_actions=counts.scheduled,
        worker=ComponentHealth(
            name="worker",
            status="healthy" if workers else "not_checked",
            detail=(
                f"{len(workers)} 个 Worker 心跳正常。"
                if workers
                else "最近 45 秒没有收到 Worker 心跳。"
            ),
        ),
    )
