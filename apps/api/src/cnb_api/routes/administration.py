"""当前管理会话与内置 RBAC 能力矩阵。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cnb_api.dependencies import (
    get_admin_principal,
    get_administration_service,
    require_permission,
)
from cnb_application import (
    AdministrationNotFoundError,
    AdministrationService,
    AdministrationValidationError,
    InvalidCursorError,
    permissions_for_role,
)
from cnb_contracts import (
    AdminRoleListResponse,
    AdminRoleResponse,
    AdminSessionResponse,
    AuditRecordListResponse,
    AuditRecordResponse,
    BulkStatusUpdateCommand,
    ManagedAgentListResponse,
    ManagedAgentResponse,
    ManagedUserListResponse,
    ManagedUserResponse,
)
from cnb_domain import AdminPermission, AdminPrincipal, AdminRole, EntityStatus

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


@router.get(
    "/agents",
    response_model=ManagedAgentListResponse,
    dependencies=[Depends(require_permission(AdminPermission.AGENT_READ))],
)
async def list_agents(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
    search: Annotated[str | None, Query(max_length=200)] = None,
    entity_status: EntityStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ManagedAgentListResponse:
    """按租户、状态与搜索词返回 Agent 键集分页列表。"""
    try:
        page = await service.list_agents(
            tenant_id=principal.tenant_id,
            search=search,
            status=entity_status,
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ManagedAgentListResponse(
        items=tuple(
            ManagedAgentResponse.model_validate(item, from_attributes=True) for item in page.items
        ),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/agents/status",
    response_model=ManagedAgentListResponse,
    dependencies=[Depends(require_permission(AdminPermission.AGENT_WRITE))],
)
async def update_agent_status(
    command: BulkStatusUpdateCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedAgentListResponse:
    """经明确确认后批量启停当前租户的 Agent。"""
    try:
        items = await service.update_agent_status(
            tenant_id=principal.tenant_id,
            agent_ids=command.ids,
            status=command.status,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return ManagedAgentListResponse(
        items=tuple(
            ManagedAgentResponse.model_validate(item, from_attributes=True) for item in items
        ),
        next_cursor=None,
    )


@router.get(
    "/users",
    response_model=ManagedUserListResponse,
    dependencies=[Depends(require_permission(AdminPermission.USER_READ))],
)
async def list_users(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
    search: Annotated[str | None, Query(max_length=200)] = None,
    entity_status: EntityStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ManagedUserListResponse:
    """按租户、状态与搜索词返回用户键集分页列表。"""
    try:
        page = await service.list_users(
            tenant_id=principal.tenant_id,
            search=search,
            status=entity_status,
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return ManagedUserListResponse(
        items=tuple(
            ManagedUserResponse.model_validate(item, from_attributes=True) for item in page.items
        ),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/users/status",
    response_model=ManagedUserListResponse,
    dependencies=[Depends(require_permission(AdminPermission.USER_WRITE))],
)
async def update_user_status(
    command: BulkStatusUpdateCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedUserListResponse:
    """经明确确认后批量启停当前租户的用户。"""
    try:
        items = await service.update_user_status(
            tenant_id=principal.tenant_id,
            user_ids=command.ids,
            status=command.status,
            actor_id=principal.user_id,
            confirmed=command.confirmed,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return ManagedUserListResponse(
        items=tuple(
            ManagedUserResponse.model_validate(item, from_attributes=True) for item in items
        ),
        next_cursor=None,
    )


@router.get(
    "/audit",
    response_model=AuditRecordListResponse,
    dependencies=[Depends(require_permission(AdminPermission.AUDIT_READ))],
)
async def list_audit_records(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
    search: Annotated[str | None, Query(max_length=200)] = None,
    action: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> AuditRecordListResponse:
    """查询当前租户和系统级只追加审计记录。"""
    try:
        page = await service.list_audit_records(
            tenant_id=principal.tenant_id,
            search=search,
            action=action,
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return AuditRecordListResponse(
        items=tuple(
            AuditRecordResponse.model_validate(item, from_attributes=True) for item in page.items
        ),
        next_cursor=page.next_cursor,
    )
