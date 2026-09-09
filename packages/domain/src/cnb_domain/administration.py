"""管理平面的角色、权限与会话主体。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.configuration import JsonValue
from cnb_domain.conversation import EntityStatus


class AdminRole(StrEnum):
    """首期内置管理角色；正式身份源接入后仍保持稳定语义。"""

    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class AdminPermission(StrEnum):
    """API 按能力而非页面名称执行的细粒度权限。"""

    DASHBOARD_READ = "dashboard:read"
    CONFIGURATION_READ = "configuration:read"
    CONFIGURATION_WRITE = "configuration:write"
    SECRET_MANAGE = "secret:manage"
    CONVERSATION_READ = "conversation:read"
    CONVERSATION_USE = "conversation:use"
    ACCESS_CONTROL_READ = "access_control:read"
    AGENT_READ = "agent:read"
    AGENT_WRITE = "agent:write"
    COGNITION_READ = "cognition:read"
    COGNITION_WRITE = "cognition:write"
    COGNITION_EVALUATE = "cognition:evaluate"
    TRACE_READ = "trace:read"
    USER_READ = "user:read"
    USER_WRITE = "user:write"
    AUDIT_READ = "audit:read"


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    """一次管理请求中已经解析且不可变的操作者主体。"""

    tenant_id: UUID
    user_id: UUID
    display_name: str
    role: AdminRole
    permissions: frozenset[AdminPermission]
    authentication_mode: str


@dataclass(frozen=True, slots=True)
class ManagedAgent:
    """管理后台可查看和启停的 Agent 摘要。"""

    id: UUID
    tenant_id: UUID
    name: str
    status: EntityStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ManagedUser:
    """管理后台可查看和启停的用户摘要。"""

    id: UUID
    tenant_id: UUID
    display_name: str
    status: EntityStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """不包含密钥明文的只追加管理审计记录。"""

    id: int
    actor_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    detail: dict[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ManagementOverview:
    """管理总览可安全聚合的当前租户实时计数。"""

    active_agents: int
    active_conversations: int
    pending_jobs: int
