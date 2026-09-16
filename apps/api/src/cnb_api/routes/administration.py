"""当前管理会话与内置 RBAC 能力矩阵。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cnb_api.dependencies import (
    get_admin_principal,
    get_administration_service,
    require_permission,
)
from cnb_application import (
    AdministrationConflictError,
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
    AgentImpactCountsResponse,
    AgentLifecycleCommand,
    AgentLifecycleImpactResponse,
    AuditRecordListResponse,
    AuditRecordResponse,
    BulkStatusUpdateCommand,
    ManagedAdminSessionResponse,
    ManagedAgentCopyCommand,
    ManagedAgentCreateCommand,
    ManagedAgentListResponse,
    ManagedAgentRenameCommand,
    ManagedAgentResponse,
    ManagedConversationMembershipResponse,
    ManagedExternalIdentityResponse,
    ManagedRoleAssignmentResponse,
    ManagedTenantResponse,
    ManagedUserAccessPolicyResponse,
    ManagedUserDetailResponse,
    ManagedUserListResponse,
    ManagedUserResponse,
    UserAccessPolicyUpdateCommand,
    UserRoleOverrideCommand,
    UserRoleOverrideRevokeCommand,
    UserSessionRevokeCommand,
)
from cnb_domain import (
    AdminPermission,
    AdminPrincipal,
    AdminRole,
    AgentLifecycleImpact,
    AgentLifecycleStatus,
    EntityStatus,
    ManagedAdminSession,
    ManagedUserDetail,
)

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
    entity_status: AgentLifecycleStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ManagedAgentListResponse:
    """按租户、状态与搜索词返回智能体键集分页列表。"""
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
    "/agents",
    response_model=ManagedAgentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.AGENT_WRITE))],
)
async def create_agent(
    command: ManagedAgentCreateCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedAgentResponse:
    """创建一个启用状态且可独立配置认知资源的智能体。"""
    try:
        item = await service.create_agent(
            tenant_id=principal.tenant_id,
            name=command.name,
            actor_id=principal.user_id,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ManagedAgentResponse.model_validate(item, from_attributes=True)


@router.patch(
    "/agents/{agent_id}",
    response_model=ManagedAgentResponse,
    dependencies=[Depends(require_permission(AdminPermission.AGENT_WRITE))],
)
async def rename_agent(
    agent_id: UUID,
    command: ManagedAgentRenameCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedAgentResponse:
    """修改当前租户内未删除智能体的显示名称。"""
    try:
        item = await service.rename_agent(
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            name=command.name,
            actor_id=principal.user_id,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AdministrationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ManagedAgentResponse.model_validate(item, from_attributes=True)


@router.post(
    "/agents/{agent_id}/copy",
    response_model=ManagedAgentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.AGENT_WRITE))],
)
async def copy_agent(
    agent_id: UUID,
    command: ManagedAgentCopyCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedAgentResponse:
    """复制智能体及其已发布认知资源，新版本从 1 独立演进。"""
    try:
        item = await service.copy_agent(
            tenant_id=principal.tenant_id,
            source_agent_id=agent_id,
            name=command.name,
            actor_id=principal.user_id,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AdministrationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ManagedAgentResponse.model_validate(item, from_attributes=True)


@router.get(
    "/agents/{agent_id}/impact",
    response_model=AgentLifecycleImpactResponse,
    dependencies=[Depends(require_permission(AdminPermission.AGENT_READ))],
)
async def get_agent_impact(
    agent_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> AgentLifecycleImpactResponse:
    """预览归档或软删除智能体会影响的安全计数与阻断条件。"""
    try:
        impact = await service.get_agent_impact(
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _impact_response(impact)


@router.post(
    "/agents/{agent_id}/archive",
    response_model=ManagedAgentResponse,
    dependencies=[Depends(require_permission(AdminPermission.AGENT_WRITE))],
)
async def archive_agent(
    agent_id: UUID,
    command: AgentLifecycleCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedAgentResponse:
    """经逐字确认后归档智能体，停止新运行、渠道和主动行为。"""
    try:
        item = await service.archive_agent(
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            actor_id=principal.user_id,
            confirmation=command.confirmation,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AdministrationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ManagedAgentResponse.model_validate(item, from_attributes=True)


@router.post(
    "/agents/{agent_id}/delete",
    response_model=ManagedAgentResponse,
    dependencies=[Depends(require_permission(AdminPermission.AGENT_WRITE))],
)
async def soft_delete_agent(
    agent_id: UUID,
    command: AgentLifecycleCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedAgentResponse:
    """经逐字确认后软删除已归档智能体，并登记最早物理清理时间。"""
    try:
        item = await service.soft_delete_agent(
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            actor_id=principal.user_id,
            confirmation=command.confirmation,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AdministrationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ManagedAgentResponse.model_validate(item, from_attributes=True)


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
    """经明确确认后批量启停当前租户的智能体。"""
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
    except AdministrationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
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


@router.get(
    "/users/{user_id}",
    response_model=ManagedUserDetailResponse,
    dependencies=[Depends(require_permission(AdminPermission.USER_READ))],
)
async def get_user_detail(
    user_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedUserDetailResponse:
    """返回当前租户内不含凭证与令牌摘要的用户身份治理详情。"""
    try:
        detail = await service.get_user_detail(
            tenant_id=principal.tenant_id,
            user_id=user_id,
        )
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return _user_detail_response(detail)


@router.post(
    "/users/{user_id}/sessions/{session_id}/revoke",
    response_model=ManagedAdminSessionResponse,
    dependencies=[Depends(require_permission(AdminPermission.USER_WRITE))],
)
async def revoke_user_admin_session(
    user_id: UUID,
    session_id: UUID,
    command: UserSessionRevokeCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedAdminSessionResponse:
    """逐字确认并审计地撤销当前租户内的指定管理会话。"""
    try:
        item = await service.revoke_admin_session(
            tenant_id=principal.tenant_id,
            user_id=user_id,
            session_id=session_id,
            actor_id=principal.user_id,
            confirmation=command.confirmation,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AdministrationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _admin_session_response(item)


@router.patch(
    "/users/{user_id}/access-policy",
    response_model=ManagedUserAccessPolicyResponse,
    dependencies=[Depends(require_permission(AdminPermission.USER_WRITE))],
)
async def update_user_access_policy(
    user_id: UUID,
    command: UserAccessPolicyUpdateCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedUserAccessPolicyResponse:
    """经逐字确认更新用户限流与临时停用策略。"""
    try:
        item = await service.update_user_access_policy(
            tenant_id=principal.tenant_id,
            user_id=user_id,
            request_rate_limit_per_minute=command.request_rate_limit_per_minute,
            suspended_until=command.suspended_until,
            suspension_reason=command.suspension_reason,
            actor_id=principal.user_id,
            confirmation=command.confirmation,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return ManagedUserAccessPolicyResponse.model_validate(item, from_attributes=True)


@router.put(
    "/users/{user_id}/role-override",
    response_model=ManagedRoleAssignmentResponse,
    dependencies=[Depends(require_permission(AdminPermission.USER_ROLE_WRITE))],
)
async def set_user_role_override(
    user_id: UUID,
    command: UserRoleOverrideCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedRoleAssignmentResponse:
    """仅管理员可显式覆盖用户角色，且保留可信身份源角色。"""
    try:
        item = await service.set_user_role_override(
            tenant_id=principal.tenant_id,
            user_id=user_id,
            role=command.role,
            override_expires_at=command.override_expires_at,
            actor_id=principal.user_id,
            confirmation=command.confirmation,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AdministrationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ManagedRoleAssignmentResponse.model_validate(item, from_attributes=True)


@router.post(
    "/users/{user_id}/role-override/revoke",
    response_model=ManagedRoleAssignmentResponse,
    dependencies=[Depends(require_permission(AdminPermission.USER_ROLE_WRITE))],
)
async def revoke_user_role_override(
    user_id: UUID,
    command: UserRoleOverrideRevokeCommand,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[AdministrationService, Depends(get_administration_service)],
) -> ManagedRoleAssignmentResponse:
    """仅管理员可撤销手工角色覆盖并恢复可信基线。"""
    try:
        item = await service.revoke_user_role_override(
            tenant_id=principal.tenant_id,
            user_id=user_id,
            actor_id=principal.user_id,
            confirmation=command.confirmation,
        )
    except AdministrationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AdministrationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AdministrationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ManagedRoleAssignmentResponse.model_validate(item, from_attributes=True)


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
    resource_type: Annotated[str | None, Query(max_length=120)] = None,
    resource_id: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> AuditRecordListResponse:
    """查询当前租户和系统级只追加审计记录，支持资源精确过滤。"""
    try:
        page = await service.list_audit_records(
            tenant_id=principal.tenant_id,
            search=search,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
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


def _impact_response(impact: AgentLifecycleImpact) -> AgentLifecycleImpactResponse:
    """显式映射计算属性，保持 OpenAPI 与领域对象同步。"""
    counts = impact.counts
    return AgentLifecycleImpactResponse(
        agent=ManagedAgentResponse.model_validate(impact.agent, from_attributes=True),
        counts=AgentImpactCountsResponse(
            conversations=counts.conversations,
            agent_runs=counts.agent_runs,
            cognition_resource_versions=counts.cognition_resource_versions,
            memories=counts.memories,
            relationships=counts.relationships,
            evaluation_suites=counts.evaluation_suites,
            evaluation_runs=counts.evaluation_runs,
            channel_instances=counts.channel_instances,
            scheduled_actions=counts.scheduled_actions,
            total=counts.total,
        ),
        active_replacement_count=impact.active_replacement_count,
        can_archive=impact.can_archive,
        can_delete=impact.can_delete,
        blockers=impact.blockers,
        archive_confirmation=impact.archive_confirmation,
        delete_confirmation=impact.delete_confirmation,
        deleted_agent_retention_days=impact.deleted_agent_retention_days,
    )


def _admin_session_response(item: ManagedAdminSession) -> ManagedAdminSessionResponse:
    """显式白名单映射管理会话，防止持久化令牌字段进入响应。"""
    return ManagedAdminSessionResponse(
        id=item.id,
        external_identity_id=item.external_identity_id,
        issued_at=item.issued_at,
        expires_at=item.expires_at,
        last_seen_at=item.last_seen_at,
        revoked_at=item.revoked_at,
    )


def _user_detail_response(detail: ManagedUserDetail) -> ManagedUserDetailResponse:
    """按公开字段映射用户详情，令牌、claim 与消息正文不参与序列化。"""
    return ManagedUserDetailResponse(
        user=ManagedUserResponse.model_validate(detail.user, from_attributes=True),
        tenant=ManagedTenantResponse.model_validate(detail.tenant, from_attributes=True),
        role_assignment=(
            None
            if detail.role_assignment is None
            else ManagedRoleAssignmentResponse.model_validate(
                detail.role_assignment,
                from_attributes=True,
            )
        ),
        access_policy=ManagedUserAccessPolicyResponse.model_validate(
            detail.access_policy,
            from_attributes=True,
        ),
        external_identities=tuple(
            ManagedExternalIdentityResponse.model_validate(item, from_attributes=True)
            for item in detail.external_identities
        ),
        admin_sessions=tuple(_admin_session_response(item) for item in detail.admin_sessions),
        conversation_memberships=tuple(
            ManagedConversationMembershipResponse.model_validate(item, from_attributes=True)
            for item in detail.conversation_memberships
        ),
    )
