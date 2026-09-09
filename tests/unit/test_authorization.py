"""管理角色与最小权限策略测试。"""

from uuid import uuid4

import pytest

from cnb_application import PermissionDeniedError, permissions_for_role, require_admin_permission
from cnb_domain import AdminPermission, AdminPrincipal, AdminRole


def _principal(role: AdminRole) -> AdminPrincipal:
    return AdminPrincipal(
        tenant_id=uuid4(),
        user_id=uuid4(),
        display_name="权限测试用户",
        role=role,
        permissions=permissions_for_role(role),
        authentication_mode="test",
    )


def test_admin_has_every_registered_permission() -> None:
    assert permissions_for_role(AdminRole.ADMIN) == frozenset(AdminPermission)


def test_operator_can_publish_configuration_but_cannot_manage_secrets() -> None:
    principal = _principal(AdminRole.OPERATOR)

    assert require_admin_permission(principal, AdminPermission.CONFIGURATION_WRITE) is principal
    assert require_admin_permission(principal, AdminPermission.MEMORY_REBUILD) is principal
    with pytest.raises(PermissionDeniedError, match="secret:manage"):
        require_admin_permission(principal, AdminPermission.SECRET_MANAGE)


def test_viewer_is_strictly_read_only() -> None:
    permissions = permissions_for_role(AdminRole.VIEWER)

    assert AdminPermission.CONFIGURATION_READ in permissions
    assert AdminPermission.CONVERSATION_READ in permissions
    assert AdminPermission.MEMORY_READ in permissions
    assert AdminPermission.CONFIGURATION_WRITE not in permissions
    assert AdminPermission.CONVERSATION_USE not in permissions
    assert AdminPermission.MEMORY_WRITE not in permissions
    assert AdminPermission.MEMORY_REBUILD not in permissions
