"""内部 Web 对话附件的预签名上传、校验、预览与清理接口。"""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from cnb_api.dependencies import get_attachment_service, require_permission
from cnb_application import (
    AttachmentConflictError,
    AttachmentNotFoundError,
    AttachmentService,
    AttachmentValidationError,
)
from cnb_contracts import (
    AttachmentListResponse,
    AttachmentPreviewResponse,
    AttachmentReservationResponse,
    AttachmentReserve,
    AttachmentResponse,
    AttachmentUploadGrant,
)
from cnb_domain import AdminPermission, Attachment

router = APIRouter(
    prefix="/chat",
    tags=["internal-chat-attachments"],
    dependencies=[Depends(require_permission(AdminPermission.CONVERSATION_READ))],
)


def attachment_response(item: Attachment) -> AttachmentResponse:
    """把领域实体映射为不暴露 object key 的安全契约。"""
    return AttachmentResponse(
        id=item.id,
        conversation_id=item.conversation_id,
        client_message_id=item.client_message_id,
        message_id=item.message_id,
        original_name=item.original_name,
        content_type=item.content_type,
        size_bytes=item.size_bytes,
        sha256=item.sha256,
        status=item.status,
        validation_error=item.validation_error,
        created_at=item.created_at,
        expires_at=item.expires_at,
        uploaded_at=item.uploaded_at,
        attached_at=item.attached_at,
    )


def _raise_attachment_error(error: Exception) -> NoReturn:
    if isinstance(error, AttachmentNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post(
    "/attachments/reservations",
    response_model=AttachmentReservationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.CONVERSATION_USE))],
)
async def reserve_attachment(
    command: AttachmentReserve,
    service: Annotated[AttachmentService, Depends(get_attachment_service)],
) -> AttachmentReservationResponse:
    """校验声明并为单个对象签发短期直传授权。"""
    try:
        reservation = await service.reserve(
            conversation_id=command.conversation_id,
            client_message_id=command.client_message_id,
            original_name=command.original_name,
            content_type=command.content_type,
            size_bytes=command.size_bytes,
            sha256=command.sha256,
        )
    except (AttachmentNotFoundError, AttachmentValidationError) as error:
        _raise_attachment_error(error)
    return AttachmentReservationResponse(
        attachment=attachment_response(reservation.attachment),
        upload=AttachmentUploadGrant(
            url=reservation.upload.url,
            method="PUT",
            headers=reservation.upload.headers,
            expires_at=reservation.upload.expires_at,
        ),
    )


@router.post(
    "/attachments/{attachment_id}/complete",
    response_model=AttachmentResponse,
    dependencies=[Depends(require_permission(AdminPermission.CONVERSATION_USE))],
)
async def complete_attachment(
    attachment_id: UUID,
    service: Annotated[AttachmentService, Depends(get_attachment_service)],
) -> AttachmentResponse:
    """依据对象存储可信元数据完成大小、类型和摘要复核。"""
    try:
        return attachment_response(await service.complete(attachment_id))
    except (AttachmentNotFoundError, AttachmentValidationError) as error:
        _raise_attachment_error(error)


@router.get(
    "/conversations/{conversation_id}/attachments",
    response_model=AttachmentListResponse,
)
async def list_attachments(
    conversation_id: UUID,
    service: Annotated[AttachmentService, Depends(get_attachment_service)],
) -> AttachmentListResponse:
    """列出当前用户在目标会话内可见的附件。"""
    try:
        items = await service.list_for_conversation(conversation_id)
    except AttachmentNotFoundError as error:
        _raise_attachment_error(error)
    return AttachmentListResponse(items=tuple(attachment_response(item) for item in items))


@router.get(
    "/attachments/{attachment_id}/preview",
    response_model=AttachmentPreviewResponse,
)
async def preview_attachment(
    attachment_id: UUID,
    service: Annotated[AttachmentService, Depends(get_attachment_service)],
) -> AttachmentPreviewResponse:
    """签发五分钟有效的私有对象预览地址。"""
    try:
        attachment, url = await service.preview(attachment_id)
    except (AttachmentNotFoundError, AttachmentValidationError) as error:
        _raise_attachment_error(error)
    return AttachmentPreviewResponse(attachment=attachment_response(attachment), url=url)


@router.delete(
    "/attachments/{attachment_id}",
    response_model=AttachmentResponse,
    dependencies=[Depends(require_permission(AdminPermission.CONVERSATION_USE))],
)
async def delete_attachment(
    attachment_id: UUID,
    service: Annotated[AttachmentService, Depends(get_attachment_service)],
) -> AttachmentResponse:
    """删除尚未随消息发送的对象并保留软删除元数据。"""
    try:
        return attachment_response(await service.delete(attachment_id))
    except (
        AttachmentConflictError,
        AttachmentNotFoundError,
        AttachmentValidationError,
    ) as error:
        _raise_attachment_error(error)
