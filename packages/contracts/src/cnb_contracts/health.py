"""健康检查与管理总览契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

HealthStatus = Literal["healthy", "ready", "degraded", "not_checked", "not_configured"]


class ComponentHealth(BaseModel):
    """单个运行依赖或模块的健康摘要。"""

    name: str
    status: HealthStatus
    detail: str | None = None


class HealthResponse(BaseModel):
    """服务健康检查响应。"""

    service: str
    status: HealthStatus
    version: str
    checked_at: datetime
    components: tuple[ComponentHealth, ...] = ()


class SystemOverviewResponse(BaseModel):
    """首版总览使用的聚合数据，不包含业务明细。"""

    environment: str
    version: str
    active_agents: int = Field(ge=0)
    active_conversations: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)
    configuration_definitions: int = Field(ge=0)
    components: tuple[ComponentHealth, ...]
