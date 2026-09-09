"""管理会话与角色能力矩阵 API 契约。"""

from uuid import UUID

from pydantic import BaseModel

from cnb_domain import AdminPermission, AdminRole


class AdminSessionResponse(BaseModel):
    """当前管理主体及其服务端授权结果。"""

    tenant_id: UUID
    user_id: UUID
    display_name: str
    role: AdminRole
    permissions: tuple[AdminPermission, ...]
    authentication_mode: str


class AdminRoleResponse(BaseModel):
    """一个内置角色的能力集合。"""

    role: AdminRole
    label: str
    description: str
    permissions: tuple[AdminPermission, ...]


class AdminRoleListResponse(BaseModel):
    """管理后台用于解释权限的完整角色矩阵。"""

    roles: tuple[AdminRoleResponse, ...]
