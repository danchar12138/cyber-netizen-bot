"""不采集业务正文的 OpenTelemetry 与 API 性能指标边界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import unquote
from uuid import UUID

from fastapi import Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

from cnb_application import ApiRequestObservation, ObservabilityRepository
from cnb_domain import AdminPrincipal
from cnb_infrastructure import Settings


@dataclass(slots=True)
class TelemetryRuntime:
    """组合根持有的 Trace Provider 生命周期。"""

    tracer: Tracer
    provider: TracerProvider | None = None

    def shutdown(self) -> None:
        if self.provider is not None:
            self.provider.shutdown()


def configure_telemetry(settings: Settings) -> TelemetryRuntime:
    """按启动设置创建 OTLP Trace 导出器；禁用时保持官方 NoOp Tracer。"""
    if not settings.otel_enabled:
        return TelemetryRuntime(trace.get_tracer("cnb.api"))
    endpoint = settings.otel_exporter_otlp_endpoint
    if endpoint is None:
        raise ValueError("启用 OpenTelemetry 时缺少 OTLP endpoint")
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment.name": settings.environment,
            }
        ),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
    )
    exporter = OTLPSpanExporter(
        endpoint=endpoint,
        headers=_parse_otlp_headers(settings.otel_exporter_otlp_headers.get_secret_value()),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return TelemetryRuntime(provider.get_tracer("cnb.api"), provider)


class SafeObservabilityMiddleware(BaseHTTPMiddleware):
    """记录安全路由元数据，并以同一边界建立 HTTP Server Span。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        repository: ObservabilityRepository,
        tracer: Tracer,
    ) -> None:
        super().__init__(app)
        self._repository = repository
        self._tracer = tracer

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = perf_counter()
        status_code = 500
        with self._tracer.start_as_current_span(
            "HTTP request",
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            finally:
                duration_ms = max(0, round((perf_counter() - started) * 1000))
                route = _route_template(request)
                span.update_name(f"{request.method} {route}")
                span.set_attribute("http.request.method", request.method)
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", status_code)
                request_id = getattr(request.state, "request_id", None)
                if isinstance(request_id, str):
                    span.set_attribute("cnb.request.id", request_id[:128])
                principal = getattr(request.state, "admin_principal", None)
                tenant_id = principal.tenant_id if isinstance(principal, AdminPrincipal) else None
                if tenant_id is not None:
                    span.set_attribute("cnb.tenant.id", str(tenant_id))
                run_id = request.path_params.get("run_id")
                if _is_uuid(run_id):
                    span.set_attribute("cnb.agent_run.id", str(run_id))
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR, "server_error"))
                await self._record_safely(
                    ApiRequestObservation(
                        tenant_id=tenant_id,
                        method=request.method[:12],
                        route=route,
                        status_code=status_code,
                        duration_ms=duration_ms,
                        occurred_at=datetime.now(UTC),
                    )
                )

    async def _record_safely(self, observation: ApiRequestObservation) -> None:
        """指标存储故障不能反向破坏业务请求。"""
        try:
            await self._repository.record_api_request(observation)
        except Exception:
            return


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path[:255]
    return "unmatched"


def _is_uuid(value: object) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return value is not None


def _parse_otlp_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        name, separator, value = item.partition("=")
        if not separator or not name.strip() or "\r" in value or "\n" in value:
            raise ValueError("OTLP headers 必须使用逗号分隔的 name=value 格式")
        headers[name.strip()] = unquote(value.strip())
    return headers
