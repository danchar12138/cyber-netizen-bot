"""存活与就绪检查接口。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status

from cnb_api import __version__
from cnb_contracts import ComponentHealth, HealthResponse
from cnb_infrastructure import DependencyProbe, Settings

router = APIRouter(tags=["health"])


def _settings_from(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    """报告 API 进程是否能够处理请求。"""
    return HealthResponse(
        service="cnb-api",
        status="healthy",
        version=__version__,
        checked_at=datetime.now(UTC),
    )


@router.get("/health/ready", response_model=HealthResponse)
async def ready(request: Request, response: Response) -> HealthResponse:
    """执行有超时边界的依赖探测，或明确说明深度检查未启用。"""
    settings = _settings_from(request)
    if not settings.readiness_deep_checks:
        return HealthResponse(
            service="cnb-api",
            status="ready",
            version=__version__,
            checked_at=datetime.now(UTC),
            components=(
                ComponentHealth(
                    name="external_dependencies",
                    status="not_checked",
                    detail="启动配置未启用深度依赖检查。",
                ),
            ),
        )

    dependency_probe: DependencyProbe = request.app.state.dependency_probe
    components = await dependency_probe(settings)
    ready_status = "ready" if all(item.status == "healthy" for item in components) else "degraded"
    if ready_status == "degraded":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        service="cnb-api",
        status=ready_status,
        version=__version__,
        checked_at=datetime.now(UTC),
        components=components,
    )
