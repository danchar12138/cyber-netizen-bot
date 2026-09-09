"""管理后台聚合总览接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from cnb_api import __version__
from cnb_api.dependencies import (
    get_admin_principal,
    get_administration_service,
    get_configuration_registry,
    require_permission,
)
from cnb_application import AdministrationService, ConfigurationRegistry
from cnb_contracts import ComponentHealth, SystemOverviewResponse
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
) -> SystemOverviewResponse:
    """返回首版管理总览所需的安全聚合数据。"""
    settings: Settings = request.app.state.settings
    counts = await administration.get_overview(tenant_id=principal.tenant_id)
    return SystemOverviewResponse(
        environment=settings.environment,
        version=__version__,
        active_agents=counts.active_agents,
        active_conversations=counts.active_conversations,
        pending_jobs=counts.pending_jobs,
        configuration_definitions=len(registry),
        components=(
            ComponentHealth(name="api", status="healthy"),
            ComponentHealth(name="postgresql", status="not_checked"),
            ComponentHealth(name="redis", status="not_checked"),
            ComponentHealth(name="object_storage", status="not_checked"),
        ),
    )
