"""性能、成本、SLO 与活动告警管理 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from cnb_api.dependencies import get_admin_principal, get_observability_service, require_permission
from cnb_application import ObservabilityService
from cnb_contracts import (
    ActiveAlertResponse,
    AgentRunSloResponse,
    ApiSloResponse,
    ModelUsageResponse,
    ObservabilityDashboardResponse,
    QueueMetricsResponse,
)
from cnb_domain import AdminPermission, AdminPrincipal

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get(
    "/dashboard",
    response_model=ObservabilityDashboardResponse,
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def dashboard(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
) -> ObservabilityDashboardResponse:
    """返回当前租户的聚合指标、冻结成本和确定性活动告警。"""
    result = await service.dashboard(tenant_id=principal.tenant_id)
    metrics = result.metrics
    return ObservabilityDashboardResponse(
        window_started_at=metrics.window_started_at,
        window_ended_at=metrics.window_ended_at,
        api=ApiSloResponse.model_validate(metrics.api, from_attributes=True),
        agent_runs=AgentRunSloResponse.model_validate(metrics.agent_runs, from_attributes=True),
        models=tuple(
            ModelUsageResponse.model_validate(item, from_attributes=True) for item in metrics.models
        ),
        queue=QueueMetricsResponse.model_validate(metrics.queue, from_attributes=True),
        total_estimated_cost_microusd=result.total_estimated_cost_microusd,
        alerts=tuple(
            ActiveAlertResponse.model_validate(item, from_attributes=True) for item in result.alerts
        ),
    )
