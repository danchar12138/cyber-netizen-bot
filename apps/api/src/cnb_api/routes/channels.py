"""多模态能力、渠道实例、模拟器、凭证与诊断管理 API。"""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cnb_adapters import ChannelAdapterError, ChannelCapabilityError
from cnb_api.dependencies import get_channel_service, get_request_identity, require_permission
from cnb_application import (
    ChannelConflictError,
    ChannelInstanceView,
    ChannelNotFoundError,
    ChannelService,
    ChannelValidationError,
)
from cnb_contracts import (
    ChannelCapabilitiesResponse,
    ChannelCatalogListResponse,
    ChannelCatalogResponse,
    ChannelConfirmedCommand,
    ChannelCredentialCommand,
    ChannelDeliveryRequest,
    ChannelDeliveryResponse,
    ChannelDiagnosticEventListResponse,
    ChannelDiagnosticEventResponse,
    ChannelInboundResponse,
    ChannelInboundSimulationCommand,
    ChannelInstanceCreate,
    ChannelInstanceListResponse,
    ChannelInstanceResponse,
    ChannelInstanceUpdate,
    ChannelSimulationCommand,
    ChannelSimulationResponse,
    ModelCapabilityMatrixResponse,
    ModelCapabilityResponse,
    MultimodalContentBlockInput,
    MultimodalContentBlockResponse,
)
from cnb_domain import (
    AdminPermission,
    AdminPrincipal,
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


@router.get(
    "/catalog",
    response_model=ChannelCatalogListResponse,
    dependencies=[Depends(require_permission(AdminPermission.CHANNEL_READ))],
)
async def list_catalog(
    service: Annotated[ChannelService, Depends(get_channel_service)],
) -> ChannelCatalogListResponse:
    """列出正式 Web/Telegram Adapter 与其余 IM 占位能力。"""
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
    """返回当前内置模型 Provider 的稳定能力矩阵。"""
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
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ChannelDiagnosticEventListResponse:
    try:
        events = await service.events(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            limit=limit,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return ChannelDiagnosticEventListResponse(items=tuple(_event_response(item) for item in events))
