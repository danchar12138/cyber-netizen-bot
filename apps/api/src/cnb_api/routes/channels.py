"""多模态能力、渠道实例、模拟器、凭证与诊断管理 API。"""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cnb_adapters import ChannelAdapterError, ChannelCapabilityError, ChannelNotConfiguredError
from cnb_api.dependencies import (
    get_alert_notification_service,
    get_channel_service,
    get_configuration_service,
    get_request_identity,
    require_permission,
)
from cnb_application import (
    AlertNotificationDeliveryError,
    AlertNotificationService,
    AlertNotificationValidationError,
    ChannelAlert,
    ChannelConflictError,
    ChannelErrorMetrics,
    ChannelHealthSnapshot,
    ChannelInstanceView,
    ChannelNotFoundError,
    ChannelOperationMetrics,
    ChannelService,
    ChannelValidationError,
    ConfigurationService,
    TelegramWebhookStatus,
)
from cnb_contracts import (
    ChannelAlertDispositionClearCommand,
    ChannelAlertDispositionCommand,
    ChannelAlertDispositionResponse,
    ChannelAlertLifecycleListResponse,
    ChannelAlertLifecycleMetricsResponse,
    ChannelAlertLifecycleResponse,
    ChannelAlertListResponse,
    ChannelAlertNotificationCommand,
    ChannelAlertNotificationJobResponse,
    ChannelAlertNotificationResponse,
    ChannelAlertResponse,
    ChannelCapabilitiesResponse,
    ChannelCatalogListResponse,
    ChannelCatalogResponse,
    ChannelConfirmedCommand,
    ChannelCredentialCommand,
    ChannelDeliveryRequest,
    ChannelDeliveryResponse,
    ChannelDiagnosticEventListResponse,
    ChannelDiagnosticEventResponse,
    ChannelErrorMetricListResponse,
    ChannelErrorMetricResponse,
    ChannelHealthSnapshotResponse,
    ChannelHealthTrendResponse,
    ChannelInboundResponse,
    ChannelInboundSimulationCommand,
    ChannelInstanceCreate,
    ChannelInstanceListResponse,
    ChannelInstanceResponse,
    ChannelInstanceUpdate,
    ChannelOperationMetricsListResponse,
    ChannelOperationMetricsResponse,
    ChannelSimulationCommand,
    ChannelSimulationResponse,
    ModelCapabilityMatrixResponse,
    ModelCapabilityResponse,
    MultimodalContentBlockInput,
    MultimodalContentBlockResponse,
    NotificationDeliveryTimelineItemResponse,
    NotificationDeliveryTimelineResponse,
    TelegramWebhookClearCommand,
    TelegramWebhookRegisterCommand,
    TelegramWebhookStatusResponse,
)
from cnb_domain import (
    AdminPermission,
    AdminPrincipal,
    AlertSeverity,
    BackgroundJobStatus,
    ChannelAlertDisposition,
    ChannelAlertDispositionStatus,
    ChannelAlertLifecycleStatus,
    ChannelCapabilities,
    ChannelDiagnosticEvent,
    DevelopmentIdentity,
    MultimodalContentBlock,
)

router = APIRouter(prefix="/channels", tags=["channels"])


def _capabilities(value: ChannelCapabilities) -> ChannelCapabilitiesResponse:
    return ChannelCapabilitiesResponse.model_validate(asdict(value))


def _block_input(value: MultimodalContentBlockInput) -> MultimodalContentBlock:
    return MultimodalContentBlock(**value.model_dump())


def _block_response(value: MultimodalContentBlock) -> MultimodalContentBlockResponse:
    return MultimodalContentBlockResponse.model_validate(asdict(value))


def _instance_response(value: ChannelInstanceView) -> ChannelInstanceResponse:
    item = value.instance
    return ChannelInstanceResponse(
        id=item.id,
        tenant_id=item.tenant_id,
        agent_id=item.agent_id,
        name=item.name,
        platform=item.platform,
        display_name=value.display_name,
        implementation_status=value.implementation_status,
        status=item.status,
        rate_limit_per_minute=item.rate_limit_per_minute,
        settings=item.settings,
        credential_configured=value.credential_configured,
        inbound_webhook_configured=value.inbound_webhook_configured,
        capabilities=_capabilities(value.capabilities),
        health_status=item.health_status,
        health_detail=item.health_detail,
        last_checked_at=item.last_checked_at,
        created_by=item.created_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _event_response(value: ChannelDiagnosticEvent) -> ChannelDiagnosticEventResponse:
    return ChannelDiagnosticEventResponse(
        id=value.id,
        channel_id=value.channel_id,
        direction=value.direction,
        event_type=value.event_type,
        status=value.status,
        external_event_id=value.external_event_id,
        idempotency_key=value.idempotency_key,
        external_message_id=value.external_message_id,
        payload_summary=value.payload_summary,
        error_code=value.error_code,
        degradations=value.degradations,
        occurred_at=value.occurred_at,
    )


def _webhook_response(value: TelegramWebhookStatus) -> TelegramWebhookStatusResponse:
    return TelegramWebhookStatusResponse(
        channel_id=value.channel_id,
        status=value.status,
        configured=value.configured,
        pending_update_count=value.pending_update_count,
        last_error_at=value.last_error_at,
        last_error_present=value.last_error_present,
        allowed_updates=value.allowed_updates,
        checked_at=value.checked_at,
    )


def _operation_metrics_response(value: ChannelOperationMetrics) -> ChannelOperationMetricsResponse:
    return ChannelOperationMetricsResponse(
        channel_id=value.channel_id,
        window_started_at=value.window_started_at,
        window_ended_at=value.window_ended_at,
        inbound_events=value.inbound_events,
        outbound_events=value.outbound_events,
        outbound_delivered=value.outbound_delivered,
        outbound_degraded=value.outbound_degraded,
        outbound_failed=value.outbound_failed,
        outbound_rate_limited=value.outbound_rate_limited,
        outbound_attempts=value.outbound_attempts,
        outbound_failure_rate_percent=value.outbound_failure_rate_percent,
        last_failure_at=value.last_failure_at,
    )


def _error_metric_response(value: ChannelErrorMetrics) -> ChannelErrorMetricResponse:
    return ChannelErrorMetricResponse(
        channel_id=value.channel_id,
        error_code=value.error_code,
        occurrences=value.occurrences,
        first_occurred_at=value.first_occurred_at,
        last_occurred_at=value.last_occurred_at,
    )


def _health_snapshot_response(value: ChannelHealthSnapshot) -> ChannelHealthSnapshotResponse:
    return ChannelHealthSnapshotResponse(
        id=value.id,
        channel_id=value.channel_id,
        platform=value.platform,
        status=value.status,
        configured=value.configured,
        pending_update_count=value.pending_update_count,
        remote_error_present=value.remote_error_present,
        sampled_at=value.sampled_at,
    )


def _alert_response(value: ChannelAlert) -> ChannelAlertResponse:
    return ChannelAlertResponse.model_validate(value, from_attributes=True)


def _disposition_response(
    value: ChannelAlertDisposition,
) -> ChannelAlertDispositionResponse:
    return ChannelAlertDispositionResponse(
        alert_key=value.alert_key,
        channel_id=value.channel_id,
        status=value.status.value,
        reason=value.reason,
        expires_at=value.expires_at,
        updated_at=value.updated_at,
    )


@router.get(
    "/catalog",
    response_model=ChannelCatalogListResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def list_catalog(
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelCatalogListResponse:
    """列出正式 Web/Telegram 适配器与其余即时通讯占位能力。"""
    return ChannelCatalogListResponse(
        items=tuple(
            ChannelCatalogResponse(
                platform=item.platform,
                display_name=item.display_name,
                implementation_status=item.implementation_status,
                credential_required=item.credential_required,
                capabilities=_capabilities(item.capabilities),
            )
            for item in service.catalog()
        )
    )


@router.get(
    "/model-capabilities",
    response_model=ModelCapabilityMatrixResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def model_capabilities() -> ModelCapabilityMatrixResponse:
    """返回当前内置模型服务的稳定能力矩阵。"""
    return ModelCapabilityMatrixResponse(
        items=(
            ModelCapabilityResponse(
                provider="development",
                model_family="friendly-echo-v1",
                text_input=True,
                image_input=False,
                document_input=False,
                streaming=True,
                structured_output=False,
                tool_calling=False,
            ),
            ModelCapabilityResponse(
                provider="openai",
                model_family="responses-api",
                text_input=True,
                image_input=True,
                document_input=True,
                streaming=True,
                structured_output=True,
                tool_calling=True,
            ),
        )
    )


@router.get(
    "",
    response_model=ChannelInstanceListResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def list_instances(
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelInstanceListResponse:
    return ChannelInstanceListResponse(
        items=tuple(
            _instance_response(item)
            for item in await service.list(
                tenant_id=principal.tenant_id,
                agent_id=identity.agent_id,
            )
        )
    )


@router.get(
    "/operations/metrics",
    response_model=ChannelOperationMetricsListResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def operation_metrics(
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    service: Annotated[ChannelService, Depends(get_channel_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    channel_id: Annotated[UUID | None, Query()] = None,
    window_minutes: Annotated[int, Query(ge=5, le=1_440)] = 60,
) -> ChannelOperationMetricsListResponse:
    ended_at = datetime.now(UTC)
    try:
        metrics = await service.operation_metrics(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            window_minutes=window_minutes,
            now=ended_at,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    if metrics:
        window_started_at = metrics[0].window_started_at
        window_ended_at = metrics[0].window_ended_at
    else:
        window_ended_at = ended_at
        window_started_at = ended_at - timedelta(minutes=window_minutes)
    return ChannelOperationMetricsListResponse(
        window_started_at=window_started_at,
        window_ended_at=window_ended_at,
        items=tuple(_operation_metrics_response(item) for item in metrics),
    )


@router.get(
    "/operations/errors",
    response_model=ChannelErrorMetricListResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def error_metrics(
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    service: Annotated[ChannelService, Depends(get_channel_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    channel_id: Annotated[UUID | None, Query()] = None,
    window_minutes: Annotated[int, Query(ge=5, le=1_440)] = 60,
) -> ChannelErrorMetricListResponse:
    ended_at = datetime.now(UTC)
    try:
        metrics = await service.error_metrics(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            window_minutes=window_minutes,
            now=ended_at,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ChannelErrorMetricListResponse(
        window_started_at=ended_at - timedelta(minutes=window_minutes),
        window_ended_at=ended_at,
        items=tuple(_error_metric_response(item) for item in metrics),
    )


@router.get(
    "/health/trend",
    response_model=ChannelHealthTrendResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def health_trend(
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    service: Annotated[ChannelService, Depends(get_channel_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    channel_id: Annotated[UUID | None, Query()] = None,
    window_minutes: Annotated[int, Query(ge=5, le=10_080)] = 1_440,
    limit: Annotated[int, Query(ge=1, le=2_000)] = 500,
) -> ChannelHealthTrendResponse:
    ended_at = datetime.now(UTC)
    try:
        snapshots = await service.health_trend(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            window_minutes=window_minutes,
            limit=limit,
            now=ended_at,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ChannelHealthTrendResponse(
        window_started_at=ended_at - timedelta(minutes=window_minutes),
        window_ended_at=ended_at,
        items=tuple(_health_snapshot_response(item) for item in snapshots),
    )


@router.get(
    "/operations/alerts",
    response_model=ChannelAlertListResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def channel_alerts(
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    service: Annotated[ChannelService, Depends(get_channel_service)],
    configuration: Annotated[ConfigurationService, Depends(get_configuration_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    channel_id: Annotated[UUID | None, Query()] = None,
    window_minutes: Annotated[int, Query(ge=5, le=1_440)] = 60,
) -> ChannelAlertListResponse:
    effective = await configuration.resolve_effective(
        tenant_id=principal.tenant_id,
        agent_id=identity.agent_id,
        channel_id=channel_id,
    )
    ended_at = datetime.now(UTC)
    try:
        alerts = await service.alerts(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            values=effective.values,
            window_minutes=window_minutes,
            now=ended_at,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (ChannelValidationError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ChannelAlertListResponse(
        window_started_at=ended_at - timedelta(minutes=window_minutes),
        window_ended_at=ended_at,
        items=tuple(_alert_response(item) for item in alerts),
    )


@router.get(
    "/operations/alerts/lifecycles",
    response_model=ChannelAlertLifecycleListResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def channel_alert_lifecycles(
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    service: Annotated[ChannelService, Depends(get_channel_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    channel_id: Annotated[UUID | None, Query()] = None,
    lifecycle_status: Annotated[ChannelAlertLifecycleStatus | None, Query(alias="status")] = None,
    severity: Annotated[AlertSeverity | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ChannelAlertLifecycleListResponse:
    """查询当前 Agent 的安全告警生命周期历史。"""
    try:
        rows = await service.alert_lifecycles(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            status=lifecycle_status,
            severity=severity,
            limit=limit,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ChannelAlertLifecycleListResponse(
        items=tuple(
            ChannelAlertLifecycleResponse.model_validate(item, from_attributes=True)
            for item in rows
        )
    )


@router.get(
    "/operations/alerts/lifecycles/metrics",
    response_model=ChannelAlertLifecycleMetricsResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def channel_alert_lifecycle_metrics(
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    service: Annotated[ChannelService, Depends(get_channel_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    window_minutes: Annotated[int, Query(ge=5, le=10_080)] = 1_440,
    bucket_minutes: Annotated[int, Query(ge=5, le=1_440)] = 60,
) -> ChannelAlertLifecycleMetricsResponse:
    """查询当前 Agent 的生命周期聚合、恢复耗时与时间桶趋势。"""
    try:
        metrics = await service.alert_lifecycle_metrics(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )
    except ChannelValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ChannelAlertLifecycleMetricsResponse.model_validate(metrics, from_attributes=True)


@router.get(
    "/operations/alerts/notifications",
    response_model=NotificationDeliveryTimelineResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def notification_delivery_timeline(
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[AlertNotificationService, Depends(get_alert_notification_service)],
    notification_status: Annotated[
        BackgroundJobStatus | None,
        Query(alias="status"),
    ] = None,
    adapter: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    event: Literal["active", "escalation", "recovery", "unknown"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> NotificationDeliveryTimelineResponse:
    """查询当前 Agent 的通知状态、连续失败计数和安全投递时间线。"""
    try:
        timeline = await service.timeline(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            status=notification_status,
            adapter=adapter,
            event=event,
            limit=limit,
        )
    except AlertNotificationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return NotificationDeliveryTimelineResponse(
        total=timeline.total,
        pending=timeline.pending,
        running=timeline.running,
        retrying=timeline.retrying,
        succeeded=timeline.succeeded,
        failed=timeline.failed,
        dead_letters=timeline.dead_letters,
        current_consecutive_failures=timeline.current_consecutive_failures,
        last_succeeded_at=timeline.last_succeeded_at,
        items=tuple(
            NotificationDeliveryTimelineItemResponse.model_validate(item, from_attributes=True)
            for item in timeline.items
        ),
    )


async def _set_alert_disposition(
    *,
    channel_id: UUID,
    command: ChannelAlertDispositionCommand,
    disposition_status: ChannelAlertDispositionStatus,
    principal: AdminPrincipal,
    identity: DevelopmentIdentity,
    service: ChannelService,
    configuration: ConfigurationService,
) -> ChannelAlertDispositionResponse:
    effective = await configuration.resolve_effective(
        tenant_id=principal.tenant_id,
        agent_id=identity.agent_id,
        channel_id=channel_id,
    )
    try:
        disposition = await service.set_alert_disposition(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            alert_key=command.alert_key,
            status=disposition_status,
            reason=command.reason,
            expires_at=command.expires_at,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
            values=effective.values,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="告警不存在") from error
    except ChannelValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _disposition_response(disposition)


@router.post(
    "/operations/alerts/{channel_id}/acknowledge",
    response_model=ChannelAlertDispositionResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_ALERT_MANAGE))],
)
async def acknowledge_alert(
    channel_id: UUID,
    command: ChannelAlertDispositionCommand,
    principal: Annotated[
        AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_ALERT_MANAGE))
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
    configuration: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> ChannelAlertDispositionResponse:
    return await _set_alert_disposition(
        channel_id=channel_id,
        command=command,
        disposition_status=ChannelAlertDispositionStatus.ACKNOWLEDGED,
        principal=principal,
        identity=identity,
        service=service,
        configuration=configuration,
    )


@router.post(
    "/operations/alerts/{channel_id}/suppress",
    response_model=ChannelAlertDispositionResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_ALERT_MANAGE))],
)
async def suppress_alert(
    channel_id: UUID,
    command: ChannelAlertDispositionCommand,
    principal: Annotated[
        AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_ALERT_MANAGE))
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
    configuration: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> ChannelAlertDispositionResponse:
    return await _set_alert_disposition(
        channel_id=channel_id,
        command=command,
        disposition_status=ChannelAlertDispositionStatus.SUPPRESSED,
        principal=principal,
        identity=identity,
        service=service,
        configuration=configuration,
    )


@router.post(
    "/operations/alerts/{channel_id}/unsuppress",
    response_model=ChannelAlertDispositionResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_ALERT_MANAGE))],
)
async def unsuppress_alert(
    channel_id: UUID,
    command: ChannelAlertDispositionClearCommand,
    principal: Annotated[
        AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_ALERT_MANAGE))
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelAlertDispositionResponse:
    if not command.confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="解除告警处置必须明确确认",
        )
    try:
        existing = await service.get_alert_disposition(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            alert_key=command.alert_key,
        )
        if existing is None or existing.channel_id != channel_id:
            raise ChannelNotFoundError("告警处置不存在")
        await service.clear_alert_disposition(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            alert_key=command.alert_key,
            actor_id=principal.user_id,
            confirmed=True,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="告警处置不存在"
        ) from error
    return ChannelAlertDispositionResponse(
        alert_key=command.alert_key,
        channel_id=channel_id,
        status="cleared",
        reason="已解除处置",
        expires_at=None,
        updated_at=datetime.now(UTC),
    )


@router.post(
    "/operations/alerts/notify",
    response_model=ChannelAlertNotificationResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_NOTIFICATION_MANAGE))],
)
async def notify_channel_alerts(
    command: ChannelAlertNotificationCommand,
    principal: Annotated[
        AdminPrincipal,
        Depends(require_permission(AdminPermission.CHANNEL_NOTIFICATION_MANAGE)),
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[AlertNotificationService, Depends(get_alert_notification_service)],
) -> ChannelAlertNotificationResponse:
    try:
        result = await service.notify(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            actor_id=principal.user_id,
            window_minutes=command.window_minutes,
            confirmed=command.confirmed,
            adapter_key=command.adapter,
        )
    except AlertNotificationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except AlertNotificationDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="告警通知投递失败，请稍后重试",
            headers={"X-CNB-Error-Code": error.code},
        ) from error
    return ChannelAlertNotificationResponse(
        delivered=result.delivered,
        alert_count=result.alert_count,
        attempts=result.attempts,
        idempotency_key=result.idempotency_key,
        elapsed_ms=result.elapsed_ms,
        status_code=result.status_code,
    )


@router.post(
    "/operations/alerts/notify/queue",
    response_model=ChannelAlertNotificationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_NOTIFICATION_MANAGE))],
)
async def queue_channel_alerts_notification(
    command: ChannelAlertNotificationCommand,
    principal: Annotated[
        AdminPrincipal,
        Depends(require_permission(AdminPermission.CHANNEL_NOTIFICATION_MANAGE)),
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[AlertNotificationService, Depends(get_alert_notification_service)],
) -> ChannelAlertNotificationJobResponse:
    """将告警摘要放入可靠后台队列，不在 API 请求内访问第三方。"""
    try:
        result = await service.enqueue(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            actor_id=principal.user_id,
            window_minutes=command.window_minutes,
            confirmed=command.confirmed,
            adapter_key=command.adapter,
        )
    except AlertNotificationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return ChannelAlertNotificationJobResponse(
        job_id=result.job.id,
        status=result.job.status,
        queue=result.job.queue,
        deduplication_key=result.job.deduplication_key,
        available_at=result.job.available_at,
        created_at=result.job.created_at,
    )


@router.post("", response_model=ChannelInstanceResponse, status_code=status.HTTP_201_CREATED)
async def create_instance(
    command: ChannelInstanceCreate,
    principal: Annotated[
        AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_WRITE))
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelInstanceResponse:
    try:
        item = await service.create(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            name=command.name,
            platform=command.platform,
            status=command.status,
            rate_limit_per_minute=command.rate_limit_per_minute,
            settings=command.settings,
            credential=(command.credential.get_secret_value() if command.credential else None),
            actor_id=principal.user_id,
        )
    except (ChannelValidationError, ChannelConflictError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _instance_response(item)


@router.get("/{channel_id}", response_model=ChannelInstanceResponse)
async def get_instance(
    channel_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelInstanceResponse:
    """读取当前租户的单个渠道实例和安全凭证状态。"""
    try:
        item = await service.get(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return _instance_response(item)


@router.patch("/{channel_id}", response_model=ChannelInstanceResponse)
async def update_instance(
    channel_id: UUID,
    command: ChannelInstanceUpdate,
    principal: Annotated[
        AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_WRITE))
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelInstanceResponse:
    try:
        item = await service.update(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            actor_id=principal.user_id,
            name=command.name,
            status=command.status,
            rate_limit_per_minute=command.rate_limit_per_minute,
            settings=command.settings,
            confirmed=command.confirmed,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (ChannelValidationError, ChannelConflictError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _instance_response(item)


@router.put("/{channel_id}/credential", response_model=ChannelInstanceResponse)
async def set_credential(
    channel_id: UUID,
    command: ChannelCredentialCommand,
    principal: Annotated[
        AdminPrincipal,
        Depends(require_permission(AdminPermission.CHANNEL_CREDENTIAL_MANAGE)),
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelInstanceResponse:
    try:
        item = await service.set_credential(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            plaintext=command.credential.get_secret_value(),
            actor_id=principal.user_id,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (ChannelValidationError, ChannelConflictError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _instance_response(item)


@router.post("/{channel_id}/credential/clear", response_model=ChannelInstanceResponse)
async def clear_credential(
    channel_id: UUID,
    command: ChannelConfirmedCommand,
    principal: Annotated[
        AdminPrincipal,
        Depends(require_permission(AdminPermission.CHANNEL_CREDENTIAL_MANAGE)),
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelInstanceResponse:
    try:
        item = await service.clear_credential(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (ChannelValidationError, ChannelConflictError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _instance_response(item)


@router.post("/{channel_id}/connection-test", response_model=ChannelInstanceResponse)
async def test_connection(
    channel_id: UUID,
    principal: Annotated[
        AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_WRITE))
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelInstanceResponse:
    try:
        item = await service.test_connection(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            actor_id=principal.user_id,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return _instance_response(item)


@router.get("/{channel_id}/telegram-webhook", response_model=TelegramWebhookStatusResponse)
async def get_telegram_webhook_status(
    channel_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> TelegramWebhookStatusResponse:
    try:
        result = await service.telegram_webhook_status(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelAdapterError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_429_TOO_MANY_REQUESTS
                if error.code in {"rate_limited", "telegram_rate_limited"}
                else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=error.safe_message,
            headers=(
                {"Retry-After": str(error.retry_after_seconds)}
                if error.retry_after_seconds is not None
                else None
            ),
        ) from error
    return _webhook_response(result)


@router.post(
    "/{channel_id}/telegram-webhook/register",
    response_model=TelegramWebhookStatusResponse,
)
async def register_telegram_webhook(
    channel_id: UUID,
    command: TelegramWebhookRegisterCommand,
    principal: Annotated[
        AdminPrincipal,
        Depends(require_permission(AdminPermission.CHANNEL_CREDENTIAL_MANAGE)),
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> TelegramWebhookStatusResponse:
    try:
        result = await service.register_telegram_webhook(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            webhook_url=command.webhook_url,
            drop_pending_updates=command.drop_pending_updates,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=error.safe_message
        ) from error
    except (ChannelValidationError, ChannelConflictError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ChannelAdapterError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_429_TOO_MANY_REQUESTS
                if error.code in {"rate_limited", "telegram_rate_limited"}
                else status.HTTP_422_UNPROCESSABLE_CONTENT
                if error.code == "unsupported_capability"
                else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=error.safe_message,
            headers=(
                {"Retry-After": str(error.retry_after_seconds)}
                if error.retry_after_seconds is not None
                else None
            ),
        ) from error
    return _webhook_response(result)


@router.post(
    "/{channel_id}/telegram-webhook/clear",
    response_model=TelegramWebhookStatusResponse,
)
async def clear_telegram_webhook(
    channel_id: UUID,
    command: TelegramWebhookClearCommand,
    principal: Annotated[
        AdminPrincipal,
        Depends(require_permission(AdminPermission.CHANNEL_CREDENTIAL_MANAGE)),
    ],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> TelegramWebhookStatusResponse:
    try:
        result = await service.clear_telegram_webhook(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            drop_pending_updates=command.drop_pending_updates,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=error.safe_message
        ) from error
    except (ChannelValidationError, ChannelConflictError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ChannelAdapterError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_429_TOO_MANY_REQUESTS
                if error.code in {"rate_limited", "telegram_rate_limited"}
                else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=error.safe_message,
            headers=(
                {"Retry-After": str(error.retry_after_seconds)}
                if error.retry_after_seconds is not None
                else None
            ),
        ) from error
    return _webhook_response(result)


@router.post(
    "/simulate",
    response_model=ChannelSimulationResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def simulate(
    command: ChannelSimulationCommand,
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelSimulationResponse:
    try:
        result = service.simulate(
            platform=command.platform,
            blocks=tuple(_block_input(item) for item in command.blocks),
            request_streaming=command.request_streaming,
            thread_id=command.thread_id,
            edit_message_id=command.edit_message_id,
            proactive=command.proactive,
        )
    except ChannelCapabilityError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ChannelSimulationResponse(
        platform=result.platform,
        blocks=tuple(_block_response(item) for item in result.blocks),
        degradations=result.degradations,
        buffered=result.buffered,
        thread_preserved=result.thread_preserved,
        edit_preserved=result.edit_preserved,
    )


@router.post("/{channel_id}/deliveries", response_model=ChannelDeliveryResponse)
async def deliver(
    channel_id: UUID,
    command: ChannelDeliveryRequest,
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_SEND))],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelDeliveryResponse:
    try:
        result = await service.deliver(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            recipient_id=command.recipient_id,
            blocks=tuple(_block_input(item) for item in command.blocks),
            idempotency_key=command.idempotency_key,
            request_streaming=command.request_streaming,
            thread_id=command.thread_id,
            edit_message_id=command.edit_message_id,
            proactive=command.proactive,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ChannelAdapterError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_429_TOO_MANY_REQUESTS
                if error.code in {"rate_limited", "telegram_rate_limited"}
                else status.HTTP_422_UNPROCESSABLE_CONTENT
                if error.code == "unsupported_capability"
                else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=error.safe_message,
            headers=(
                {"Retry-After": str(error.retry_after_seconds)}
                if error.retry_after_seconds is not None
                else None
            ),
        ) from error
    return ChannelDeliveryResponse(**asdict(result))


@router.post("/{channel_id}/simulate-inbound", response_model=ChannelInboundResponse)
async def simulate_inbound(
    channel_id: UUID,
    command: ChannelInboundSimulationCommand,
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_SEND))],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelInboundResponse:
    try:
        event = await service.normalize_inbound(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            payload=command.payload,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ChannelAdapterError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ChannelInboundResponse(
        external_event_id=event.external_event_id,
        event_type=event.event_type,
        sender_external_id=event.sender_external_id,
        conversation_external_id=event.conversation_external_id,
        blocks=tuple(_block_response(item) for item in event.blocks),
        occurred_at=event.occurred_at,
        thread_external_id=event.thread_external_id,
    )


@router.get(
    "/diagnostics/events",
    response_model=ChannelDiagnosticEventListResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def list_events(
    principal: Annotated[AdminPrincipal, Depends(require_permission(AdminPermission.CHANNEL_READ))],
    service: Annotated[ChannelService, Depends(get_channel_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    channel_id: Annotated[UUID | None, Query()] = None,
    event_type: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ChannelDiagnosticEventListResponse:
    try:
        events = await service.events(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            event_type=event_type,
            limit=limit,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ChannelDiagnosticEventListResponse(items=tuple(_event_response(item) for item in events))
