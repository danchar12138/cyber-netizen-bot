"""管理平面的角色、权限与会话主体。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.configuration import JsonValue
from cnb_domain.conversation import EntityStatus

AGENT_ARCHIVE_CONFIRMATION_PREFIX = "确认归档 Agent "
AGENT_DELETE_CONFIRMATION_PREFIX = "确认删除 Agent "


class AgentLifecycleStatus(StrEnum):
    """Agent 从可运行到保留期等待清理的完整生命周期。"""

    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"
    DELETED = "deleted"


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
    EVALUATION_REVIEW = "evaluation:review"
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    MEMORY_REBUILD = "memory:rebuild"
    TASK_READ = "task:read"
    TASK_MANAGE = "task:manage"
    PROACTIVE_MANAGE = "proactive:manage"
    CHANNEL_READ = "channel:read"
    CHANNEL_WRITE = "channel:write"
    CHANNEL_SEND = "channel:send"
    CHANNEL_CREDENTIAL_MANAGE = "channel_credential:manage"
    TRACE_READ = "trace:read"
    USER_READ = "user:read"
    USER_WRITE = "user:write"
    AUDIT_READ = "audit:read"
    DATA_LIFECYCLE_READ = "data_lifecycle:read"
    DATA_EXPORT = "data_lifecycle:export"
    DATA_FORGET = "data_lifecycle:forget"
    DATA_RETENTION_MANAGE = "data_lifecycle:retention_manage"
    BACKUP_DRILL_RECORD = "data_lifecycle:backup_drill_record"


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
    """管理后台可查看的 Agent 生命周期摘要。"""

    id: UUID
    tenant_id: UUID
    name: str
    status: AgentLifecycleStatus
    created_at: datetime
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    purge_after: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentImpactCounts:
    """归档或软删除 Agent 前可安全公开的依赖数量。"""

    conversations: int = 0
    agent_runs: int = 0
    cognition_resource_versions: int = 0
    memories: int = 0
    relationships: int = 0
    evaluation_suites: int = 0
    evaluation_runs: int = 0
    channel_instances: int = 0
    scheduled_actions: int = 0

    @property
    def total(self) -> int:
        """返回各类顶层依赖记录的合计，仅用于影响规模提示。"""
        return sum(
            (
                self.conversations,
                self.agent_runs,
                self.cognition_resource_versions,
                self.memories,
                self.relationships,
                self.evaluation_suites,
                self.evaluation_runs,
                self.channel_instances,
                self.scheduled_actions,
            )
        )


@dataclass(frozen=True, slots=True)
class AgentLifecycleImpact:
    """Agent 生命周期命令执行前的影响、阻断原因与确认短语。"""

    agent: ManagedAgent
    counts: AgentImpactCounts
    active_replacement_count: int
    can_archive: bool
    can_delete: bool
    blockers: tuple[str, ...]
    archive_confirmation: str
    delete_confirmation: str
    deleted_agent_retention_days: int


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
