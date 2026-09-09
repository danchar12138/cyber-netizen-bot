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


class BootstrapSettingsResponse(BaseModel):
    """仅返回非敏感启动设置和凭证配置状态。"""

    environment: str
    log_level: str
    cors_origins: tuple[str, ...]
    readiness_deep_checks: bool
    authentication_mode: Literal["development", "oidc"]
    oidc_configured: bool
    otel_enabled: bool
    otel_exporter_configured: bool
    otel_service_name: str
    otel_trace_sample_ratio: float = Field(ge=0, le=1)
    object_storage_provider: Literal["minio"] = "minio"
    minio_endpoint_url: str
    minio_bucket: str
    database_configured: bool
    redis_configured: bool
    minio_credentials_configured: bool
    config_master_key_status: Literal["development_placeholder", "configured"]
    requires_restart: bool = True


class TaskStatusResponse(BaseModel):
    """异步任务真相计数与 Worker 心跳摘要。"""

    broker: Literal["dramatiq-redis"] = "dramatiq-redis"
    queues: tuple[str, ...] = ("system", "memory", "reflection", "proactive")
    pending_jobs: int = Field(ge=0)
    running_jobs: int = Field(default=0, ge=0)
    retrying_jobs: int = Field(default=0, ge=0)
    dead_letter_jobs: int = Field(default=0, ge=0)
    scheduled_actions: int = Field(default=0, ge=0)
    worker: ComponentHealth
