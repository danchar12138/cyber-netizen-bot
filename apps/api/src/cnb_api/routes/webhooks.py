"""无需管理身份的外部平台 Webhook 边界。"""

import json
import math
from datetime import UTC, datetime
from typing import Annotated, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from cnb_adapters import ChannelAdapterError
from cnb_api.dependencies import get_channel_service, get_inbound_service
from cnb_application import (
    ChannelConflictError,
    ChannelNotFoundError,
    ChannelService,
    InboundConflictError,
    InboundGatewayService,
    InboundNotFoundError,
    InboundValidationError,
)
from cnb_domain import InboundVerification, JsonValue

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
_MAX_PAYLOAD_BYTES = 10 * 1024 * 1024
_MAX_JSON_DEPTH = 20
_MAX_CONTAINER_ITEMS = 1_000
_MAX_STRING_CHARS = 65_536
_MAX_KEY_CHARS = 255


@router.post(
    "/telegram/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def receive_telegram_update(
    channel_id: UUID,
    request: Request,
    channels: Annotated[ChannelService, Depends(get_channel_service)],
    inbound: Annotated[InboundGatewayService, Depends(get_inbound_service)],
) -> Response:
    """安全接收 Telegram 文本 Update，并交由已有 Inbox 流程异步处理。"""
    try:
        context = await channels.verify_telegram_webhook(
            channel_id=channel_id,
            presented_secret=request.headers.get(_TELEGRAM_SECRET_HEADER),
        )
    except ChannelNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook 不可用",
        ) from error

    _require_json_content_type(request)
    payload_bytes = await _read_payload(request)
    payload = _parse_json_object(payload_bytes)
    try:
        event = await channels.normalize_inbound(
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            channel_id=context.channel_id,
            payload=payload,
        )
        await inbound.accept_normalized(
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            channel_id=context.channel_id,
            event=event,
            verification=InboundVerification(
                signature_valid=True,
                payload_size_bytes=len(payload_bytes),
                received_at=datetime.now(UTC),
            ),
            created_by=None,
        )
    except (
        ChannelNotFoundError,
        ChannelConflictError,
        InboundNotFoundError,
        InboundConflictError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook 入站路由不可用",
        ) from error
    except (ChannelAdapterError, InboundValidationError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Webhook 入站事件未通过验证",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _require_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", maxsplit=1)[0].strip().casefold()
    if media_type != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Webhook 仅接受 application/json 请求体",
        )


async def _read_payload(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook Content-Length 无效",
            ) from error
        if declared_length < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook Content-Length 无效",
            )
        if declared_length > _MAX_PAYLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Webhook 请求体超过允许大小",
            )

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_PAYLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Webhook 请求体超过允许大小",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_json_object(payload_bytes: bytes) -> dict[str, JsonValue]:
    try:
        decoded = payload_bytes.decode("utf-8")
        value = cast(object, json.loads(decoded, parse_constant=_reject_json_constant))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook JSON 格式无效",
        ) from error
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook JSON 根节点必须是对象",
        )
    payload = cast(dict[str, object], value)
    _validate_json_structure(payload)
    return cast(dict[str, JsonValue], payload)


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"JSON 不允许非常量数值: {value}")


def _validate_json_structure(value: object) -> None:
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAX_JSON_DEPTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook JSON 嵌套层级超过限制",
            )
        if isinstance(current, dict):
            values = cast(dict[object, object], current)
            if len(values) > _MAX_CONTAINER_ITEMS:
                _raise_json_size_error()
            for key, item in values.items():
                if not isinstance(key, str) or len(key) > _MAX_KEY_CHARS:
                    _raise_json_size_error()
                pending.append((item, depth + 1))
        elif isinstance(current, list):
            values = cast(list[object], current)
            if len(values) > _MAX_CONTAINER_ITEMS:
                _raise_json_size_error()
            pending.extend((item, depth + 1) for item in values)
        elif isinstance(current, str):
            if len(current) > _MAX_STRING_CHARS:
                _raise_json_size_error()
        elif isinstance(current, float) and not math.isfinite(current):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook JSON 包含无效数值",
            )
        elif current is not None and type(current) not in {bool, int, float}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook JSON 包含不支持的数据类型",
            )


def _raise_json_size_error() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Webhook JSON 字段大小超过限制",
    )
