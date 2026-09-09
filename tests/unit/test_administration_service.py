"""管理资源搜索、分页、批量确认与审计测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cnb_application import (
    AdministrationService,
    AdministrationValidationError,
    AuditCursor,
    InvalidCursorError,
    decode_audit_cursor,
    encode_audit_cursor,
)
from cnb_domain import DevelopmentIdentity, EntityStatus
from cnb_infrastructure import MemoryAdministrationRepository


def _identity() -> DevelopmentIdentity:
    return DevelopmentIdentity(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        user_name="管理测试用户",
        agent_name="管理测试 Agent",
    )


async def test_management_lists_seed_identity_and_audits_bulk_status_change() -> None:
    identity = _identity()
    service = AdministrationService(MemoryAdministrationRepository(identity))

    agents = await service.list_agents(
        tenant_id=identity.tenant_id,
        search="测试 Agent",
        status=EntityStatus.ACTIVE,
        limit=20,
        cursor=None,
    )
    updated = await service.update_agent_status(
        tenant_id=identity.tenant_id,
        agent_ids=(identity.agent_id,),
        status=EntityStatus.DISABLED,
        actor_id=identity.user_id,
        confirmed=True,
    )
    audit = await service.list_audit_records(
        tenant_id=identity.tenant_id,
        search="agent",
        action=None,
        limit=20,
        cursor=None,
    )

    assert agents.items[0].name == "管理测试 Agent"
    assert updated[0].status is EntityStatus.DISABLED
    assert audit.items[0].action == "agent.status_updated"
    assert audit.items[0].detail["status"] == "disabled"


async def test_bulk_status_change_requires_explicit_confirmation() -> None:
    identity = _identity()
    service = AdministrationService(MemoryAdministrationRepository(identity))

    with pytest.raises(AdministrationValidationError, match="明确确认"):
        await service.update_user_status(
            tenant_id=identity.tenant_id,
            user_ids=(identity.user_id,),
            status=EntityStatus.DISABLED,
            actor_id=identity.user_id,
            confirmed=False,
        )


def test_audit_cursor_round_trip_and_rejects_invalid_values() -> None:
    cursor = AuditCursor(datetime.now(UTC), 42)

    assert decode_audit_cursor(encode_audit_cursor(cursor)) == cursor
    with pytest.raises(InvalidCursorError, match="分页游标无效"):
        decode_audit_cursor("错误游标")
