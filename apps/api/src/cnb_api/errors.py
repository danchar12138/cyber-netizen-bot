"""统一 HTTP 错误响应与请求追踪 ID。"""

from collections.abc import Awaitable, Callable, Mapping
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为管理 API 添加浏览器侧纵深防御头，并禁止敏感响应缓存。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    payload = ApiErrorResponse(
        error=ApiError(
            code=code,
            message=message,
            request_id=_request_id(request),
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


def install_error_handlers(application: FastAPI) -> None:
    """为应用安装 HTTP 与参数校验错误处理器。"""

    async def handle_http_exception(request: Request, error: HTTPException) -> JSONResponse:
        message = error.detail
        codes = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "validation_error",
            429: "rate_limited",
            503: "service_unavailable",
        }
        return _response(
            request,
            status_code=error.status_code,
            code=codes.get(error.status_code, "request_failed"),
            message=message,
            headers=error.headers,
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
