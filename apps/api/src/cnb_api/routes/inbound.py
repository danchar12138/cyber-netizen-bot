"""外部身份、线程路由与可重放 Inbox 管理接口。"""

from collections.abc import Mapping
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from cnb_adapters import ChannelAdapterError
from cnb_api.dependencies import (
    get_admin_principal,
    get_channel_service,
    get_inbound_service,
    get_request_identity,
    get_task_service,
    require_permission,
)
from cnb_application import (
    BackgroundTaskService,
    ChannelConflictError,
    ChannelNotFoundError,
    ChannelService,
    InboundConflictError,
    InboundGatewayService,
    InboundNotFoundError,
    InboundValidationError,
)
from cnb_contracts import (
    ExternalConversationMappingCreate,
    ExternalConversationMappingListResponse,
    ExternalConversationMappingResponse,
    ExternalIdentityMappingCreate,
    ExternalIdentityMappingListResponse,
    ExternalIdentityMappingResponse,
    ExternalMappingStatusCommand,
    InboundAcceptanceResponse,
    InboundSimulationCommand,
    InboxEventListResponse,
    InboxEventResponse,
    InboxReplayCommand,
    InboxReplayResponse,
)
from cnb_domain import (
    AdminPermission,
    AdminPrincipal,
    AgentRunStatus,
    ChannelPlatform,
    DevelopmentIdentity,
    ExternalMappingStatus,
    InboundVerification,
    InboxEventStatus,
)

router = APIRouter(
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[
        Depends(require_permission(AdminPermission.INTEGRATION_READ)),
        Depends(get_request_identity),
    ],
)

_INBOUND_EXECUTION_STATUSES = {
    "agent_run_processed",
    "already_terminal",
    "claimed_elsewhere",
}


def _summary_uuid(summary: Mapping[str, object], key: str) -> UUID | None:
    value = summary.get(key)
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _summary_run_status(summary: Mapping[str, object]) -> AgentRunStatus | None:
    value = summary.get("run_status")
    if not isinstance(value, str):
        return None
    try:
        return AgentRunStatus(value)
    except ValueError:
        return None


def _summary_bool(summary: Mapping[str, object], key: str) -> bool | None:
    value = summary.get(key)
    return value if isinstance(value, bool) else None


@router.get(
    "/identity-mappings",
    response_model=ExternalIdentityMappingListResponse,
)
async def list_identity_mappings(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[InboundGatewayService, Depends(get_inbound_service)],
    channel_id: UUID | None = None,
    mapping_status: ExternalMappingStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ExternalIdentityMappingListResponse:
    items = await service.list_identities(
        tenant_id=principal.tenant_id,
        agent_id=request.state.request_identity.agent_id,
        channel_id=channel_id,
        status=mapping_status,
        limit=limit,
    )
    return ExternalIdentityMappingListResponse(
        items=tuple(ExternalIdentityMappingResponse(**asdict(item)) for item in items)
    )


@router.post(
    "/identity-mappings",
    response_model=ExternalIdentityMappingResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.INTEGRATION_MANAGE))],
)
async def create_identity_mapping(
    command: ExternalIdentityMappingCreate,
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[InboundGatewayService, Depends(get_inbound_service)],
) -> ExternalIdentityMappingResponse:
    try:
        item = await service.bind_identity(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=command.channel_id,
            external_subject_id=command.external_subject_id,
            user_id=command.user_id,
            actor_id=principal.user_id,
        )
    except InboundNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InboundConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except InboundValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return ExternalIdentityMappingResponse(**asdict(item))


@router.patch(
    "/identity-mappings/{mapping_id}/status",
    response_model=ExternalIdentityMappingResponse,
    dependencies=[Depends(require_permission(AdminPermission.INTEGRATION_MANAGE))],
)
async def update_identity_mapping_status(
    mapping_id: UUID,
    command: ExternalMappingStatusCommand,
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[InboundGatewayService, Depends(get_inbound_service)],
) -> ExternalIdentityMappingResponse:
    try:
        item = await service.set_identity_status(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            mapping_id=mapping_id,
            status=command.status,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except InboundNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InboundValidationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ExternalIdentityMappingResponse(**asdict(item))


@router.get(
    "/conversation-mappings",
    response_model=ExternalConversationMappingListResponse,
)
async def list_conversation_mappings(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[InboundGatewayService, Depends(get_inbound_service)],
    channel_id: UUID | None = None,
    mapping_status: ExternalMappingStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ExternalConversationMappingListResponse:
    items = await service.list_conversations(
        tenant_id=principal.tenant_id,
        agent_id=request.state.request_identity.agent_id,
        channel_id=channel_id,
        status=mapping_status,
        limit=limit,
    )
    return ExternalConversationMappingListResponse(
        items=tuple(ExternalConversationMappingResponse(**asdict(item)) for item in items)
    )


@router.post(
    "/conversation-mappings",
    response_model=ExternalConversationMappingResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.INTEGRATION_MANAGE))],
)
async def create_conversation_mapping(
    command: ExternalConversationMappingCreate,
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[InboundGatewayService, Depends(get_inbound_service)],
) -> ExternalConversationMappingResponse:
    try:
        item = await service.bind_conversation(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=command.channel_id,
            user_id=command.user_id,
            kind=command.kind,
            external_conversation_id=command.external_conversation_id,
            external_thread_id=command.external_thread_id,
            conversation_id=command.conversation_id,
            actor_id=principal.user_id,
        )
    except InboundNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InboundConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except InboundValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return ExternalConversationMappingResponse(**asdict(item))


@router.patch(
    "/conversation-mappings/{mapping_id}/status",
    response_model=ExternalConversationMappingResponse,
    dependencies=[Depends(require_permission(AdminPermission.INTEGRATION_MANAGE))],
)
async def update_conversation_mapping_status(
    mapping_id: UUID,
    command: ExternalMappingStatusCommand,
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[InboundGatewayService, Depends(get_inbound_service)],
) -> ExternalConversationMappingResponse:
    try:
        item = await service.set_conversation_status(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            mapping_id=mapping_id,
            status=command.status,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except InboundNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InboundValidationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ExternalConversationMappingResponse(**asdict(item))


@router.post(
    "/inbound/{channel_id}/simulate",
    response_model=InboundAcceptanceResponse,
    dependencies=[Depends(require_permission(AdminPermission.INTEGRATION_MANAGE))],
)
async def simulate_inbound_acceptance(
    channel_id: UUID,
    command: InboundSimulationCommand,
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    channels: Annotated[ChannelService, Depends(get_channel_service)],
    service: Annotated[InboundGatewayService, Depends(get_inbound_service)],
) -> InboundAcceptanceResponse:
    """从内部 Web Adapter 模拟已验签事件，不接收真实 IM Webhook。"""
    if not command.signature_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="平台签名验证失败",
        )
    try:
        event = await channels.normalize_inbound(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            payload=command.payload,
        )
        result = await service.accept_normalized(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            channel_id=channel_id,
            event=event,
            verification=InboundVerification(
                signature_valid=command.signature_valid,
                payload_size_bytes=command.payload_size_bytes,
                received_at=command.received_at,
            ),
            created_by=principal.user_id,
        )
    except ChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InboundNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ChannelConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except InboundConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ChannelAdapterError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error.safe_message,
        ) from error
    except InboundValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return InboundAcceptanceResponse(
        inbox_id=result.inbox.id,
        job_id=result.job.id,
        created=result.created,
        status=result.inbox.status,
        schema_version=result.inbox.schema_version or "",
    )


@router.get("/inbox", response_model=InboxEventListResponse)
async def list_inbox_events(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[InboundGatewayService, Depends(get_inbound_service)],
    tasks: Annotated[BackgroundTaskService, Depends(get_task_service)],
    inbox_status: InboxEventStatus | None = None,
    channel_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> InboxEventListResponse:
    items = await service.list_inbox(
        tenant_id=principal.tenant_id,
        agent_id=request.state.request_identity.agent_id,
        status=inbox_status,
        channel_id=channel_id,
        limit=limit,
    )
    responses: list[InboxEventResponse] = []
    for item in items:
        job = await tasks.get_job(tenant_id=principal.tenant_id, job_id=item.job_id)
        summary: Mapping[str, object] = job.result_summary
        execution_status = summary.get("execution_status")
        if (
            item.agent_id is None
            or item.channel_id is None
            or item.schema_version is None
            or item.platform is None
            or item.external_event_digest is None
            or item.external_subject_digest is None
            or item.external_conversation_digest is None
            or item.external_message_digest is None
            or item.user_id is None
            or item.conversation_id is None
        ):
            continue
        responses.append(
            InboxEventResponse(
                id=item.id,
                agent_id=item.agent_id,
                channel_id=item.channel_id,
                schema_version=item.schema_version,
                platform=ChannelPlatform(item.platform),
                event_type=item.event_type,
                status=item.status,
                job_id=item.job_id,
                job_status=job.status,
                external_event_digest=item.external_event_digest,
                external_subject_digest=item.external_subject_digest,
                external_conversation_digest=item.external_conversation_digest,
                external_thread_digest=item.external_thread_digest,
                external_message_digest=item.external_message_digest,
                user_id=item.user_id,
                conversation_id=item.conversation_id,
                content_kinds=item.content_kinds,
                content_block_count=item.content_block_count,
                received_at=item.received_at,
                processed_at=item.processed_at,
                last_error_code=item.last_error_code,
                message_id=_summary_uuid(summary, "message_id"),
                run_id=_summary_uuid(summary, "run_id"),
                run_status=_summary_run_status(summary),
                idempotent_replay=_summary_bool(summary, "idempotent_replay"),
                execution_status=(
                    execution_status
                    if isinstance(execution_status, str)
                    and execution_status in _INBOUND_EXECUTION_STATUSES
                    else None
                ),
            )
        )
    return InboxEventListResponse(items=tuple(responses))


@router.post(
    "/inbox/{inbox_id}/replay",
    response_model=InboxReplayResponse,
    dependencies=[Depends(require_permission(AdminPermission.INBOX_REPLAY))],
)
async def replay_inbox_event(
    inbox_id: UUID,
    command: InboxReplayCommand,
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[InboundGatewayService, Depends(get_inbound_service)],
) -> InboxReplayResponse:
    try:
        job = await service.replay_inbox(
            tenant_id=principal.tenant_id,
            agent_id=identity.agent_id,
            inbox_id=inbox_id,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
            reason=command.reason,
        )
    except InboundNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InboundConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if job.replayed_from_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="重放任务缺少来源")
    return InboxReplayResponse(
        job_id=job.id,
        status=job.status,
        replayed_from_id=job.replayed_from_id,
    )
