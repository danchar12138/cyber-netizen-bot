"""Health and administration overview contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

HealthStatus = Literal["healthy", "ready", "degraded", "not_checked", "not_configured"]


class ComponentHealth(BaseModel):
    """Health summary for one runtime dependency or module."""

    name: str
    status: HealthStatus
    detail: str | None = None


class HealthResponse(BaseModel):
    """Service health response."""

    service: str
    status: HealthStatus
    version: str
    checked_at: datetime
    components: tuple[ComponentHealth, ...] = ()


class SystemOverviewResponse(BaseModel):
    """Initial dashboard payload kept intentionally aggregate-only."""

    environment: str
    version: str
    active_agents: int = Field(ge=0)
    active_conversations: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)
    configuration_definitions: int = Field(ge=0)
    components: tuple[ComponentHealth, ...]
