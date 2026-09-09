"""管理后台聚合总览接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from cnb_api import __version__
from cnb_api.dependencies import get_configuration_registry, require_permission
from cnb_application import ConfigurationRegistry
from cnb_contracts import ComponentHealth, SystemOverviewResponse
from cnb_domain import AdminPermission
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
) -> SystemOverviewResponse:
    """返回首版管理总览所需的安全聚合数据。"""
    settings: Settings = request.app.state.settings
    return SystemOverviewResponse(
        environment=settings.environment,
        version=__version__,
        active_agents=0,
        active_conversations=0,
        pending_jobs=0,
        configuration_definitions=len(registry),
        components=(
            ComponentHealth(name="api", status="healthy"),
            ComponentHealth(name="postgresql", status="not_checked"),
            ComponentHealth(name="redis", status="not_checked"),
            ComponentHealth(name="object_storage", status="not_checked"),
        ),
    )
