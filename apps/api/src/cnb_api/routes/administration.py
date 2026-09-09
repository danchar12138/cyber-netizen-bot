"""当前管理会话与内置 RBAC 能力矩阵。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from cnb_api.dependencies import get_admin_principal, require_permission
from cnb_application import permissions_for_role
from cnb_contracts import AdminRoleListResponse, AdminRoleResponse, AdminSessionResponse
from cnb_domain import AdminPermission, AdminPrincipal, AdminRole

router = APIRouter(prefix="/administration", tags=["administration"])

_ROLE_DESCRIPTIONS = {
    AdminRole.ADMIN: ("管理员", "拥有全部管理、配置与密钥权限。"),
    AdminRole.OPERATOR: ("运营者", "可运营对话并发布普通配置，但不能管理密钥。"),
    AdminRole.VIEWER: ("只读访客", "可查看状态、配置和对话，不能执行变更。"),
}


@router.get("/session", response_model=AdminSessionResponse)
async def get_admin_session(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
) -> AdminSessionResponse:
    """返回服务端实际使用的当前管理主体和权限。"""
    return AdminSessionResponse(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        display_name=principal.display_name,
        role=principal.role,
        permissions=tuple(sorted(principal.permissions, key=lambda item: item.value)),
        authentication_mode=principal.authentication_mode,
    )


@router.get("/roles", response_model=AdminRoleListResponse)
async def list_admin_roles(
    _: Annotated[
        AdminPrincipal,
        Depends(require_permission(AdminPermission.ACCESS_CONTROL_READ)),
    ],
) -> AdminRoleListResponse:
    """返回首期不可变角色矩阵，供管理后台解释最小权限。"""
    return AdminRoleListResponse(
        roles=tuple(
            AdminRoleResponse(
                role=role,
                label=_ROLE_DESCRIPTIONS[role][0],
                description=_ROLE_DESCRIPTIONS[role][1],
                permissions=tuple(sorted(permissions_for_role(role), key=lambda item: item.value)),
            )
            for role in AdminRole
        )
    )
