"""统一 HTTP 错误响应与请求追踪 ID。"""

from collections.abc import Awaitable, Callable
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ExceptionHandler

from cnb_contracts import ApiError, ApiErrorDetail, ApiErrorResponse


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求生成或传递可关联日志的追踪 ID。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "").strip()
        request_id = incoming[:128] if incoming else str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else str(uuid4())


def _response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: tuple[ApiErrorDetail, ...] = (),
) -> JSONResponse:
    payload = ApiErrorResponse(
        error=ApiError(
            code=code,
            message=message,
            request_id=_request_id(request),
            details=details,
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def install_error_handlers(application: FastAPI) -> None:
    """为应用安装 HTTP 与参数校验错误处理器。"""

    async def handle_http_exception(request: Request, error: HTTPException) -> JSONResponse:
        message = error.detail
        codes = {
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "validation_error",
            503: "service_unavailable",
        }
        return _response(
            request,
            status_code=error.status_code,
            code=codes.get(error.status_code, "request_failed"),
            message=message,
        )

    async def handle_validation_exception(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        details = tuple(
            ApiErrorDetail(
                field=".".join(str(part) for part in item["loc"]),
                message=item["msg"],
                error_type=item["type"],
            )
            for item in error.errors()
        )
        return _response(
            request,
            status_code=422,
            code="validation_error",
            message="请求参数校验失败",
            details=details,
        )

    application.add_exception_handler(HTTPException, cast(ExceptionHandler, handle_http_exception))
    application.add_exception_handler(
        RequestValidationError, cast(ExceptionHandler, handle_validation_exception)
    )
