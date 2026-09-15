"""性能、成本、服务等级与活动告警管理接口。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cnb_api.dependencies import (
    get_admin_principal,
    get_observability_service,
    get_request_identity,
    require_permission,
)
from cnb_application import (
    ObservabilityNotFoundError,
    ObservabilityService,
    ObservabilityValidationError,
)
from cnb_contracts import (
    ActiveAlertResponse,
    AgentRunSloResponse,
    ApiSloResponse,
    ChannelDeliveryMetricsResponse,
    ModelUsageResponse,
    NotificationDeliveryMetricsResponse,
    ObservabilityAlertBatchDispositionCommand,
    ObservabilityAlertBatchDispositionResponse,
    ObservabilityAlertDispositionClearCommand,
    ObservabilityAlertDispositionCommand,
    ObservabilityAlertDispositionEventResponse,
    ObservabilityAlertDispositionResponse,
    ObservabilityAlertLifecycleMetricsResponse,
    ObservabilityAlertLifecyclePageResponse,
    ObservabilityAlertLifecycleResponse,
    ObservabilityAlertOperationsSummaryResponse,
    ObservabilityAlertReplayMetricsResponse,
    ObservabilityAlertReplayReviewPageResponse,
    ObservabilityAlertReplayReviewResponse,
    ObservabilityAlertSuppressionCommand,
    ObservabilityDashboardResponse,
    QueueMetricsResponse,
)
from cnb_domain import (
    AdminPermission,
    AdminPrincipal,
    AlertSeverity,
    DevelopmentIdentity,
    ObservabilityAlertDisposition,
    ObservabilityAlertDispositionAction,
    ObservabilityAlertLifecycleStatus,
    ObservabilityAlertReplayDecision,
    ObservabilityAlertReplayReason,
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
    """返回当前 Agent 的隔离指标、冻结成本和确定性活动告警。"""
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
    source_type: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    severity: AlertSeverity | None = None,
    minimum_duration_minutes: Annotated[int | None, Query(ge=0, le=525_600)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> tuple[ObservabilityAlertLifecycleResponse, ...]:
    rows = await service.alert_lifecycles(
        tenant_id=principal.tenant_id,
        agent_id=identity.agent_id,
        status=status,
        source_type=source_type,
        severity=severity,
        minimum_duration_minutes=minimum_duration_minutes,
        limit=limit,
    )
    return tuple(
        ObservabilityAlertLifecycleResponse.model_validate(item, from_attributes=True)
        for item in rows
    )


@router.get(
    "/alert-lifecycles/metrics",
    response_model=ObservabilityAlertLifecycleMetricsResponse,
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def alert_lifecycle_metrics(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    window_minutes: Annotated[int, Query(ge=5, le=10_080)] = 1_440,
    bucket_minutes: Annotated[int, Query(ge=5, le=1_440)] = 60,
    source_type: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    severity: AlertSeverity | None = None,
) -> ObservabilityAlertLifecycleMetricsResponse:
    """查询当前 Agent 的通用来源生命周期聚合与固定时间桶趋势。"""
    try:
        metrics = await service.alert_lifecycle_metrics(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
            source_type=source_type,
            severity=severity,
        )
    except ObservabilityValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ObservabilityAlertLifecycleMetricsResponse.model_validate(metrics, from_attributes=True)


@router.get(
    "/alert-operations-summary",
    response_model=ObservabilityAlertOperationsSummaryResponse,
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def alert_operations_summary(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
) -> ObservabilityAlertOperationsSummaryResponse:
    """返回当前 Agent 的告警异常基线与值班交接摘要。"""
    try:
        summary = await service.alert_operations_summary(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
        )
    except ObservabilityValidationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ObservabilityAlertOperationsSummaryResponse.model_validate(
        summary,
        from_attributes=True,
    )


@router.get(
    "/alert-lifecycles/page",
    response_model=ObservabilityAlertLifecyclePageResponse,
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def alert_lifecycle_page(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    status_filter: Annotated[
        ObservabilityAlertLifecycleStatus | None, Query(alias="status")
    ] = None,
    source_type: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    severity: AlertSeverity | None = None,
    minimum_duration_minutes: Annotated[int | None, Query(ge=0, le=525_600)] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ObservabilityAlertLifecyclePageResponse:
    """按来源和组合筛选稳定下钻通用告警生命周期。"""
    try:
        page = await service.alert_lifecycle_page(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            status=status_filter,
            source_type=source_type,
            severity=severity,
            minimum_duration_minutes=minimum_duration_minutes,
            cursor=cursor,
            limit=limit,
        )
    except ObservabilityValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ObservabilityAlertLifecyclePageResponse(
        items=tuple(
            ObservabilityAlertLifecycleResponse.model_validate(item, from_attributes=True)
            for item in page.items
        ),
        next_cursor=page.next_cursor,
    )


def _disposition_response(
    lifecycle_id: UUID,
    item: ObservabilityAlertDisposition,
    *,
    cleared: bool = False,
) -> ObservabilityAlertDispositionResponse:
    return ObservabilityAlertDispositionResponse(
        lifecycle_id=lifecycle_id,
        source_type=item.source_type,
        source_key=item.source_key,
        code=item.code,
        status="cleared" if cleared else item.status.value,
        reason=item.reason,
        expires_at=item.expires_at,
        updated_at=item.updated_at,
    )


@router.get(
    "/alert-disposition-events",
    response_model=tuple[ObservabilityAlertDispositionEventResponse, ...],
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def alert_disposition_events(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    lifecycle_id: UUID | None = None,
    source_type: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    source_key: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    action: ObservabilityAlertDispositionAction | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> tuple[ObservabilityAlertDispositionEventResponse, ...]:
    """查询当前 Agent 的通用告警处置历史时间线。"""
    try:
        rows = await service.disposition_events(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            lifecycle_id=lifecycle_id,
            source_type=source_type,
            source_key=source_key,
            action=action,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            limit=limit,
        )
    except ObservabilityValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return tuple(
        ObservabilityAlertDispositionEventResponse.model_validate(item, from_attributes=True)
        for item in rows
    )


@router.get(
    "/alert-replay-reviews/metrics",
    response_model=ObservabilityAlertReplayMetricsResponse,
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def alert_replay_metrics(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    window_minutes: Annotated[int, Query(ge=5, le=10_080)] = 1_440,
    source_type: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
) -> ObservabilityAlertReplayMetricsResponse:
    """聚合当前 Agent 的通用告警通知重放复核结果。"""
    try:
        metrics = await service.alert_replay_metrics(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            window_minutes=window_minutes,
            source_type=source_type,
        )
    except ObservabilityValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ObservabilityAlertReplayMetricsResponse.model_validate(metrics, from_attributes=True)


@router.get(
    "/alert-replay-reviews",
    response_model=ObservabilityAlertReplayReviewPageResponse,
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def alert_replay_reviews(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    decision: ObservabilityAlertReplayDecision | None = None,
    reason_code: ObservabilityAlertReplayReason | None = None,
    source_type: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ObservabilityAlertReplayReviewPageResponse:
    """分页查询当前 Agent 的通用告警通知重放复核事件。"""
    try:
        page = await service.alert_replay_reviews(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            decision=decision,
            reason_code=reason_code,
            source_type=source_type,
            cursor=cursor,
            limit=limit,
        )
    except ObservabilityValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ObservabilityAlertReplayReviewPageResponse(
        items=tuple(
            ObservabilityAlertReplayReviewResponse.model_validate(item, from_attributes=True)
            for item in page.items
        ),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/alert-lifecycles/batch-disposition",
    response_model=ObservabilityAlertBatchDispositionResponse,
    dependencies=[Depends(require_permission(AdminPermission.OBSERVABILITY_ALERT_MANAGE))],
)
async def batch_disposition(
    command: ObservabilityAlertBatchDispositionCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
) -> ObservabilityAlertBatchDispositionResponse:
    """原子确认、临时抑制或解除多个通用告警。"""
    try:
        rows = await service.batch_disposition(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            lifecycle_ids=command.lifecycle_ids,
            action=command.action,
            reason=command.reason,
            expires_at=command.expires_at,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except ObservabilityNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ObservabilityValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ObservabilityAlertBatchDispositionResponse(
        items=tuple(
            _disposition_response(
                item.lifecycle_id,
                item.disposition,
                cleared=command.action == "clear",
            )
            for item in rows
        )
    )


@router.post(
    "/alert-lifecycles/{lifecycle_id}/acknowledge",
    response_model=ObservabilityAlertDispositionResponse,
    dependencies=[Depends(require_permission(AdminPermission.OBSERVABILITY_ALERT_MANAGE))],
)
async def acknowledge_alert(
    lifecycle_id: UUID,
    command: ObservabilityAlertDispositionCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
) -> ObservabilityAlertDispositionResponse:
    """确认当前 Agent 的活动通用告警。"""
    try:
        item = await service.acknowledge_alert(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            lifecycle_id=lifecycle_id,
            reason=command.reason,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except ObservabilityNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ObservabilityValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _disposition_response(lifecycle_id, item)


@router.post(
    "/alert-lifecycles/{lifecycle_id}/suppress",
    response_model=ObservabilityAlertDispositionResponse,
    dependencies=[Depends(require_permission(AdminPermission.OBSERVABILITY_ALERT_MANAGE))],
)
async def suppress_alert(
    lifecycle_id: UUID,
    command: ObservabilityAlertSuppressionCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
) -> ObservabilityAlertDispositionResponse:
    """临时抑制当前 Agent 的活动通用告警通知。"""
    try:
        item = await service.suppress_alert(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            lifecycle_id=lifecycle_id,
            reason=command.reason,
            expires_at=command.expires_at,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except ObservabilityNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ObservabilityValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _disposition_response(lifecycle_id, item)


@router.post(
    "/alert-lifecycles/{lifecycle_id}/clear-disposition",
    response_model=ObservabilityAlertDispositionResponse,
    dependencies=[Depends(require_permission(AdminPermission.OBSERVABILITY_ALERT_MANAGE))],
)
async def clear_alert_disposition(
    lifecycle_id: UUID,
    command: ObservabilityAlertDispositionClearCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
) -> ObservabilityAlertDispositionResponse:
    """解除当前 Agent 的通用告警处置。"""
    try:
        item = await service.clear_alert_disposition(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            lifecycle_id=lifecycle_id,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except ObservabilityNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ObservabilityValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _disposition_response(lifecycle_id, item, cleared=True)
