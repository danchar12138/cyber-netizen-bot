"""数据导出、可验证遗忘、保留期清理和备份演练应用用例。"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol, cast
from uuid import UUID

from cnb_application.configuration_service import ConfigurationService
from cnb_application.object_storage import ObjectStorage
from cnb_domain import (
    BACKUP_RESTORE_CONFIRMATION,
    USER_DATA_FORGET_CONFIRMATION_PREFIX,
    DevelopmentIdentity,
    JsonValue,
    LifecycleRun,
    LifecycleRunKind,
    LifecycleRunStatus,
    ObservabilityAlertDispositionEvent,
    ObservabilityAlertLifecycle,
    ObservabilityAlertRecommendationFeedback,
    ObservabilityAlertReplayReview,
)


class DataLifecycleValidationError(ValueError):
    """高风险命令未确认或参数超出安全边界。"""


class DataLifecycleNotFoundError(LookupError):
    """目标用户或保留期候选不属于当前租户。"""


class DataLifecycleOperationError(RuntimeError):
    """数据库已保护但仍有对象存储副作用需要重试。"""


@dataclass(frozen=True, slots=True)
class ExportSnapshot:
    """仓储按白名单收集、可安全序列化的用户数据快照。"""

    data: dict[str, JsonValue]
    record_count: int


@dataclass(frozen=True, slots=True)
class ForgetResult:
    """数据库遗忘结果和需要幂等删除的私有对象。"""

    object_keys: tuple[str, ...]
    counters: dict[str, int]


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    """达到删除保留期的会话及其对象依赖。"""

    conversation_id: UUID
    object_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentRetentionCandidate:
    """达到 Agent 保留截止时间且等待先清理私有对象的候选。"""

    agent_id: UUID
    object_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertHistorySnapshot:
    """受上限保护的当前 Agent 告警运营历史。"""

    lifecycles: tuple[ObservabilityAlertLifecycle, ...]
    disposition_events: tuple[ObservabilityAlertDispositionEvent, ...]
    replay_reviews: tuple[ObservabilityAlertReplayReview, ...]
    recommendation_feedback: tuple[ObservabilityAlertRecommendationFeedback, ...] = ()
    truncated: bool = False

    @property
    def record_count(self) -> int:
        return (
            len(self.lifecycles)
            + len(self.disposition_events)
            + len(self.replay_reviews)
            + len(self.recommendation_feedback)
        )


@dataclass(frozen=True, slots=True)
class ObservabilityHistoryRetentionResult:
    """单次租户隔离告警历史清理计数。"""

    disposition_events_purged: int
    replay_reviews_purged: int
    recommendation_feedback_purged: int = 0


@dataclass(frozen=True, slots=True)
class DataLifecyclePolicy:
    """从版本化运行配置解析出的生命周期边界。"""

    deleted_agent_days: int
    deleted_conversation_days: int
    deleted_attachment_days: int
    observability_disposition_event_days: int
    observability_replay_review_days: int
    observability_recommendation_feedback_days: int
    orphan_grace_hours: int
    batch_size: int
    export_max_records: int
    export_max_bytes: int
    backup_expected_interval_hours: int


@dataclass(frozen=True, slots=True)
class DataExportArtifact:
    """仅在当前请求内存在的下载内容及完整性信息。"""

    run: LifecycleRun
    filename: str
    content: bytes
    sha256: str


class DataLifecycleRepository(Protocol):
    """数据生命周期真相源；具体数据库实现留在基础设施层。"""

    async def start_run(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        subject_user_id: UUID | None,
        kind: LifecycleRunKind,
    ) -> LifecycleRun: ...

    async def finish_run(
        self,
        run_id: UUID,
        *,
        tenant_id: UUID,
        status: LifecycleRunStatus,
        counters: dict[str, int],
        evidence: dict[str, JsonValue],
        error_code: str | None = None,
    ) -> LifecycleRun: ...

    async def list_runs(self, *, tenant_id: UUID, limit: int) -> tuple[LifecycleRun, ...]: ...

    async def collect_user_export(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        max_records: int,
    ) -> ExportSnapshot: ...

    async def forget_user_data(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        actor_id: UUID,
    ) -> ForgetResult: ...

    async def list_retention_candidates(
        self,
        *,
        tenant_id: UUID,
        deleted_before: datetime,
        limit: int,
    ) -> tuple[RetentionCandidate, ...]: ...

    async def purge_conversation(
        self,
        conversation_id: UUID,
        *,
        tenant_id: UUID,
        deleted_before: datetime,
    ) -> bool: ...

    async def purge_expired_attachment_metadata(
        self,
        *,
        tenant_id: UUID,
        deleted_before: datetime,
        limit: int,
    ) -> int: ...

    async def list_agent_retention_candidates(
        self,
        *,
        tenant_id: UUID,
        purge_before: datetime,
        limit: int,
    ) -> tuple[AgentRetentionCandidate, ...]: ...

    async def purge_agent(
        self,
        agent_id: UUID,
        *,
        tenant_id: UUID,
        purge_before: datetime,
    ) -> bool: ...

    async def list_known_object_keys(self, *, tenant_id: UUID) -> frozenset[str]: ...


class ObservabilityHistoryRepository(Protocol):
    """告警历史导出与保留清理使用的最小仓储边界。"""

    async def collect_observability_alert_history(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        max_records: int,
    ) -> ObservabilityAlertHistorySnapshot: ...

    async def purge_observability_alert_history(
        self,
        *,
        tenant_id: UUID,
        disposition_events_before: datetime,
        replay_reviews_before: datetime,
        recommendation_feedback_before: datetime,
        limit: int,
    ) -> ObservabilityHistoryRetentionResult: ...


class DataLifecycleService:
    """执行有上限、可审计、默认不泄露内部数据的生命周期操作。"""

    _FORBIDDEN_EXPORT_KEYS = frozenset(
        {
            "secret",
            "secret_value",
            "plaintext",
            "token",
            "token_hash",
            "object_key",
            "system_prompt",
            "prompt",
            "hidden_reasoning",
            "chain_of_thought",
            "password",
            "credential",
            "authorization",
            "cookie",
        }
    )
    _FORBIDDEN_EXPORT_KEY_PARTS = frozenset(
        {"secret", "token", "prompt", "password", "credential", "authorization", "cookie"}
    )

    def __init__(
        self,
        repository: DataLifecycleRepository,
        object_storage: ObjectStorage,
        configuration_service: ConfigurationService,
        identity: DevelopmentIdentity,
        observability_repository: ObservabilityHistoryRepository,
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage
        self._configuration_service = configuration_service
        self._identity = identity
        self._observability_repository = observability_repository

    async def policy(self) -> DataLifecyclePolicy:
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=self._identity.tenant_id,
            agent_id=self._identity.agent_id,
            user_id=self._identity.user_id,
        )
        values = configuration.values
        return DataLifecyclePolicy(
            deleted_agent_days=self._integer(values, "data.retention.deleted_agent_days"),
            deleted_conversation_days=self._integer(
                values, "data.retention.deleted_conversation_days"
            ),
            deleted_attachment_days=self._integer(values, "data.retention.deleted_attachment_days"),
            observability_disposition_event_days=self._integer(
                values, "data.retention.observability_disposition_event_days"
            ),
            observability_replay_review_days=self._integer(
                values, "data.retention.observability_replay_review_days"
            ),
            observability_recommendation_feedback_days=self._integer(
                values, "data.retention.observability_recommendation_feedback_days"
            ),
            orphan_grace_hours=self._integer(values, "data.retention.orphan_grace_hours"),
            batch_size=self._integer(values, "data.retention.batch_size"),
            export_max_records=self._integer(values, "data.export.max_records"),
            export_max_bytes=self._integer(values, "data.export.max_bytes"),
            backup_expected_interval_hours=self._integer(
                values, "data.backup.expected_interval_hours"
            ),
        )

    async def list_runs(self, *, limit: int = 50) -> tuple[LifecycleRun, ...]:
        if not 1 <= limit <= 100:
            raise DataLifecycleValidationError("生命周期运行记录数量必须位于 1 到 100 之间")
        return await self._repository.list_runs(tenant_id=self._identity.tenant_id, limit=limit)

    async def export_user_data(self, user_id: UUID) -> DataExportArtifact:
        policy = await self.policy()
        run = await self._repository.start_run(
            tenant_id=self._identity.tenant_id,
            actor_id=self._identity.user_id,
            subject_user_id=user_id,
            kind=LifecycleRunKind.USER_EXPORT,
        )
        try:
            snapshot = await self._repository.collect_user_export(
                tenant_id=self._identity.tenant_id,
                user_id=user_id,
                max_records=policy.export_max_records,
            )
            self._assert_export_keys(snapshot.data)
            package: dict[str, JsonValue] = {
                "schema_version": "cnb-user-export-v1",
                "export_id": str(run.id),
                "generated_at": datetime.now(UTC).isoformat(),
                "tenant_id": str(self._identity.tenant_id),
                "subject_user_id": str(user_id),
                "data": snapshot.data,
            }
            content = json.dumps(
                package,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(content) > policy.export_max_bytes:
                raise DataLifecycleValidationError("导出内容超过已配置的安全大小上限")
            digest = sha256(content).hexdigest()
            completed = await self._repository.finish_run(
                run.id,
                tenant_id=self._identity.tenant_id,
                status=LifecycleRunStatus.SUCCEEDED,
                counters={"records": snapshot.record_count, "bytes": len(content)},
                evidence={"schema_version": "cnb-user-export-v1", "sha256": digest},
            )
            return DataExportArtifact(
                run=completed,
                filename=f"cyber-netizen-user-{user_id}.json",
                content=content,
                sha256=digest,
            )
        except (DataLifecycleNotFoundError, DataLifecycleValidationError):
            await self._fail_run(run.id, error_code="export_rejected")
            raise
        except Exception:
            await self._fail_run(run.id, error_code="export_failed")
            raise DataLifecycleOperationError("用户数据导出执行失败") from None

    async def export_observability_alert_history(
        self, *, window_minutes: int
    ) -> DataExportArtifact:
        """生成当前 Agent 的一次性安全告警运营历史下载。"""
        if not 5 <= window_minutes <= 525_600:
            raise DataLifecycleValidationError("告警历史导出窗口必须位于 5 到 525600 分钟之间")
        policy = await self.policy()
        run = await self._repository.start_run(
            tenant_id=self._identity.tenant_id,
            actor_id=self._identity.user_id,
            subject_user_id=None,
            kind=LifecycleRunKind.OBSERVABILITY_ALERT_HISTORY_EXPORT,
        )
        window_ended_at = datetime.now(UTC)
        window_started_at = window_ended_at - timedelta(minutes=window_minutes)
        try:
            snapshot = await self._observability_repository.collect_observability_alert_history(
                tenant_id=self._identity.tenant_id,
                agent_id=self._identity.agent_id,
                window_started_at=window_started_at,
                window_ended_at=window_ended_at,
                max_records=policy.export_max_records,
            )
            if snapshot.truncated or snapshot.record_count > policy.export_max_records:
                raise DataLifecycleValidationError("告警历史超过已配置的导出记录上限")
            data = self._observability_export_data(snapshot)
            self._assert_export_keys(data)
            package = cast(
                dict[str, JsonValue],
                {
                    "schema_version": "cnb-observability-alert-history-v2",
                    "export_id": str(run.id),
                    "generated_at": window_ended_at.isoformat(),
                    "tenant_id": str(self._identity.tenant_id),
                    "agent_id": str(self._identity.agent_id),
                    "window": {
                        "started_at": window_started_at.isoformat(),
                        "ended_at": window_ended_at.isoformat(),
                        "minutes": window_minutes,
                    },
                    "data": data,
                },
            )
            content = json.dumps(
                package,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(content) > policy.export_max_bytes:
                raise DataLifecycleValidationError("告警历史导出超过已配置的安全大小上限")
            digest = sha256(content).hexdigest()
            completed = await self._repository.finish_run(
                run.id,
                tenant_id=self._identity.tenant_id,
                status=LifecycleRunStatus.SUCCEEDED,
                counters={
                    "records": snapshot.record_count,
                    "bytes": len(content),
                    "alert_lifecycles": len(snapshot.lifecycles),
                    "disposition_events": len(snapshot.disposition_events),
                    "replay_reviews": len(snapshot.replay_reviews),
                    "recommendation_feedback": len(snapshot.recommendation_feedback),
                },
                evidence={
                    "schema_version": "cnb-observability-alert-history-v2",
                    "sha256": digest,
                    "window_started_at": window_started_at.isoformat(),
                    "window_ended_at": window_ended_at.isoformat(),
                },
            )
            return DataExportArtifact(
                run=completed,
                filename=f"cyber-netizen-alert-history-{self._identity.agent_id}.json",
                content=content,
                sha256=digest,
            )
        except DataLifecycleValidationError:
            await self._fail_run(run.id, error_code="observability_export_rejected")
            raise
        except Exception:
            await self._fail_run(run.id, error_code="observability_export_failed")
            raise DataLifecycleOperationError("告警运营历史导出执行失败") from None

    async def forget_user_data(self, user_id: UUID, *, confirmation: str) -> LifecycleRun:
        expected = f"{USER_DATA_FORGET_CONFIRMATION_PREFIX}{user_id}"
        if confirmation != expected:
            raise DataLifecycleValidationError(f"遗忘操作必须准确输入：{expected}")
        run = await self._repository.start_run(
            tenant_id=self._identity.tenant_id,
            actor_id=self._identity.user_id,
            subject_user_id=user_id,
            kind=LifecycleRunKind.USER_FORGET,
        )
        try:
            result = await self._repository.forget_user_data(
                tenant_id=self._identity.tenant_id,
                user_id=user_id,
                actor_id=self._identity.user_id,
            )
        except (DataLifecycleNotFoundError, DataLifecycleValidationError):
            await self._fail_run(run.id, error_code="forget_rejected")
            raise
        except Exception:
            await self._fail_run(run.id, error_code="forget_failed")
            raise DataLifecycleOperationError("用户数据遗忘执行失败") from None
        failed_objects = 0
        for object_key in result.object_keys:
            try:
                await self._object_storage.delete_object(object_key)
            except Exception:
                failed_objects += 1
        counters = {**result.counters, "objects_failed": failed_objects}
        if failed_objects:
            await self._repository.finish_run(
                run.id,
                tenant_id=self._identity.tenant_id,
                status=LifecycleRunStatus.FAILED,
                counters=counters,
                evidence={"database_redaction_completed": True},
                error_code="object_cleanup_pending",
            )
            raise DataLifecycleOperationError("数据库遗忘已完成，部分对象等待孤儿清理重试")
        return await self._repository.finish_run(
            run.id,
            tenant_id=self._identity.tenant_id,
            status=LifecycleRunStatus.SUCCEEDED,
            counters=counters,
            evidence={"database_redaction_completed": True, "objects_deleted": True},
        )

    async def run_retention_cleanup(self, *, confirmed: bool) -> LifecycleRun:
        if not confirmed:
            raise DataLifecycleValidationError("保留期清理必须显式确认")
        policy = await self.policy()
        run = await self._repository.start_run(
            tenant_id=self._identity.tenant_id,
            actor_id=self._identity.user_id,
            subject_user_id=None,
            kind=LifecycleRunKind.RETENTION_CLEANUP,
        )
        cleanup_started_at = datetime.now(UTC)
        deleted_before = cleanup_started_at - timedelta(days=policy.deleted_conversation_days)
        try:
            candidates = await self._repository.list_retention_candidates(
                tenant_id=self._identity.tenant_id,
                deleted_before=deleted_before,
                limit=policy.batch_size,
            )
            conversations_purged = 0
            objects_deleted = 0
            candidates_failed = 0
            for candidate in candidates:
                candidate_failed = False
                for object_key in candidate.object_keys:
                    try:
                        await self._object_storage.delete_object(object_key)
                        objects_deleted += 1
                    except Exception:
                        candidate_failed = True
                if candidate_failed:
                    candidates_failed += 1
                    continue
                if await self._repository.purge_conversation(
                    candidate.conversation_id,
                    tenant_id=self._identity.tenant_id,
                    deleted_before=deleted_before,
                ):
                    conversations_purged += 1
            attachment_cutoff = cleanup_started_at - timedelta(days=policy.deleted_attachment_days)
            attachment_metadata_purged = await self._repository.purge_expired_attachment_metadata(
                tenant_id=self._identity.tenant_id,
                deleted_before=attachment_cutoff,
                limit=policy.batch_size,
            )
            agent_purge_before = cleanup_started_at
            agent_candidates = await self._repository.list_agent_retention_candidates(
                tenant_id=self._identity.tenant_id,
                purge_before=agent_purge_before,
                limit=policy.batch_size,
            )
            agents_purged = 0
            agent_candidates_failed = 0
            for candidate in agent_candidates:
                candidate_failed = False
                for object_key in candidate.object_keys:
                    try:
                        await self._object_storage.delete_object(object_key)
                        objects_deleted += 1
                    except Exception:
                        candidate_failed = True
                if candidate_failed:
                    agent_candidates_failed += 1
                    continue
                if await self._repository.purge_agent(
                    candidate.agent_id,
                    tenant_id=self._identity.tenant_id,
                    purge_before=agent_purge_before,
                ):
                    agents_purged += 1
            disposition_event_cutoff = cleanup_started_at - timedelta(
                days=policy.observability_disposition_event_days
            )
            replay_review_cutoff = cleanup_started_at - timedelta(
                days=policy.observability_replay_review_days
            )
            recommendation_feedback_cutoff = cleanup_started_at - timedelta(
                days=policy.observability_recommendation_feedback_days
            )
            observability_history = (
                await self._observability_repository.purge_observability_alert_history(
                    tenant_id=self._identity.tenant_id,
                    disposition_events_before=disposition_event_cutoff,
                    replay_reviews_before=replay_review_cutoff,
                    recommendation_feedback_before=recommendation_feedback_cutoff,
                    limit=policy.batch_size,
                )
            )
            counters = {
                "candidates": len(candidates),
                "conversations_purged": conversations_purged,
                "attachment_metadata_purged": attachment_metadata_purged,
                "agent_candidates": len(agent_candidates),
                "agents_purged": agents_purged,
                "objects_deleted": objects_deleted,
                "candidates_failed": candidates_failed,
                "agent_candidates_failed": agent_candidates_failed,
                "observability_disposition_events_purged": (
                    observability_history.disposition_events_purged
                ),
                "observability_replay_reviews_purged": (
                    observability_history.replay_reviews_purged
                ),
                "observability_recommendation_feedback_purged": (
                    observability_history.recommendation_feedback_purged
                ),
            }
            return await self._repository.finish_run(
                run.id,
                tenant_id=self._identity.tenant_id,
                status=(
                    LifecycleRunStatus.FAILED
                    if candidates_failed or agent_candidates_failed
                    else LifecycleRunStatus.SUCCEEDED
                ),
                counters=counters,
                evidence={
                    "conversation_cutoff": deleted_before.isoformat(),
                    "attachment_cutoff": attachment_cutoff.isoformat(),
                    "agent_purge_before": agent_purge_before.isoformat(),
                    "observability_disposition_event_cutoff": (
                        disposition_event_cutoff.isoformat()
                    ),
                    "observability_replay_review_cutoff": replay_review_cutoff.isoformat(),
                    "observability_recommendation_feedback_cutoff": (
                        recommendation_feedback_cutoff.isoformat()
                    ),
                },
                error_code=(
                    "object_cleanup_failed"
                    if candidates_failed or agent_candidates_failed
                    else None
                ),
            )
        except (DataLifecycleNotFoundError, DataLifecycleValidationError):
            await self._fail_run(run.id, error_code="retention_rejected")
            raise
        except Exception:
            await self._fail_run(run.id, error_code="retention_failed")
            raise DataLifecycleOperationError("保留期清理执行失败") from None

    async def run_orphan_cleanup(self, *, confirmed: bool) -> LifecycleRun:
        if not confirmed:
            raise DataLifecycleValidationError("MinIO 孤儿清理必须显式确认")
        policy = await self.policy()
        run = await self._repository.start_run(
            tenant_id=self._identity.tenant_id,
            actor_id=self._identity.user_id,
            subject_user_id=None,
            kind=LifecycleRunKind.ORPHAN_CLEANUP,
        )
        try:
            known = await self._repository.list_known_object_keys(
                tenant_id=self._identity.tenant_id
            )
            objects = await self._object_storage.list_objects(
                prefix=f"tenants/{self._identity.tenant_id}/",
                limit=policy.batch_size,
            )
            cutoff = datetime.now(UTC) - timedelta(hours=policy.orphan_grace_hours)
            deleted = 0
            failed = 0
            for item in objects:
                if item.object_key in known or item.last_modified > cutoff:
                    continue
                try:
                    await self._object_storage.delete_object(item.object_key)
                    deleted += 1
                except Exception:
                    failed += 1
            return await self._repository.finish_run(
                run.id,
                tenant_id=self._identity.tenant_id,
                status=LifecycleRunStatus.FAILED if failed else LifecycleRunStatus.SUCCEEDED,
                counters={
                    "objects_scanned": len(objects),
                    "objects_deleted": deleted,
                    "objects_failed": failed,
                },
                evidence={"grace_cutoff": cutoff.isoformat()},
                error_code="object_cleanup_failed" if failed else None,
            )
        except (DataLifecycleNotFoundError, DataLifecycleValidationError):
            await self._fail_run(run.id, error_code="orphan_cleanup_rejected")
            raise
        except Exception:
            await self._fail_run(run.id, error_code="orphan_cleanup_failed")
            raise DataLifecycleOperationError("MinIO 孤儿清理执行失败") from None

    async def record_backup_restore_drill(
        self,
        *,
        manifest_sha256: str,
        database_rows_verified: int,
        objects_verified: int,
        database_integrity_verified: bool,
        object_integrity_verified: bool,
        application_smoke_verified: bool,
        confirmation: str,
    ) -> LifecycleRun:
        if confirmation != BACKUP_RESTORE_CONFIRMATION:
            raise DataLifecycleValidationError(
                f"备份恢复演练必须准确输入：{BACKUP_RESTORE_CONFIRMATION}"
            )
        digest = manifest_sha256.casefold()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise DataLifecycleValidationError("备份清单 SHA-256 必须是 64 位十六进制")
        if database_rows_verified < 0 or objects_verified < 0:
            raise DataLifecycleValidationError("校验计数不能为负数")
        if not all(
            (
                database_integrity_verified,
                object_integrity_verified,
                application_smoke_verified,
            )
        ):
            raise DataLifecycleValidationError("只有数据库、对象和应用冒烟均通过才能登记演练")
        run = await self._repository.start_run(
            tenant_id=self._identity.tenant_id,
            actor_id=self._identity.user_id,
            subject_user_id=None,
            kind=LifecycleRunKind.BACKUP_RESTORE_DRILL,
        )
        return await self._repository.finish_run(
            run.id,
            tenant_id=self._identity.tenant_id,
            status=LifecycleRunStatus.SUCCEEDED,
            counters={
                "database_rows_verified": database_rows_verified,
                "objects_verified": objects_verified,
            },
            evidence={
                "manifest_sha256": digest,
                "database_integrity_verified": True,
                "object_integrity_verified": True,
                "application_smoke_verified": True,
                "isolated_restore_required": True,
            },
        )

    @classmethod
    def _assert_export_keys(cls, value: JsonValue, *, path: str = "data") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = key.casefold()
                key_parts = frozenset(filter(None, re.split(r"[^a-z0-9]+", normalized)))
                if (
                    normalized in cls._FORBIDDEN_EXPORT_KEYS
                    or key_parts.intersection(cls._FORBIDDEN_EXPORT_KEY_PARTS)
                    or normalized.endswith("object_key")
                ):
                    raise DataLifecycleValidationError(f"导出白名单拒绝内部字段：{path}.{key}")
                cls._assert_export_keys(nested, path=f"{path}.{key}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                cls._assert_export_keys(nested, path=f"{path}[{index}]")

    @staticmethod
    def _observability_export_data(
        snapshot: ObservabilityAlertHistorySnapshot,
    ) -> dict[str, JsonValue]:
        """只序列化审定字段；处置自由文本和任务载荷不进入导出。"""
        return cast(
            dict[str, JsonValue],
            {
                "alert_lifecycles": [
                    {
                        "id": str(item.id),
                        "source_type": item.source_type,
                        "source_key": item.source_key,
                        "code": item.code,
                        "status": item.status.value,
                        "severity": item.severity.value,
                        "occurrences": item.occurrences,
                        "current_value": item.current_value,
                        "threshold_value": item.threshold_value,
                        "unit": item.unit,
                        "first_occurred_at": item.first_occurred_at.isoformat(),
                        "last_occurred_at": item.last_occurred_at.isoformat(),
                        "last_evaluated_at": item.last_evaluated_at.isoformat(),
                        "resolved_at": (
                            item.resolved_at.isoformat() if item.resolved_at is not None else None
                        ),
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in snapshot.lifecycles
                ],
                "disposition_events": [
                    {
                        "id": str(item.id),
                        "lifecycle_id": str(item.lifecycle_id),
                        "source_type": item.source_type,
                        "source_key": item.source_key,
                        "code": item.code,
                        "action": item.action.value,
                        "actor_id": str(item.actor_id),
                        "expires_at": (
                            item.expires_at.isoformat() if item.expires_at is not None else None
                        ),
                        "occurred_at": item.occurred_at.isoformat(),
                    }
                    for item in snapshot.disposition_events
                ],
                "replay_reviews": [
                    {
                        "id": str(item.id),
                        "source_job_id": (
                            str(item.source_job_id) if item.source_job_id is not None else None
                        ),
                        "source_type": item.source_type,
                        "source_key": item.source_key,
                        "decision": item.decision.value,
                        "reason_code": item.reason_code.value,
                        "actor_id": str(item.actor_id),
                        "suppression_expires_at": (
                            item.suppression_expires_at.isoformat()
                            if item.suppression_expires_at is not None
                            else None
                        ),
                        "reviewed_at": item.reviewed_at.isoformat(),
                    }
                    for item in snapshot.replay_reviews
                ],
                "recommendation_feedback": [
                    {
                        "id": str(item.id),
                        "lifecycle_id": str(item.lifecycle_id),
                        "source_type": item.source_type,
                        "source_key": item.source_key,
                        "code": item.code,
                        "recommendation_action": item.recommendation_action.value,
                        "priority": item.priority.value,
                        "reason_codes": [reason.value for reason in item.reason_codes],
                        "decision": item.decision.value,
                        "actor_id": str(item.actor_id),
                        "feedback_at": item.feedback_at.isoformat(),
                    }
                    for item in snapshot.recommendation_feedback
                ],
            },
        )

    async def _fail_run(
        self,
        run_id: UUID,
        *,
        error_code: str,
        counters: dict[str, int] | None = None,
        evidence: dict[str, JsonValue] | None = None,
    ) -> None:
        """仅用固定错误码结束失败运行，避免持久化底层异常正文。"""
        await self._repository.finish_run(
            run_id,
            tenant_id=self._identity.tenant_id,
            status=LifecycleRunStatus.FAILED,
            counters=counters or {},
            evidence=evidence or {},
            error_code=error_code,
        )

    @staticmethod
    def _integer(values: Mapping[str, JsonValue], key: str) -> int:
        value = values.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise DataLifecycleValidationError(f"生效生命周期配置类型无效：{key}")
        return value


__all__ = [
    "AgentRetentionCandidate",
    "DataExportArtifact",
    "DataLifecycleNotFoundError",
    "DataLifecycleOperationError",
    "DataLifecyclePolicy",
    "DataLifecycleRepository",
    "DataLifecycleService",
    "DataLifecycleValidationError",
    "ExportSnapshot",
    "ForgetResult",
    "ObservabilityAlertHistorySnapshot",
    "ObservabilityHistoryRepository",
    "ObservabilityHistoryRetentionResult",
    "RetentionCandidate",
]
