"""FastAPI composition root."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cnb_api import __version__
from cnb_api.routes import configuration, health, system
from cnb_infrastructure import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an isolated application instance for production or tests."""
    resolved_settings = settings or get_settings()
    application = FastAPI(
        title="Cyber Netizen Bot API",
        summary="Administration and messaging gateway",
        version=__version__,
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
    )
    application.state.settings = resolved_settings
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
