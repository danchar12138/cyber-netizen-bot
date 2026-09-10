"""管理会话与角色能力矩阵 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from cnb_domain import (
    AdminPermission,
    AdminRole,
    AgentLifecycleStatus,
    EntityStatus,
    JsonValue,
)


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


class ManagedAgentResponse(BaseModel):
    """Agent 管理列表中的安全摘要。"""

    id: UUID
    tenant_id: UUID
    name: str
    status: AgentLifecycleStatus
    created_at: datetime
    archived_at: datetime | None
    deleted_at: datetime | None
    purge_after: datetime | None


class ManagedAgentListResponse(BaseModel):
    """Agent 键集分页列表。"""

    items: tuple[ManagedAgentResponse, ...]
    next_cursor: str | None


class ManagedAgentCreateCommand(BaseModel):
    """创建独立 Agent 的名称命令。"""

    name: str = Field(min_length=1, max_length=120)


class ManagedAgentCopyCommand(BaseModel):
    """从已有 Agent 复制已发布认知资源的命令。"""

    name: str = Field(min_length=1, max_length=120)


class ManagedAgentRenameCommand(BaseModel):
    """修改 Agent 显示名称的命令。"""

    name: str = Field(min_length=1, max_length=120)


class AgentLifecycleCommand(BaseModel):
    """要求逐字匹配服务端预览短语的高风险生命周期命令。"""

    confirmation: str = Field(min_length=1, max_length=200)


class AgentImpactCountsResponse(BaseModel):
    """归档或软删除前按资源类型统计的依赖数量。"""

    conversations: int
    agent_runs: int
    cognition_resource_versions: int
    memories: int
    relationships: int
    evaluation_suites: int
    evaluation_runs: int
    channel_instances: int
    scheduled_actions: int
    total: int


class AgentLifecycleImpactResponse(BaseModel):
    """管理后台执行 Agent 生命周期命令所需的完整安全预览。"""

    agent: ManagedAgentResponse
    counts: AgentImpactCountsResponse
    active_replacement_count: int
    can_archive: bool
    can_delete: bool
    blockers: tuple[str, ...]
    archive_confirmation: str
    delete_confirmation: str
    deleted_agent_retention_days: int


class ManagedUserResponse(BaseModel):
    """用户管理列表中的安全摘要。"""

    id: UUID
    tenant_id: UUID
    display_name: str
    status: EntityStatus
    created_at: datetime


class ManagedUserListResponse(BaseModel):
    """用户键集分页列表。"""

    items: tuple[ManagedUserResponse, ...]
    next_cursor: str | None


class BulkStatusUpdateCommand(BaseModel):
    """必须明确确认影响范围的批量启停命令。"""

    ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    status: EntityStatus
    confirmed: bool = False


class AuditRecordResponse(BaseModel):
    """不含敏感载荷的管理审计记录。"""

    id: int
    actor_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    detail: dict[str, JsonValue]
    created_at: datetime


class AuditRecordListResponse(BaseModel):
    """审计记录键集分页列表。"""

    items: tuple[AuditRecordResponse, ...]
    next_cursor: str | None
