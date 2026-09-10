"""管理资源搜索、分页、批量确认与审计测试。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cnb_application import (
    AdministrationAccessDeniedError,
    AdministrationConflictError,
    AdministrationNotFoundError,
    AdministrationRateLimitError,
    AdministrationService,
    AdministrationValidationError,
    AuditCursor,
    ConfigurationService,
    InvalidCursorError,
    build_default_registry,
    decode_audit_cursor,
    encode_audit_cursor,
    permissions_for_role,
)
from cnb_domain import (
    AdminPrincipal,
    AdminRole,
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


async def test_user_access_policy_validates_confirmation_time_and_safe_audit() -> None:
    identity = _identity()
    repository = MemoryAdministrationRepository(identity)
    service = _service(repository)

    with pytest.raises(AdministrationValidationError, match="必须准确输入"):
        await service.update_user_access_policy(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            request_rate_limit_per_minute=60,
            suspended_until=None,
            suspension_reason=None,
            actor_id=identity.user_id,
            confirmation="确认更新",
        )
    with pytest.raises(AdministrationValidationError, match="1 到 10000"):
        await service.update_user_access_policy(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            request_rate_limit_per_minute=0,
            suspended_until=None,
            suspension_reason=None,
            actor_id=identity.user_id,
            confirmation=f"确认更新用户访问策略 {identity.user_id}",
        )
    with pytest.raises(AdministrationValidationError, match="必须包含时区"):
        await service.update_user_access_policy(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            request_rate_limit_per_minute=None,
            suspended_until=datetime.now() + timedelta(hours=1),
            suspension_reason="维护",
            actor_id=identity.user_id,
            confirmation=f"确认更新用户访问策略 {identity.user_id}",
        )
    with pytest.raises(AdministrationValidationError, match="不能超过 365 天"):
        await service.update_user_access_policy(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            request_rate_limit_per_minute=None,
            suspended_until=datetime.now(UTC) + timedelta(days=366),
            suspension_reason="长期冻结",
            actor_id=identity.user_id,
            confirmation=f"确认更新用户访问策略 {identity.user_id}",
        )
    with pytest.raises(AdministrationValidationError, match="必须填写原因"):
        await service.update_user_access_policy(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            request_rate_limit_per_minute=None,
            suspended_until=datetime.now(UTC) + timedelta(hours=1),
            suspension_reason=None,
            actor_id=identity.user_id,
            confirmation=f"确认更新用户访问策略 {identity.user_id}",
        )
    with pytest.raises(AdministrationValidationError, match="不能保留停用原因"):
        await service.update_user_access_policy(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            request_rate_limit_per_minute=None,
            suspended_until=None,
            suspension_reason="无截止时间",
            actor_id=identity.user_id,
            confirmation=f"确认更新用户访问策略 {identity.user_id}",
        )

    suspended_until = datetime.now(UTC) + timedelta(hours=2)
    policy = await service.update_user_access_policy(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        request_rate_limit_per_minute=60,
        suspended_until=suspended_until,
        suspension_reason="  安全   调查  ",
        actor_id=identity.user_id,
        confirmation=f"确认更新用户访问策略 {identity.user_id}",
    )
    detail = await service.get_user_detail(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
    )
    audit = await service.list_audit_records(
        tenant_id=identity.tenant_id,
        search=None,
        action="user.access_policy_updated",
        limit=20,
        cursor=None,
    )

    assert policy.suspension_reason == "安全 调查"
    assert detail.access_policy == policy
    assert audit.items[0].detail["suspended"] is True
    assert "安全 调查" not in str(audit.items[0].detail)


async def test_user_request_governance_enforces_status_rate_limit_and_window_reset() -> None:
    identity = _identity()
    repository = MemoryAdministrationRepository(identity)
    service = _service(repository)
    principal = AdminPrincipal(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        display_name=identity.user_name,
        role=AdminRole.ADMIN,
        permissions=permissions_for_role(AdminRole.ADMIN),
        authentication_mode="development",
    )
    requested_at = datetime.now(UTC)
    window_started_at = requested_at.replace(second=0, microsecond=0)
    await service.update_user_access_policy(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        request_rate_limit_per_minute=2,
        suspended_until=None,
        suspension_reason=None,
        actor_id=identity.user_id,
        confirmation=f"确认更新用户访问策略 {identity.user_id}",
    )

    await repository.authorize_admin_request(
        principal=principal,
        requested_at=requested_at,
        window_started_at=window_started_at,
    )
    await repository.authorize_admin_request(
        principal=principal,
        requested_at=requested_at + timedelta(seconds=1),
        window_started_at=window_started_at,
    )
    with pytest.raises(AdministrationRateLimitError) as rate_limit:
        await repository.authorize_admin_request(
            principal=principal,
            requested_at=requested_at + timedelta(seconds=2),
            window_started_at=window_started_at,
        )
    assert 1 <= rate_limit.value.retry_after_seconds <= 60

    next_window = window_started_at + timedelta(minutes=1)
    governed = await repository.authorize_admin_request(
        principal=principal,
        requested_at=next_window,
        window_started_at=next_window,
    )
    assert governed.role is AdminRole.ADMIN

    await service.update_user_status(
        tenant_id=identity.tenant_id,
        user_ids=(identity.user_id,),
        status=EntityStatus.DISABLED,
        actor_id=identity.user_id,
        confirmed=True,
    )
    with pytest.raises(AdministrationAccessDeniedError, match="用户已停用"):
        await repository.authorize_admin_request(
            principal=principal,
            requested_at=next_window,
            window_started_at=next_window,
        )


async def test_role_override_expires_and_revocation_restore_trusted_role() -> None:
    identity = _identity()
    repository = MemoryAdministrationRepository(identity)
    service = _service(repository)
    principal = AdminPrincipal(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        display_name=identity.user_name,
        role=AdminRole.ADMIN,
        permissions=permissions_for_role(AdminRole.ADMIN),
        authentication_mode="development",
    )
    now = datetime.now(UTC)

    with pytest.raises(AdministrationValidationError, match="必须准确输入"):
        await service.set_user_role_override(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            role=AdminRole.VIEWER,
            override_expires_at=None,
            actor_id=identity.user_id,
            confirmation="确认覆盖",
        )
    with pytest.raises(AdministrationValidationError, match="必须包含时区"):
        await service.set_user_role_override(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            role=AdminRole.VIEWER,
            override_expires_at=datetime.now() + timedelta(hours=1),
            actor_id=identity.user_id,
            confirmation=f"确认覆盖用户角色 {identity.user_id}",
        )
    with pytest.raises(AdministrationValidationError, match="必须晚于当前时间"):
        await service.set_user_role_override(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            role=AdminRole.VIEWER,
            override_expires_at=now - timedelta(minutes=1),
            actor_id=identity.user_id,
            confirmation=f"确认覆盖用户角色 {identity.user_id}",
        )
    with pytest.raises(AdministrationValidationError, match="不能超过 365 天"):
        await service.set_user_role_override(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            role=AdminRole.VIEWER,
            override_expires_at=now + timedelta(days=366),
            actor_id=identity.user_id,
            confirmation=f"确认覆盖用户角色 {identity.user_id}",
        )
    overridden = await service.set_user_role_override(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        role=AdminRole.VIEWER,
        override_expires_at=now + timedelta(hours=1),
        actor_id=identity.user_id,
        confirmation=f"确认覆盖用户角色 {identity.user_id}",
    )
    governed = await repository.authorize_admin_request(
        principal=principal,
        requested_at=now,
        window_started_at=now.replace(second=0, microsecond=0),
    )
    restored = await repository.authorize_admin_request(
        principal=principal,
        requested_at=now + timedelta(hours=2),
        window_started_at=(now + timedelta(hours=2)).replace(second=0, microsecond=0),
    )

    assert overridden.source is IdentityGovernanceSource.MANUAL
    assert overridden.trusted_role is AdminRole.ADMIN
    assert governed.role is AdminRole.VIEWER
    assert restored.role is AdminRole.ADMIN

    await service.set_user_role_override(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        role=AdminRole.OPERATOR,
        override_expires_at=None,
        actor_id=identity.user_id,
        confirmation=f"确认覆盖用户角色 {identity.user_id}",
    )
    revoked = await service.revoke_user_role_override(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        actor_id=identity.user_id,
        confirmation=f"确认撤销用户角色覆盖 {identity.user_id}",
    )
    audit = await service.list_audit_records(
        tenant_id=identity.tenant_id,
        search=None,
        action=None,
        limit=20,
        cursor=None,
    )

    assert revoked.role is AdminRole.ADMIN
    assert revoked.source is IdentityGovernanceSource.DEVELOPMENT
    assert revoked.overridden_by is None
    assert {item.action for item in audit.items} >= {
        "user.role_overridden",
        "user.role_override_expired",
        "user.role_override_revoked",
    }

    with pytest.raises(AdministrationNotFoundError, match="用户不存在"):
        await service.set_user_role_override(
            tenant_id=uuid4(),
            user_id=identity.user_id,
            role=AdminRole.ADMIN,
            override_expires_at=None,
            actor_id=identity.user_id,
            confirmation=f"确认覆盖用户角色 {identity.user_id}",
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
