"""与 Web 框架和身份厂商无关的管理认证端口与权限策略。"""

from typing import Protocol

from cnb_domain import AdminPermission, AdminPrincipal, AdminRole


class PermissionDeniedError(PermissionError):
    """当前管理主体不具备所需能力时抛出。"""


class AuthenticationError(PermissionError):
    """访问令牌缺失、无效或主体不可用时使用的安全认证错误。"""


class AdminAuthenticator(Protocol):
    """把厂商访问令牌验证为本地稳定管理主体。"""

    async def authenticate(self, access_token: str) -> AdminPrincipal: ...


_ROLE_PERMISSIONS: dict[AdminRole, frozenset[AdminPermission]] = {
    AdminRole.ADMIN: frozenset(AdminPermission),
    AdminRole.OPERATOR: frozenset(
        {
            AdminPermission.DASHBOARD_READ,
            AdminPermission.CONFIGURATION_READ,
            AdminPermission.CONFIGURATION_WRITE,
            AdminPermission.CONVERSATION_READ,
            AdminPermission.CONVERSATION_USE,
            AdminPermission.ACCESS_CONTROL_READ,
            AdminPermission.AGENT_READ,
            AdminPermission.AGENT_WRITE,
            AdminPermission.COGNITION_READ,
            AdminPermission.COGNITION_WRITE,
            AdminPermission.COGNITION_EVALUATE,
            AdminPermission.EVALUATION_REVIEW,
            AdminPermission.MEMORY_READ,
            AdminPermission.MEMORY_WRITE,
            AdminPermission.MEMORY_REBUILD,
            AdminPermission.TASK_READ,
            AdminPermission.TASK_MANAGE,
            AdminPermission.PROACTIVE_MANAGE,
            AdminPermission.CHANNEL_READ,
            AdminPermission.CHANNEL_WRITE,
            AdminPermission.CHANNEL_SEND,
            AdminPermission.CHANNEL_ALERT_MANAGE,
            AdminPermission.CHANNEL_NOTIFICATION_MANAGE,
            AdminPermission.INTEGRATION_READ,
            AdminPermission.INTEGRATION_MANAGE,
            AdminPermission.INBOX_REPLAY,
            AdminPermission.TRACE_READ,
            AdminPermission.USER_READ,
            AdminPermission.USER_WRITE,
            AdminPermission.AUDIT_READ,
            AdminPermission.DATA_LIFECYCLE_READ,
            AdminPermission.DATA_EXPORT,
        }
    ),
    AdminRole.VIEWER: frozenset(
        {
            AdminPermission.DASHBOARD_READ,
            AdminPermission.CONFIGURATION_READ,
            AdminPermission.CONVERSATION_READ,
            AdminPermission.ACCESS_CONTROL_READ,
            AdminPermission.AGENT_READ,
            AdminPermission.COGNITION_READ,
            AdminPermission.EVALUATION_REVIEW,
            AdminPermission.MEMORY_READ,
            AdminPermission.TASK_READ,
            AdminPermission.CHANNEL_READ,
            AdminPermission.INTEGRATION_READ,
            AdminPermission.TRACE_READ,
            AdminPermission.USER_READ,
            AdminPermission.AUDIT_READ,
            AdminPermission.DATA_LIFECYCLE_READ,
        }
    ),
}


def permissions_for_role(role: AdminRole) -> frozenset[AdminPermission]:
    """返回内置角色的稳定不可变权限集合。"""
    return _ROLE_PERMISSIONS[role]


def require_admin_permission(
    principal: AdminPrincipal, permission: AdminPermission
) -> AdminPrincipal:
    """验证主体权限并保留主体供后续审计使用。"""
    if permission not in principal.permissions:
        raise PermissionDeniedError(f"当前角色无权执行此操作：{permission.value}")
    return principal
