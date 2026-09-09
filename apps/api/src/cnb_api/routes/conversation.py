"""内部 Web 对话的持久化 HTTP 命令与 WebSocket 事件流。"""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from cnb_api.dependencies import get_conversation_service, require_permission
from cnb_application import (
    AgentRunNotFoundError,
    ConversationConflictError,
    ConversationNotFoundError,
    ConversationService,
    InvalidCursorError,
)
from cnb_contracts import (
    AgentRunResponse,
    ConversationCreate,
    ConversationEventEnvelope,
    ConversationListResponse,
    ConversationResponse,
    DevelopmentIdentityResponse,
    HeartbeatEnvelope,
    MessageAcceptedResponse,
    MessageCreate,
    MessageListResponse,
    MessageResponse,
)
from cnb_domain import (
    AdminPermission,
    AgentRun,
    Conversation,
    ConversationEvent,
    Message,
    PendingAgentRun,
)

router = APIRouter(
    prefix="/chat",
    tags=["internal-chat"],
    dependencies=[Depends(require_permission(AdminPermission.CONVERSATION_READ))],
)


def _conversation_response(item: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=item.id,
        tenant_id=item.tenant_id,
        agent_id=item.agent_id,
        title=item.title,
        status=item.status,
        event_sequence=item.event_sequence,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _message_response(item: Message) -> MessageResponse:
    return MessageResponse(
        id=item.id,
        conversation_id=item.conversation_id,
        sender_type=item.sender_type,
        sender_id=item.sender_id,
        content=item.content,
        status=item.status,
        client_message_id=item.client_message_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _run_response(item: AgentRun) -> AgentRunResponse:
    return AgentRunResponse(
        id=item.id,
        conversation_id=item.conversation_id,
        response_message_id=item.response_message_id,
        status=item.status,
        configuration_version=item.configuration_version,
        persona_version=item.persona_version,
        prompt_version=item.prompt_version,
        model_profile=item.model_profile,
        input_tokens=item.input_tokens,
        output_tokens=item.output_tokens,
        error_code=item.error_code,
        created_at=item.created_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
    )


def _event_response(item: ConversationEvent) -> ConversationEventEnvelope:
    return ConversationEventEnvelope(
        event_id=item.id,
        conversation_id=item.conversation_id,
        sequence=item.sequence,
        event_type=item.event_type,
        occurred_at=item.created_at,
        run_id=item.run_id,
        message_id=item.message_id,
        payload=item.payload,
    )


def _accepted_response(item: PendingAgentRun) -> MessageAcceptedResponse:
    return MessageAcceptedResponse(
        user_message=_message_response(item.trigger_message),
        response_message=_message_response(item.response_message),
        run=_run_response(item.run),
        idempotent_replay=not item.created,
    )


@router.get("/identity", response_model=DevelopmentIdentityResponse)
async def get_development_identity(
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> DevelopmentIdentityResponse:
    """返回当前开发模式身份，并确保其已在仓储中建立。"""
    identity = await service.ensure_development_identity()
    return DevelopmentIdentityResponse(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        agent_id=identity.agent_id,
        user_name=identity.user_name,
        agent_name=identity.agent_name,
    )


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    service: Annotated[ConversationService, Depends(get_conversation_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: str | None = None,
) -> ConversationListResponse:
    """按最近更新顺序分页返回当前用户的会话。"""
    try:
        page = await service.list_conversations(limit=limit, cursor=cursor)
    except InvalidCursorError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ConversationListResponse(
        items=tuple(_conversation_response(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.CONVERSATION_USE))],
)
async def create_conversation(
    command: ConversationCreate,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ConversationResponse:
    """创建一个绑定当前开发用户与 Agent 的会话。"""
    return _conversation_response(await service.create_conversation(title=command.title))


@router.get("/conversations/{conversation_id}/messages", response_model=MessageListResponse)
async def list_messages(
    conversation_id: UUID,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: str | None = None,
) -> MessageListResponse:
    """以时间正序返回一页消息，下一页游标指向更早历史。"""
    try:
        page = await service.list_messages(conversation_id, limit=limit, cursor=cursor)
    except ConversationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InvalidCursorError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return MessageListResponse(
        items=tuple(_message_response(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission(AdminPermission.CONVERSATION_USE))],
)
async def send_message(
    conversation_id: UUID,
    command: MessageCreate,
    request: Request,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> MessageAcceptedResponse:
    """原子接收幂等消息并异步启动 Agent Run。"""
    try:
        pending = await service.send_message(
            conversation_id,
            client_message_id=command.client_message_id,
            content=command.content,
        )
    except ConversationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ConversationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    if pending.created:
        task = asyncio.create_task(service.execute_run(pending), name=f"agent-run-{pending.run.id}")
        run_tasks: dict[UUID, asyncio.Task[None]] = request.app.state.agent_run_tasks
        run_tasks[pending.run.id] = task
        task.add_done_callback(lambda _: run_tasks.pop(pending.run.id, None))
    return _accepted_response(pending)


@router.post(
    "/runs/{run_id}/cancel",
    response_model=AgentRunResponse,
    dependencies=[Depends(require_permission(AdminPermission.CONVERSATION_USE))],
)
async def cancel_run(
    run_id: UUID,
    request: Request,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> AgentRunResponse:
    """幂等取消排队中或运行中的 Agent Run。"""
    run_tasks: dict[UUID, asyncio.Task[None]] = request.app.state.agent_run_tasks
    task = run_tasks.get(run_id)
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    try:
        return _run_response(await service.cancel_run(run_id))
    except AgentRunNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ConversationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.websocket("/conversations/{conversation_id}/events")
async def conversation_events(
    websocket: WebSocket,
    conversation_id: UUID,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
    after: Annotated[int, Query(ge=0)] = 0,
) -> None:
    """从指定序号重放事件，并持续推送新事件和心跳。"""
    await websocket.accept()
    sequence = after
    last_heartbeat = monotonic()
    try:
        await service.get_conversation(conversation_id)
        while True:
            events = await service.list_events(conversation_id, after_sequence=sequence)
            for event in events:
                await websocket.send_json(_event_response(event).model_dump(mode="json"))
                sequence = event.sequence
            if monotonic() - last_heartbeat >= 15:
                await websocket.send_json(
                    HeartbeatEnvelope(
                        occurred_at=datetime.now(UTC), last_sequence=sequence
                    ).model_dump(mode="json")
                )
                last_heartbeat = monotonic()
            try:
                frame = await asyncio.wait_for(websocket.receive_text(), timeout=0.2)
                if frame == "ping":
                    await websocket.send_json(
                        HeartbeatEnvelope(
                            occurred_at=datetime.now(UTC), last_sequence=sequence
                        ).model_dump(mode="json")
                    )
            except TimeoutError:
                continue
    except ConversationNotFoundError:
        await websocket.close(code=4404, reason="会话不存在")
    except WebSocketDisconnect:
        return
