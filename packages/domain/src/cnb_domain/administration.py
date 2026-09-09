"""管理平面的角色、权限与会话主体。"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


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


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    """一次管理请求中已经解析且不可变的操作者主体。"""

    tenant_id: UUID
    user_id: UUID
    display_name: str
    role: AdminRole
    permissions: frozenset[AdminPermission]
    authentication_mode: str
