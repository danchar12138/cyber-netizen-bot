"""Agent、用户与审计资源的管理应用用例。"""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from cnb_application.configuration_service import ConfigurationService
from cnb_application.pagination import (
    EntityCursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)
from cnb_domain import (
    AGENT_ARCHIVE_CONFIRMATION_PREFIX,
    AGENT_DELETE_CONFIRMATION_PREFIX,
    USER_ACCESS_POLICY_CONFIRMATION_PREFIX,
    USER_ROLE_OVERRIDE_CONFIRMATION_PREFIX,
    USER_ROLE_OVERRIDE_REVOKE_CONFIRMATION_PREFIX,
    USER_SESSION_REVOKE_CONFIRMATION_PREFIX,
    AdminPrincipal,
    AdminRole,
    AgentImpactCounts,
    AgentLifecycleImpact,
    AgentLifecycleStatus,
    AuditRecord,
    EntityStatus,
    JsonValue,
    ManagedAdminSession,
    ManagedAgent,
    ManagedRoleAssignment,
    ManagedUser,
    ManagedUserAccessPolicy,
    ManagedUserDetail,
    ManagementOverview,
)


class AdministrationValidationError(ValueError):
    """管理命令缺少有效目标或确认时抛出。"""


class AdministrationNotFoundError(LookupError):
    """批量命令包含当前租户不可见的对象时抛出。"""


class AdministrationConflictError(RuntimeError):
    """Agent 名称或当前状态与管理命令冲突时抛出。"""


class AdministrationAccessDeniedError(PermissionError):
    """用户状态或临时停用策略拒绝已认证请求时抛出。"""


class AdministrationRateLimitError(RuntimeError):
    """用户请求预算耗尽，并携带可安全返回的重试秒数。"""

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class AuditCursor:
    """审计日志按时间与自增 ID 组成的稳定键集游标。"""

    occurred_at: datetime
    record_id: int


@dataclass(frozen=True, slots=True)
class ManagementPage[T]:
    """后台通用列表的键集分页结果。"""

    items: tuple[T, ...]
    next_cursor: str | None


class AdministrationRepository(Protocol):
    """Agent、用户状态和只追加审计的持久化边界。"""

    async def get_overview(self, *, tenant_id: UUID) -> ManagementOverview: ...

    async def authorize_admin_request(
        self,
        *,
        principal: AdminPrincipal,
        requested_at: datetime,
        window_started_at: datetime,
    ) -> AdminPrincipal: ...

    async def list_agents(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: AgentLifecycleStatus | None,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[ManagedAgent, ...]: ...

    async def get_agent(self, *, tenant_id: UUID, agent_id: UUID) -> ManagedAgent | None: ...

    async def create_agent(
        self,
        *,
        tenant_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent: ...

    async def copy_agent(
        self,
        *,
        tenant_id: UUID,
        source_agent_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent: ...

    async def rename_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent: ...

    async def get_agent_impact(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> tuple[AgentImpactCounts, int]: ...

    async def archive_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        archived_at: datetime,
    ) -> ManagedAgent: ...

    async def soft_delete_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        deleted_at: datetime,
        purge_after: datetime,
        retention_days: int,
    ) -> ManagedAgent: ...

    async def update_agent_status(
        self,
        *,
        tenant_id: UUID,
        agent_ids: tuple[UUID, ...],
        status: EntityStatus,
        actor_id: UUID,
    ) -> tuple[ManagedAgent, ...]: ...

    async def list_users(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: EntityStatus | None,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[ManagedUser, ...]: ...

    async def get_user_detail(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> ManagedUserDetail | None: ...

    async def revoke_admin_session(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        session_id: UUID,
        actor_id: UUID,
        revoked_at: datetime,
    ) -> ManagedAdminSession: ...

    async def update_user_access_policy(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        request_rate_limit_per_minute: int | None,
        suspended_until: datetime | None,
        suspension_reason: str | None,
        actor_id: UUID,
        updated_at: datetime,
    ) -> ManagedUserAccessPolicy: ...

    async def set_user_role_override(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role: AdminRole,
        override_expires_at: datetime | None,
        actor_id: UUID,
        updated_at: datetime,
    ) -> ManagedRoleAssignment: ...

    async def revoke_user_role_override(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        actor_id: UUID,
        updated_at: datetime,
    ) -> ManagedRoleAssignment: ...

    async def update_user_status(
        self,
        *,
        tenant_id: UUID,
        user_ids: tuple[UUID, ...],
        status: EntityStatus,
        actor_id: UUID,
    ) -> tuple[ManagedUser, ...]: ...

    async def list_audit_records(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        action: str | None,
        resource_type: str | None,
        resource_id: str | None,
        limit: int,
        cursor: AuditCursor | None,
    ) -> tuple[AuditRecord, ...]: ...

    async def record_audit(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: str | None,
        detail: Mapping[str, JsonValue],
    ) -> None: ...


class AdministrationService:
    """提供搜索、分页和必须显式确认的批量启停操作。"""

    def __init__(
        self,
        repository: AdministrationRepository,
        configuration_service: ConfigurationService,
    ) -> None:
        self._repository = repository
        self._configuration_service = configuration_service

    async def get_overview(self, *, tenant_id: UUID) -> ManagementOverview:
        return await self._repository.get_overview(tenant_id=tenant_id)

    async def list_agents(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: AgentLifecycleStatus | None,
        limit: int,
        cursor: str | None,
    ) -> ManagementPage[ManagedAgent]:
        rows = await self._repository.list_agents(
            tenant_id=tenant_id,
            search=self._normalize_search(search),
            status=status,
            limit=limit + 1,
            cursor=decode_cursor(cursor),
        )
        return self._entity_page(rows, limit, lambda item: EntityCursor(item.created_at, item.id))

    async def get_agent(self, *, tenant_id: UUID, agent_id: UUID) -> ManagedAgent:
        agent = await self._repository.get_agent(tenant_id=tenant_id, agent_id=agent_id)
        if agent is None:
            raise AdministrationNotFoundError("智能体不存在")
        return agent

    async def create_agent(
        self,
        *,
        tenant_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent:
        """创建一个启用状态且认知资源独立的新 Agent。"""
        return await self._repository.create_agent(
            tenant_id=tenant_id,
            name=self._normalize_agent_name(name),
            actor_id=actor_id,
        )

    async def copy_agent(
        self,
        *,
        tenant_id: UUID,
        source_agent_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent:
        """复制 Agent，并让源 Agent 的已发布认知资源从 v1 独立演进。"""
        return await self._repository.copy_agent(
            tenant_id=tenant_id,
            source_agent_id=source_agent_id,
            name=self._normalize_agent_name(name),
            actor_id=actor_id,
        )

    async def rename_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        name: str,
        actor_id: UUID,
    ) -> ManagedAgent:
        """重命名未进入软删除状态的 Agent。"""
        return await self._repository.rename_agent(
            tenant_id=tenant_id,
            agent_id=agent_id,
            name=self._normalize_agent_name(name),
            actor_id=actor_id,
        )

    async def get_agent_impact(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
    ) -> AgentLifecycleImpact:
        """返回不含正文、密钥或对象键的生命周期影响预览。"""
        agent = await self.get_agent(tenant_id=tenant_id, agent_id=agent_id)
        counts, active_replacement_count = await self._repository.get_agent_impact(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        retention_days = await self._deleted_agent_retention_days(tenant_id)
        blockers: list[str] = []
        can_archive = agent.status in {
            AgentLifecycleStatus.ACTIVE,
            AgentLifecycleStatus.DISABLED,
        }
        if not can_archive:
            blockers.append("只有已启用或已停用的智能体可以归档")
        if can_archive and active_replacement_count < 1:
            can_archive = False
            blockers.append("归档前必须保留至少一个其他已启用智能体")
        can_delete = agent.status is AgentLifecycleStatus.ARCHIVED
        if not can_delete:
            blockers.append("只有已归档的智能体可以进入软删除保留期")
        return AgentLifecycleImpact(
            agent=agent,
            counts=counts,
            active_replacement_count=active_replacement_count,
            can_archive=can_archive,
            can_delete=can_delete,
            blockers=tuple(blockers),
            archive_confirmation=f"{AGENT_ARCHIVE_CONFIRMATION_PREFIX}{agent.id}",
            delete_confirmation=f"{AGENT_DELETE_CONFIRMATION_PREFIX}{agent.id}",
            deleted_agent_retention_days=retention_days,
        )

    async def archive_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        confirmation: str,
    ) -> ManagedAgent:
        """显式确认后归档 Agent，并阻断新运行及主动触达。"""
        expected = f"{AGENT_ARCHIVE_CONFIRMATION_PREFIX}{agent_id}"
        if confirmation != expected:
            raise AdministrationValidationError(f"归档操作必须准确输入：{expected}")
        return await self._repository.archive_agent(
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor_id=actor_id,
            archived_at=datetime.now(UTC),
        )

    async def soft_delete_agent(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
        confirmation: str,
    ) -> ManagedAgent:
        """显式确认后软删除已归档 Agent，物理清理交给保留期流程。"""
        expected = f"{AGENT_DELETE_CONFIRMATION_PREFIX}{agent_id}"
        if confirmation != expected:
            raise AdministrationValidationError(f"删除操作必须准确输入：{expected}")
        retention_days = await self._deleted_agent_retention_days(tenant_id)
        deleted_at = datetime.now(UTC)
        return await self._repository.soft_delete_agent(
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor_id=actor_id,
            deleted_at=deleted_at,
            purge_after=deleted_at + timedelta(days=retention_days),
            retention_days=retention_days,
        )

    async def update_agent_status(
        self,
        *,
        tenant_id: UUID,
        agent_ids: tuple[UUID, ...],
        status: EntityStatus,
        actor_id: UUID,
        confirmed: bool,
    ) -> tuple[ManagedAgent, ...]:
        self._validate_bulk_command(agent_ids, confirmed)
        return await self._repository.update_agent_status(
            tenant_id=tenant_id,
            agent_ids=agent_ids,
            status=status,
            actor_id=actor_id,
        )

    async def list_users(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: EntityStatus | None,
        limit: int,
        cursor: str | None,
    ) -> ManagementPage[ManagedUser]:
        rows = await self._repository.list_users(
            tenant_id=tenant_id,
            search=self._normalize_search(search),
            status=status,
            limit=limit + 1,
            cursor=decode_cursor(cursor),
        )
        return self._entity_page(rows, limit, lambda item: EntityCursor(item.created_at, item.id))

    async def update_user_status(
        self,
        *,
        tenant_id: UUID,
        user_ids: tuple[UUID, ...],
        status: EntityStatus,
        actor_id: UUID,
        confirmed: bool,
    ) -> tuple[ManagedUser, ...]:
        self._validate_bulk_command(user_ids, confirmed)
        return await self._repository.update_user_status(
            tenant_id=tenant_id,
            user_ids=user_ids,
            status=status,
            actor_id=actor_id,
        )

    async def get_user_detail(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> ManagedUserDetail:
        """返回当前租户内不含凭证与令牌摘要的用户身份治理详情。"""
        detail = await self._repository.get_user_detail(
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if detail is None:
            raise AdministrationNotFoundError("用户不存在")
        return detail

    async def revoke_admin_session(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        session_id: UUID,
        actor_id: UUID,
        confirmation: str,
    ) -> ManagedAdminSession:
        """逐字确认后撤销用户的指定管理会话。"""
        expected = f"{USER_SESSION_REVOKE_CONFIRMATION_PREFIX}{session_id}"
        if confirmation != expected:
            raise AdministrationValidationError(f"撤销操作必须准确输入：{expected}")
        return await self._repository.revoke_admin_session(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            actor_id=actor_id,
            revoked_at=datetime.now(UTC),
        )

    async def update_user_access_policy(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        request_rate_limit_per_minute: int | None,
        suspended_until: datetime | None,
        suspension_reason: str | None,
        actor_id: UUID,
        confirmation: str,
    ) -> ManagedUserAccessPolicy:
        """逐字确认后更新用户限流和临时停用策略。"""
        expected = f"{USER_ACCESS_POLICY_CONFIRMATION_PREFIX}{user_id}"
        if confirmation != expected:
            raise AdministrationValidationError(f"访问策略操作必须准确输入：{expected}")
        if request_rate_limit_per_minute is not None and not (
            1 <= request_rate_limit_per_minute <= 10_000
        ):
            raise AdministrationValidationError("用户每分钟请求上限必须在 1 到 10000 之间")
        now = datetime.now(UTC)
        normalized_reason = " ".join((suspension_reason or "").strip().split()) or None
        if suspended_until is not None:
            if suspended_until.tzinfo is None:
                raise AdministrationValidationError("临时停用时间必须包含时区")
            if suspended_until <= now:
                raise AdministrationValidationError("临时停用时间必须晚于当前时间")
            if suspended_until > now + timedelta(days=365):
                raise AdministrationValidationError("临时停用时间不能超过 365 天")
            if normalized_reason is None:
                raise AdministrationValidationError("临时停用必须填写原因")
            if len(normalized_reason) > 500:
                raise AdministrationValidationError("临时停用原因不能超过 500 个字符")
        elif normalized_reason is not None:
            raise AdministrationValidationError("未设置临时停用时间时不能保留停用原因")
        return await self._repository.update_user_access_policy(
            tenant_id=tenant_id,
            user_id=user_id,
            request_rate_limit_per_minute=request_rate_limit_per_minute,
            suspended_until=suspended_until,
            suspension_reason=normalized_reason,
            actor_id=actor_id,
            updated_at=now,
        )

    async def set_user_role_override(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role: AdminRole,
        override_expires_at: datetime | None,
        actor_id: UUID,
        confirmation: str,
    ) -> ManagedRoleAssignment:
        """逐字确认后以可撤销方式覆盖可信身份源角色。"""
        expected = f"{USER_ROLE_OVERRIDE_CONFIRMATION_PREFIX}{user_id}"
        if confirmation != expected:
            raise AdministrationValidationError(f"角色覆盖操作必须准确输入：{expected}")
        now = datetime.now(UTC)
        if override_expires_at is not None:
            if override_expires_at.tzinfo is None:
                raise AdministrationValidationError("角色覆盖到期时间必须包含时区")
            if override_expires_at <= now:
                raise AdministrationValidationError("角色覆盖到期时间必须晚于当前时间")
            if override_expires_at > now + timedelta(days=365):
                raise AdministrationValidationError("角色覆盖期限不能超过 365 天")
        return await self._repository.set_user_role_override(
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            override_expires_at=override_expires_at,
            actor_id=actor_id,
            updated_at=now,
        )

    async def revoke_user_role_override(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        actor_id: UUID,
        confirmation: str,
    ) -> ManagedRoleAssignment:
        """显式撤销手工覆盖并恢复最近一次可信身份源角色。"""
        expected = f"{USER_ROLE_OVERRIDE_REVOKE_CONFIRMATION_PREFIX}{user_id}"
        if confirmation != expected:
            raise AdministrationValidationError(f"撤销角色覆盖必须准确输入：{expected}")
        return await self._repository.revoke_user_role_override(
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor_id,
            updated_at=datetime.now(UTC),
        )

    async def list_audit_records(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        action: str | None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int,
        cursor: str | None,
    ) -> ManagementPage[AuditRecord]:
        rows = await self._repository.list_audit_records(
            tenant_id=tenant_id,
            search=self._normalize_search(search),
            action=self._normalize_search(action),
            resource_type=self._normalize_search(resource_type),
            resource_id=self._normalize_search(resource_id),
            limit=limit + 1,
            cursor=decode_audit_cursor(cursor),
        )
        visible = tuple(rows[:limit])
        next_cursor = (
            encode_audit_cursor(AuditCursor(visible[-1].created_at, visible[-1].id))
            if len(rows) > limit and visible
            else None
        )
        return ManagementPage(items=visible, next_cursor=next_cursor)

    @staticmethod
    def _validate_bulk_command(ids: tuple[UUID, ...], confirmed: bool) -> None:
        if not ids:
            raise AdministrationValidationError("至少选择一个管理对象")
        if len(ids) > 100:
            raise AdministrationValidationError("单次批量操作不能超过 100 个对象")
        if len(set(ids)) != len(ids):
            raise AdministrationValidationError("批量操作包含重复对象")
        if not confirmed:
            raise AdministrationValidationError("批量状态变更必须明确确认影响范围")

    @staticmethod
    def _normalize_search(value: str | None) -> str | None:
        normalized = value.strip() if value else ""
        return normalized or None

    @staticmethod
    def _normalize_agent_name(value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise AdministrationValidationError("Agent 名称不能为空")
        if len(normalized) > 120:
            raise AdministrationValidationError("Agent 名称不能超过 120 个字符")
        if any(ord(character) < 32 for character in normalized):
            raise AdministrationValidationError("Agent 名称不能包含控制字符")
        return normalized

    async def _deleted_agent_retention_days(self, tenant_id: UUID) -> int:
        configuration = await self._configuration_service.resolve_effective(tenant_id=tenant_id)
        value = configuration.values.get("data.retention.deleted_agent_days")
        if not isinstance(value, int) or isinstance(value, bool):
            raise AdministrationValidationError("Agent 删除保留期配置类型无效")
        return value

    @staticmethod
    def _entity_page[T](
        rows: Sequence[T], limit: int, cursor_for: Callable[[T], EntityCursor]
    ) -> ManagementPage[T]:
        visible = tuple(rows[:limit])
        next_cursor = (
            encode_cursor(cursor_for(visible[-1])) if len(rows) > limit and visible else None
        )
        return ManagementPage(items=visible, next_cursor=next_cursor)


def encode_audit_cursor(cursor: AuditCursor) -> str:
    """编码审计键集游标。"""
    raw = f"{cursor.occurred_at.isoformat()}|{cursor.record_id}".encode()
    return urlsafe_b64encode(raw).decode().rstrip("=")


def decode_audit_cursor(value: str | None) -> AuditCursor | None:
    """解析审计游标，并复用统一的友好错误语义。"""
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        timestamp, record_id = urlsafe_b64decode(padded).decode().split("|", maxsplit=1)
        occurred_at = datetime.fromisoformat(timestamp)
        parsed_id = int(record_id)
        if occurred_at.tzinfo is None or parsed_id < 1:
            raise ValueError
        return AuditCursor(occurred_at=occurred_at, record_id=parsed_id)
    except (UnicodeDecodeError, ValueError) as error:
        raise InvalidCursorError("分页游标无效") from error
