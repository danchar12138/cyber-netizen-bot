"""数据导出、遗忘、保留期、孤儿清理和恢复演练测试。"""

import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import pytest

from cnb_application import (
    AgentRetentionCandidate,
    ConfigurationService,
    DataLifecycleOperationError,
    DataLifecycleService,
    DataLifecycleValidationError,
    ObservabilityAlertHistorySnapshot,
    RetentionCandidate,
    StoredObjectEntry,
    build_default_registry,
)
from cnb_domain import (
    ActiveAlert,
    AlertSeverity,
    DevelopmentIdentity,
    JsonValue,
    LifecycleRunStatus,
    ObservabilityAlertDisposition,
    ObservabilityAlertDispositionStatus,
    ObservabilityAlertRecommendationAction,
    ObservabilityAlertRecommendationFeedback,
    ObservabilityAlertRecommendationFeedbackDecision,
    ObservabilityAlertRecommendationPriority,
    ObservabilityAlertRecommendationReason,
    ObservabilityAlertReplayDecision,
    ObservabilityAlertReplayReason,
    ObservabilityAlertReplayReview,
)
from cnb_infrastructure import (
    MemoryConfigurationRepository,
    MemoryDataLifecycleRepository,
    MemoryObjectStorage,
    MemoryObservabilityRepository,
)


def _identity() -> DevelopmentIdentity:
    return DevelopmentIdentity(
        tenant_id=uuid5(NAMESPACE_DNS, "data-lifecycle.tenant"),
        user_id=uuid5(NAMESPACE_DNS, "data-lifecycle.user"),
        agent_id=uuid5(NAMESPACE_DNS, "data-lifecycle.agent"),
        user_name="生命周期测试用户",
        agent_name="生命周期测试 Agent",
    )


def _services(
    storage: MemoryObjectStorage | None = None,
    observability: MemoryObservabilityRepository | None = None,
) -> tuple[
    DataLifecycleService,
    MemoryDataLifecycleRepository,
    MemoryObjectStorage,
    MemoryObservabilityRepository,
]:
    identity = _identity()
    repository = MemoryDataLifecycleRepository(identity)
    resolved_storage = storage or MemoryObjectStorage()
    observability_repository = observability or MemoryObservabilityRepository()
    service = DataLifecycleService(
        repository,
        resolved_storage,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
        identity,
        observability_repository,
    )
    return service, repository, resolved_storage, observability_repository


def _all_keys(value: JsonValue) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(value)
        for item in value.values():
            keys.update(_all_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_all_keys(item))
    return keys


async def _seed_alert_history(
    repository: MemoryObservabilityRepository,
    *,
    occurred_at: datetime,
    source_key: str,
) -> None:
    identity = _identity()
    reconciliation = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        alerts=(
            ActiveAlert(
                code="api_error_rate",
                severity=AlertSeverity.WARNING,
                title="测试告警",
                summary="不进入导出",
                current_value=5.0,
                threshold_value=2.0,
                unit="percent",
                source_type="api",
                source_key=source_key,
            ),
        ),
        observed_at=occurred_at,
    )
    lifecycle = reconciliation.activated_lifecycles[0]
    await repository.save_observability_alert_disposition(
        ObservabilityAlertDisposition(
            id=uuid4(),
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            source_type="api",
            source_key=source_key,
            code="api_error_rate",
            status=ObservabilityAlertDispositionStatus.ACKNOWLEDGED,
            reason="不应进入安全导出的自由文本备注",
            actor_id=identity.user_id,
            expires_at=None,
            created_at=occurred_at,
            updated_at=occurred_at,
        ),
        lifecycle_id=lifecycle.id,
    )
    await repository.record_observability_alert_replay_review(
        ObservabilityAlertReplayReview(
            id=uuid4(),
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            source_job_id=None,
            source_type="api",
            source_key=source_key,
            decision=ObservabilityAlertReplayDecision.BLOCKED,
            reason_code=ObservabilityAlertReplayReason.BLOCKED_ACTIVE_SUPPRESSION,
            actor_id=identity.user_id,
            suppression_expires_at=None,
            reviewed_at=occurred_at,
        )
    )
    await repository.save_observability_alert_recommendation_feedback(
        ObservabilityAlertRecommendationFeedback(
            id=uuid4(),
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            lifecycle_id=lifecycle.id,
            source_type="api",
            source_key=source_key,
            code="api_error_rate",
            recommendation_action=ObservabilityAlertRecommendationAction.ACKNOWLEDGE,
            priority=ObservabilityAlertRecommendationPriority.HIGH,
            reason_codes=(ObservabilityAlertRecommendationReason.LONG_RUNNING,),
            decision=ObservabilityAlertRecommendationFeedbackDecision.ACCEPTED,
            actor_id=identity.user_id,
            feedback_at=occurred_at,
        )
    )


async def test_lifecycle_policy_exposes_agent_retention_setting() -> None:
    service, _, _, _ = _services()

    policy = await service.policy()

    assert policy.deleted_agent_days == 30
    assert policy.observability_disposition_event_days == 90
    assert policy.observability_replay_review_days == 90
    assert policy.observability_recommendation_feedback_days == 90


async def test_user_export_is_bounded_and_excludes_internal_fields_and_object_keys() -> None:
    service, repository, _, _ = _services()
    identity = _identity()
    private_object_key = f"tenants/{identity.tenant_id}/attachments/private.txt"
    repository.seed_user_export(
        identity.user_id,
        data=cast(
            dict[str, JsonValue],
            {
                "profile": {"id": str(identity.user_id), "display_name": "测试用户"},
                "messages": [{"content": "可导出的本人内容"}],
            },
        ),
        record_count=2,
        object_keys=(private_object_key,),
    )

    artifact = await service.export_user_data(identity.user_id)
    payload = cast(dict[str, JsonValue], json.loads(artifact.content))

    assert artifact.run.status is LifecycleRunStatus.SUCCEEDED
    assert artifact.run.evidence["sha256"] == artifact.sha256
    assert private_object_key not in artifact.content.decode()
    assert _all_keys(payload).isdisjoint(
        {"secret", "token", "object_key", "prompt", "system_prompt", "hidden_reasoning"}
    )


async def test_export_rejects_a_nested_forbidden_field_and_records_only_fixed_error() -> None:
    service, repository, _, _ = _services()
    identity = _identity()
    repository.seed_user_export(
        identity.user_id,
        data=cast(dict[str, JsonValue], {"profile": {"access_token": "must-not-leak"}}),
        record_count=1,
    )

    with pytest.raises(DataLifecycleValidationError, match="拒绝内部字段"):
        await service.export_user_data(identity.user_id)

    run = (await service.list_runs())[0]
    assert run.status is LifecycleRunStatus.FAILED
    assert run.error_code == "export_rejected"
    assert "must-not-leak" not in json.dumps(run.evidence)


async def test_observability_history_export_is_scoped_bounded_and_excludes_free_text() -> None:
    service, _, _, observability = _services()
    now = datetime.now(UTC) - timedelta(minutes=1)
    await _seed_alert_history(observability, occurred_at=now, source_key="GET:/api/v1/tasks")
    await observability.record_observability_alert_replay_review(
        ObservabilityAlertReplayReview(
            id=uuid4(),
            tenant_id=_identity().tenant_id,
            agent_id=uuid4(),
            source_job_id=None,
            source_type="api",
            source_key="other-agent",
            decision=ObservabilityAlertReplayDecision.ALLOWED,
            reason_code=ObservabilityAlertReplayReason.ALLOWED_NO_SUPPRESSION,
            actor_id=_identity().user_id,
            suppression_expires_at=None,
            reviewed_at=now,
        )
    )

    artifact = await service.export_observability_alert_history(window_minutes=1_440)
    payload = cast(dict[str, JsonValue], json.loads(artifact.content))
    data = cast(dict[str, JsonValue], payload["data"])

    assert payload["schema_version"] == "cnb-observability-alert-history-v2"
    assert payload["agent_id"] == str(_identity().agent_id)
    assert artifact.run.counters["records"] == 4
    assert len(cast(list[JsonValue], data["alert_lifecycles"])) == 1
    assert len(cast(list[JsonValue], data["disposition_events"])) == 1
    assert len(cast(list[JsonValue], data["replay_reviews"])) == 1
    assert len(cast(list[JsonValue], data["recommendation_feedback"])) == 1
    assert "不应进入安全导出的自由文本备注" not in artifact.content.decode()
    assert _all_keys(payload).isdisjoint(
        {"payload", "target", "secret", "token", "prompt", "hidden_reasoning", "reason"}
    )

    with pytest.raises(DataLifecycleValidationError, match="导出窗口"):
        await service.export_observability_alert_history(window_minutes=4)


class _TruncatedObservabilityRepository(MemoryObservabilityRepository):
    async def collect_observability_alert_history(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        max_records: int,
    ) -> ObservabilityAlertHistorySnapshot:
        del tenant_id, agent_id, window_started_at, window_ended_at, max_records
        return ObservabilityAlertHistorySnapshot((), (), (), (), truncated=True)


async def test_observability_history_export_rejects_record_overflow_with_fixed_error() -> None:
    service, _, _, _ = _services(observability=_TruncatedObservabilityRepository())

    with pytest.raises(DataLifecycleValidationError, match="导出记录上限"):
        await service.export_observability_alert_history(window_minutes=1_440)

    run = (await service.list_runs())[0]
    assert run.status is LifecycleRunStatus.FAILED
    assert run.error_code == "observability_export_rejected"
    assert run.evidence == {}


async def test_forget_requires_exact_phrase_and_deletes_private_objects() -> None:
    service, repository, storage, _ = _services()
    identity = _identity()
    object_key = f"tenants/{identity.tenant_id}/attachments/forgotten.txt"
    repository.seed_user_export(
        identity.user_id,
        data=cast(dict[str, JsonValue], {"profile": {"display_name": "遗忘前姓名"}}),
        record_count=1,
        object_keys=(object_key,),
    )
    storage.put_for_test(object_key=object_key, content=b"private", content_type="text/plain")

    with pytest.raises(DataLifecycleValidationError, match="必须准确输入"):
        await service.forget_user_data(identity.user_id, confirmation="错误确认短语")

    run = await service.forget_user_data(
        identity.user_id,
        confirmation=f"确认永久遗忘 {identity.user_id}",
    )
    remaining = await storage.list_objects(prefix=f"tenants/{identity.tenant_id}/", limit=100)
    exported = await service.export_user_data(identity.user_id)

    assert run.status is LifecycleRunStatus.SUCCEEDED
    assert run.counters["users_redacted"] == 1
    assert remaining == ()
    assert "遗忘前姓名" not in exported.content.decode()


async def test_retention_cleanup_deletes_objects_before_purging_conversation() -> None:
    service, repository, storage, _ = _services()
    identity = _identity()
    conversation_id = uuid5(NAMESPACE_DNS, "data-lifecycle.expired-conversation")
    object_key = f"tenants/{identity.tenant_id}/attachments/expired.txt"
    repository.seed_retention_candidate(
        RetentionCandidate(conversation_id=conversation_id, object_keys=(object_key,))
    )
    storage.put_for_test(
        object_key=object_key,
        content=b"expired",
        content_type="text/plain",
        created_at=datetime.now(UTC) - timedelta(days=40),
    )

    run = await service.run_retention_cleanup(confirmed=True)
    repeated = await service.run_retention_cleanup(confirmed=True)

    assert run.status is LifecycleRunStatus.SUCCEEDED
    assert run.counters["objects_deleted"] == 1
    assert run.counters["conversations_purged"] == 1
    assert run.counters["agents_purged"] == 0
    assert repeated.counters["candidates"] == 0
    assert run.counters["observability_disposition_events_purged"] == 0
    assert run.counters["observability_replay_reviews_purged"] == 0
    assert run.counters["observability_recommendation_feedback_purged"] == 0


async def test_retention_cleanup_purges_only_expired_observability_history() -> None:
    service, _, _, observability = _services()
    old = datetime.now(UTC) - timedelta(days=91)
    fresh = datetime.now(UTC) - timedelta(days=1)
    await _seed_alert_history(observability, occurred_at=old, source_key="old-alert")
    await _seed_alert_history(observability, occurred_at=fresh, source_key="fresh-alert")
    foreign_tenant_id, foreign_agent_id = uuid4(), uuid4()
    await observability.record_observability_alert_replay_review(
        ObservabilityAlertReplayReview(
            id=uuid4(),
            tenant_id=foreign_tenant_id,
            agent_id=foreign_agent_id,
            source_job_id=None,
            source_type="api",
            source_key="foreign-alert",
            decision=ObservabilityAlertReplayDecision.ALLOWED,
            reason_code=ObservabilityAlertReplayReason.ALLOWED_NO_SUPPRESSION,
            actor_id=uuid4(),
            suppression_expires_at=None,
            reviewed_at=old,
        )
    )

    run = await service.run_retention_cleanup(confirmed=True)
    identity = _identity()
    events = await observability.list_observability_alert_disposition_events(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        limit=100,
    )
    reviews = await observability.list_observability_alert_replay_reviews(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        limit=100,
    )
    feedback = await observability.list_observability_alert_recommendation_feedback(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
    )
    foreign_reviews = await observability.list_observability_alert_replay_reviews(
        tenant_id=foreign_tenant_id,
        agent_id=foreign_agent_id,
        limit=100,
    )

    assert run.counters["observability_disposition_events_purged"] == 1
    assert run.counters["observability_replay_reviews_purged"] == 1
    assert run.counters["observability_recommendation_feedback_purged"] == 1
    assert [item.source_key for item in events] == ["fresh-alert"]
    assert [item.source_key for item in reviews] == ["fresh-alert"]
    assert [item.source_key for item in feedback] == ["fresh-alert"]
    assert [item.source_key for item in foreign_reviews] == ["foreign-alert"]


async def test_retention_cleanup_deletes_agent_objects_before_physical_purge() -> None:
    service, repository, storage, _ = _services()
    identity = _identity()
    object_key = f"tenants/{identity.tenant_id}/attachments/deleted-agent.txt"
    repository.seed_agent_retention_candidate(
        AgentRetentionCandidate(agent_id=identity.agent_id, object_keys=(object_key,))
    )
    storage.put_for_test(
        object_key=object_key,
        content=b"deleted agent",
        content_type="text/plain",
        created_at=datetime.now(UTC) - timedelta(days=40),
    )

    run = await service.run_retention_cleanup(confirmed=True)
    repeated = await service.run_retention_cleanup(confirmed=True)

    assert run.status is LifecycleRunStatus.SUCCEEDED
    assert run.counters["agent_candidates"] == 1
    assert run.counters["agents_purged"] == 1
    assert run.counters["objects_deleted"] == 1
    assert repeated.counters["agent_candidates"] == 0


async def test_orphan_cleanup_protects_referenced_and_recent_objects() -> None:
    service, repository, storage, _ = _services()
    identity = _identity()
    prefix = f"tenants/{identity.tenant_id}/"
    known = f"{prefix}attachments/known.txt"
    old_orphan = f"{prefix}attachments/old-orphan.txt"
    recent_orphan = f"{prefix}attachments/recent-orphan.txt"
    old = datetime.now(UTC) - timedelta(hours=25)
    storage.put_for_test(
        object_key=known, content=b"known", content_type="text/plain", created_at=old
    )
    storage.put_for_test(
        object_key=old_orphan,
        content=b"old orphan",
        content_type="text/plain",
        created_at=old,
    )
    storage.put_for_test(
        object_key=recent_orphan,
        content=b"recent orphan",
        content_type="text/plain",
    )
    repository.set_known_object_keys({known})

    run = await service.run_orphan_cleanup(confirmed=True)
    remaining = {item.object_key for item in await storage.list_objects(prefix=prefix, limit=100)}

    assert run.status is LifecycleRunStatus.SUCCEEDED
    assert run.counters == {"objects_scanned": 3, "objects_deleted": 1, "objects_failed": 0}
    assert remaining == {known, recent_orphan}


async def test_backup_restore_drill_requires_all_three_verifications() -> None:
    service, _, _, _ = _services()

    with pytest.raises(DataLifecycleValidationError, match="必须准确输入"):
        await service.record_backup_restore_drill(
            manifest_sha256="a" * 64,
            database_rows_verified=120,
            objects_verified=8,
            database_integrity_verified=True,
            object_integrity_verified=True,
            application_smoke_verified=True,
            confirmation="BACKUP RESTORE VERIFIED",
        )

    with pytest.raises(DataLifecycleValidationError, match="均通过"):
        await service.record_backup_restore_drill(
            manifest_sha256="a" * 64,
            database_rows_verified=120,
            objects_verified=8,
            database_integrity_verified=True,
            object_integrity_verified=True,
            application_smoke_verified=False,
            confirmation="确认备份恢复演练已验证",
        )

    run = await service.record_backup_restore_drill(
        manifest_sha256="A" * 64,
        database_rows_verified=120,
        objects_verified=8,
        database_integrity_verified=True,
        object_integrity_verified=True,
        application_smoke_verified=True,
        confirmation="确认备份恢复演练已验证",
    )

    assert run.status is LifecycleRunStatus.SUCCEEDED
    assert run.evidence["manifest_sha256"] == "a" * 64
    assert run.evidence["isolated_restore_required"] is True


class _FailingListingStorage(MemoryObjectStorage):
    async def list_objects(self, *, prefix: str, limit: int) -> tuple[StoredObjectEntry, ...]:
        del prefix, limit
        raise RuntimeError("tenants/secret/object-key")


async def test_unexpected_storage_error_is_replaced_with_safe_operation_error() -> None:
    service, _, _, _ = _services(_FailingListingStorage())

    with pytest.raises(DataLifecycleOperationError, match="MinIO 孤儿清理执行失败") as captured:
        await service.run_orphan_cleanup(confirmed=True)

    run = (await service.list_runs())[0]
    assert captured.value.__suppress_context__ is True
    assert "object-key" not in str(captured.value)
    assert run.status is LifecycleRunStatus.FAILED
    assert run.error_code == "orphan_cleanup_failed"
    assert run.evidence == {}
