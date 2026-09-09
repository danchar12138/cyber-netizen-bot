"""Liveness and readiness routes."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request

from cnb_api import __version__
from cnb_contracts import ComponentHealth, HealthResponse
from cnb_infrastructure import Settings

router = APIRouter(tags=["health"])


def _settings_from(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    """Report whether the API process can serve requests."""
    return HealthResponse(
        service="cnb-api",
        status="healthy",
        version=__version__,
        checked_at=datetime.now(UTC),
    )


@router.get("/health/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    """Report bootstrap readiness without hiding disabled deep checks."""
    settings = _settings_from(request)
    detail = (
        "Deep dependency checks are scheduled for the infrastructure slice."
        if settings.readiness_deep_checks
        else "Deep checks are disabled by bootstrap configuration."
    )
    return HealthResponse(
        service="cnb-api",
        status="ready",
        version=__version__,
        checked_at=datetime.now(UTC),
        components=(
            ComponentHealth(name="external_dependencies", status="not_checked", detail=detail),
        ),
    )
