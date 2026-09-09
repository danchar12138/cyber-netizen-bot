"""Agent、用户与审计资源的管理应用用例。"""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from cnb_application.pagination import (
    EntityCursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)
from cnb_domain import AuditRecord, EntityStatus, ManagedAgent, ManagedUser, ManagementOverview


class AdministrationValidationError(ValueError):
    """管理命令缺少有效目标或确认时抛出。"""


class AdministrationNotFoundError(LookupError):
    """批量命令包含当前租户不可见的对象时抛出。"""


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

    async def list_agents(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: EntityStatus | None,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[ManagedAgent, ...]: ...

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
        limit: int,
        cursor: AuditCursor | None,
    ) -> tuple[AuditRecord, ...]: ...


class AdministrationService:
    """提供搜索、分页和必须显式确认的批量启停操作。"""

    def __init__(self, repository: AdministrationRepository) -> None:
        self._repository = repository

    async def get_overview(self, *, tenant_id: UUID) -> ManagementOverview:
        return await self._repository.get_overview(tenant_id=tenant_id)

    async def list_agents(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        status: EntityStatus | None,
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

    async def list_audit_records(
        self,
        *,
        tenant_id: UUID,
        search: str | None,
        action: str | None,
        limit: int,
        cursor: str | None,
    ) -> ManagementPage[AuditRecord]:
        rows = await self._repository.list_audit_records(
            tenant_id=tenant_id,
            search=self._normalize_search(search),
            action=self._normalize_search(action),
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
