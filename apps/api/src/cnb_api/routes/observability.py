"""性能、成本、SLO 与活动告警管理 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from cnb_api.dependencies import (
    get_admin_principal,
    get_observability_service,
    get_request_identity,
    require_permission,
)
from cnb_application import ObservabilityService
from cnb_contracts import (
    ActiveAlertResponse,
    AgentRunSloResponse,
    ApiSloResponse,
    ChannelDeliveryMetricsResponse,
    ModelUsageResponse,
    NotificationDeliveryMetricsResponse,
    ObservabilityAlertLifecycleResponse,
    ObservabilityDashboardResponse,
    QueueMetricsResponse,
)
from cnb_domain import (
    AdminPermission,
    AdminPrincipal,
    DevelopmentIdentity,
    ObservabilityAlertLifecycleStatus,
)

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get(
    "/dashboard",
    response_model=ObservabilityDashboardResponse,
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def dashboard(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
) -> ObservabilityDashboardResponse:
    """返回当前租户的聚合指标、冻结成本和确定性活动告警。"""
    result = await service.dashboard(
        tenant_id=principal.tenant_id,
        agent_id=identity.agent_id,
    )
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
        channel_delivery=ChannelDeliveryMetricsResponse.model_validate(
            metrics.channel_delivery, from_attributes=True
        ),
        notification_delivery=NotificationDeliveryMetricsResponse.model_validate(
            metrics.notification_delivery, from_attributes=True
        ),
        total_estimated_cost_microusd=result.total_estimated_cost_microusd,
        alerts=tuple(
            ActiveAlertResponse.model_validate(item, from_attributes=True) for item in result.alerts
        ),
        alert_lifecycles=tuple(
            ObservabilityAlertLifecycleResponse.model_validate(item, from_attributes=True)
            for item in result.alert_lifecycles
        ),
    )


@router.get(
    "/alert-lifecycles",
    response_model=tuple[ObservabilityAlertLifecycleResponse, ...],
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def alert_lifecycles(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    status: ObservabilityAlertLifecycleStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> tuple[ObservabilityAlertLifecycleResponse, ...]:
    rows = await service.alert_lifecycles(
        tenant_id=principal.tenant_id, agent_id=identity.agent_id, status=status, limit=limit
    )
    return tuple(
        ObservabilityAlertLifecycleResponse.model_validate(item, from_attributes=True)
        for item in rows
    )
