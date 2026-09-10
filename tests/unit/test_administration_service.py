"""管理资源搜索、分页、批量确认与审计测试。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cnb_application import (
    AdministrationConflictError,
    AdministrationNotFoundError,
    AdministrationService,
    AdministrationValidationError,
    AuditCursor,
    ConfigurationService,
    InvalidCursorError,
    build_default_registry,
    decode_audit_cursor,
    encode_audit_cursor,
)
from cnb_domain import (
    AgentImpactCounts,
    AgentLifecycleStatus,
    CognitionResourceKind,
    DevelopmentIdentity,
    EntityStatus,
    IdentityGovernanceSource,
    ManagedAdminSession,
)
from cnb_infrastructure import (
    MemoryAdministrationRepository,
    MemoryCognitionRepository,
    MemoryConfigurationRepository,
)


def _identity() -> DevelopmentIdentity:
    return DevelopmentIdentity(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        user_name="管理测试用户",
        agent_name="管理测试 Agent",
    )


def _service(repository: MemoryAdministrationRepository) -> AdministrationService:
    return AdministrationService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )


async def test_management_lists_seed_identity_and_audits_bulk_status_change() -> None:
    identity = _identity()
    service = _service(MemoryAdministrationRepository(identity))

    agents = await service.list_agents(
        tenant_id=identity.tenant_id,
        search="测试 Agent",
        status=AgentLifecycleStatus.ACTIVE,
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
    assert updated[0].status is AgentLifecycleStatus.DISABLED
    assert audit.items[0].action == "agent.status_updated"
    assert audit.items[0].detail["status"] == "disabled"


async def test_bulk_status_change_requires_explicit_confirmation() -> None:
    identity = _identity()
    service = _service(MemoryAdministrationRepository(identity))

    with pytest.raises(AdministrationValidationError, match="明确确认"):
        await service.update_user_status(
            tenant_id=identity.tenant_id,
            user_ids=(identity.user_id,),
            status=EntityStatus.DISABLED,
            actor_id=identity.user_id,
            confirmed=False,
        )


async def test_user_detail_marks_development_identity_without_fabricated_oidc_data() -> None:
    identity = _identity()
    service = _service(MemoryAdministrationRepository(identity))

    detail = await service.get_user_detail(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
    )

    assert detail.tenant.name == "本地开发环境"
    assert detail.role_assignment is not None
    assert detail.role_assignment.source is IdentityGovernanceSource.DEVELOPMENT
    assert detail.external_identities == ()
    assert detail.admin_sessions == ()
    assert detail.conversation_memberships == ()

    with pytest.raises(AdministrationNotFoundError, match="用户不存在"):
        await service.get_user_detail(tenant_id=uuid4(), user_id=identity.user_id)


async def test_admin_session_revoke_requires_exact_confirmation_and_is_idempotency_safe() -> None:
    identity = _identity()
    repository = MemoryAdministrationRepository(identity)
    service = _service(repository)
    now = datetime.now(UTC)
    session = ManagedAdminSession(
        id=uuid4(),
        external_identity_id=uuid4(),
        issued_at=now - timedelta(minutes=10),
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        revoked_at=None,
    )
    repository.seed_admin_session(identity.user_id, session)

    with pytest.raises(AdministrationValidationError, match="必须准确输入"):
        await service.revoke_admin_session(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            session_id=session.id,
            actor_id=identity.user_id,
            confirmation="确认撤销",
        )

    revoked = await service.revoke_admin_session(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        session_id=session.id,
        actor_id=identity.user_id,
        confirmation=f"确认撤销管理会话 {session.id}",
    )
    detail = await service.get_user_detail(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
    )
    audit = await service.list_audit_records(
        tenant_id=identity.tenant_id,
        search=None,
        action="admin_session.revoked",
        limit=20,
        cursor=None,
    )

    assert revoked.revoked_at is not None
    assert detail.admin_sessions == (revoked,)
    assert audit.items[0].resource_id == str(session.id)
    assert "token" not in str(audit.items[0].detail).lower()

    with pytest.raises(AdministrationConflictError, match="已撤销"):
        await service.revoke_admin_session(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            session_id=session.id,
            actor_id=identity.user_id,
            confirmation=f"确认撤销管理会话 {session.id}",
        )


async def test_create_and_copy_agent_clones_only_published_cognition_resources() -> None:
    identity = _identity()
    cognition = MemoryCognitionRepository()
    repository = MemoryAdministrationRepository(identity, cognition_cloner=cognition)
    service = _service(repository)
    draft = await cognition.create_resource_draft(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        kind=CognitionResourceKind.PROMPT,
        key="chat.realizer",
        name="自然表达",
        payload={"template": "自然回应"},
        note="待发布基线",
        actor_id=identity.user_id,
    )
    await cognition.publish_resource(
        resource_id=draft.id,
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        actor_id=identity.user_id,
    )
    await cognition.create_resource_draft(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        kind=CognitionResourceKind.POLICY,
        key="draft-only",
        name="未发布策略",
        payload={},
        note=None,
        actor_id=identity.user_id,
    )

    blank = await service.create_agent(
        tenant_id=identity.tenant_id,
        name="  空白   Agent  ",
        actor_id=identity.user_id,
    )
    copied = await service.copy_agent(
        tenant_id=identity.tenant_id,
        source_agent_id=identity.agent_id,
        name="人格副本",
        actor_id=identity.user_id,
    )
    resources = await cognition.list_resource_versions(
        tenant_id=identity.tenant_id,
        agent_id=copied.id,
        kind=None,
    )

    assert blank.name == "空白 Agent"
    assert copied.status is AgentLifecycleStatus.ACTIVE
    assert len(resources) == 1
    assert resources[0].key == "chat.realizer"
    assert resources[0].version == 1
    assert resources[0].payload == {"template": "自然回应"}

    with pytest.raises(AdministrationConflictError, match="同名 Agent"):
        await service.create_agent(
            tenant_id=identity.tenant_id,
            name="人格副本",
            actor_id=identity.user_id,
        )


async def test_agent_lifecycle_requires_preview_replacement_and_exact_confirmations() -> None:
    identity = _identity()
    repository = MemoryAdministrationRepository(identity)
    service = _service(repository)
    initial_impact = await service.get_agent_impact(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
    )

    assert initial_impact.can_archive is False
    assert initial_impact.active_replacement_count == 0
    assert "至少一个其他已启用 Agent" in initial_impact.blockers[0]

    replacement = await service.create_agent(
        tenant_id=identity.tenant_id,
        name="生命周期替代 Agent",
        actor_id=identity.user_id,
    )
    repository.seed_agent_impact(
        identity.agent_id,
        AgentImpactCounts(
            conversations=2,
            agent_runs=4,
            cognition_resource_versions=3,
            memories=5,
            relationships=1,
            evaluation_suites=2,
            evaluation_runs=6,
            channel_instances=1,
            scheduled_actions=3,
        ),
    )
    renamed = await service.rename_agent(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        name="  长期   伙伴  ",
        actor_id=identity.user_id,
    )
    impact = await service.get_agent_impact(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
    )

    assert renamed.name == "长期 伙伴"
    assert impact.counts.total == 27
    assert impact.active_replacement_count == 1
    assert impact.can_archive is True
    assert impact.can_delete is False
    assert impact.deleted_agent_retention_days == 30
    assert impact.archive_confirmation == f"确认归档 Agent {identity.agent_id}"

    with pytest.raises(AdministrationValidationError, match="必须准确输入"):
        await service.archive_agent(
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            actor_id=identity.user_id,
            confirmation="确认归档",
        )

    archived = await service.archive_agent(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        actor_id=identity.user_id,
        confirmation=impact.archive_confirmation,
    )
    archived_impact = await service.get_agent_impact(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
    )
    deleted = await service.soft_delete_agent(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        actor_id=identity.user_id,
        confirmation=archived_impact.delete_confirmation,
    )
    audits = await service.list_audit_records(
        tenant_id=identity.tenant_id,
        search="agent",
        action=None,
        limit=20,
        cursor=None,
    )

    assert archived.status is AgentLifecycleStatus.ARCHIVED
    assert archived.archived_at is not None
    assert archived_impact.can_archive is False
    assert archived_impact.can_delete is True
    assert deleted.status is AgentLifecycleStatus.DELETED
    assert deleted.deleted_at is not None
    assert deleted.purge_after is not None
    assert (deleted.purge_after - deleted.deleted_at).days == 30
    assert audits.items[0].action == "agent.soft_deleted"
    assert audits.items[0].detail["physical_delete_performed"] is False
    assert replacement.status is AgentLifecycleStatus.ACTIVE


async def test_agent_lifecycle_rejects_last_agent_and_cross_tenant_target() -> None:
    identity = _identity()
    service = _service(MemoryAdministrationRepository(identity))

    with pytest.raises(AdministrationConflictError, match="至少一个其他已启用 Agent"):
        await service.archive_agent(
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            actor_id=identity.user_id,
            confirmation=f"确认归档 Agent {identity.agent_id}",
        )

    with pytest.raises(AdministrationNotFoundError, match="Agent 不存在"):
        await service.get_agent_impact(tenant_id=uuid4(), agent_id=identity.agent_id)


def test_audit_cursor_round_trip_and_rejects_invalid_values() -> None:
    cursor = AuditCursor(datetime.now(UTC), 42)

    assert decode_audit_cursor(encode_audit_cursor(cursor)) == cursor
    with pytest.raises(InvalidCursorError, match="分页游标无效"):
        decode_audit_cursor("错误游标")
