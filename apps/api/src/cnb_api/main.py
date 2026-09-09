"""FastAPI 应用组合根。"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cnb_api import __version__
from cnb_api.routes import configuration, health, system
from cnb_application import ConfigurationRepository
from cnb_infrastructure import (
    DependencyProbe,
    Settings,
    SqlAlchemyConfigurationRepository,
    get_settings,
    probe_dependencies,
)
from cnb_infrastructure.database import create_session_factory


def create_app(
    settings: Settings | None = None,
    *,
    configuration_repository: ConfigurationRepository | None = None,
    dependency_probe: DependencyProbe | None = None,
) -> FastAPI:
    """创建可用于生产或测试的独立应用实例。"""
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield

    application = FastAPI(
        title="Cyber Netizen Bot API",
        summary="赛博网友管理与消息网关",
        version=__version__,
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.configuration_repository = configuration_repository or (
        SqlAlchemyConfigurationRepository(create_session_factory(resolved_settings))
    )
    application.state.dependency_probe = dependency_probe or probe_dependencies
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )
    application.include_router(health.router)
    application.include_router(system.router, prefix="/api/v1")
    application.include_router(configuration.router, prefix="/api/v1")
    return application


app = create_app()
