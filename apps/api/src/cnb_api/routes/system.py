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
from cnb_infrastructure import DependencyProbe, Settings

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
    """返回管理总览所需的安全聚合数据和可选真实依赖状态。"""
    settings: Settings = request.app.state.settings
    counts = await administration.get_overview(tenant_id=principal.tenant_id)
    task_counts = await tasks.counts(tenant_id=principal.tenant_id)
    if settings.readiness_deep_checks:
        dependency_probe: DependencyProbe = request.app.state.dependency_probe
        dependency_components = await dependency_probe(settings)
    else:
        dependency_components = tuple(
            ComponentHealth(
                name=name,
                status="not_checked",
                detail="启动配置未启用深度依赖检查。",
            )
            for name in ("postgresql", "redis", "object_storage")
        )
    return SystemOverviewResponse(
        environment=settings.environment,
        version=__version__,
        active_agents=counts.active_agents,
        active_conversations=counts.active_conversations,
        pending_jobs=task_counts.pending + task_counts.retrying,
        configuration_definitions=len(registry),
        components=(
            ComponentHealth(name="api", status="healthy"),
            *dependency_components,
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
        authentication_mode=settings.authentication_mode,
        oidc_configured=bool(
            settings.oidc_issuer_url
            and settings.oidc_client_id
            and settings.oidc_tenant_id
            and settings.oidc_agent_id
        ),
        otel_enabled=settings.otel_enabled,
        otel_exporter_configured=bool(settings.otel_exporter_otlp_endpoint),
        otel_service_name=settings.otel_service_name,
        otel_trace_sample_ratio=settings.otel_trace_sample_ratio,
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
    """返回 PostgreSQL 任务真相计数和可验证的任务进程心跳。"""
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
                f"{len(workers)} 个任务进程心跳正常。"
                if workers
                else "最近 45 秒没有收到任务进程心跳。"
            ),
        ),
    )
