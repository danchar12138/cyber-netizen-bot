"""数据导出、可验证遗忘、保留期清理和备份演练应用用例。"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from cnb_application.attachment_service import ObjectStorage
from cnb_application.configuration_service import ConfigurationService
from cnb_domain import (
    BACKUP_RESTORE_CONFIRMATION,
    USER_DATA_FORGET_CONFIRMATION_PREFIX,
    DevelopmentIdentity,
    JsonValue,
    LifecycleRun,
    LifecycleRunKind,
    LifecycleRunStatus,
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
class DataLifecyclePolicy:
    """从版本化运行配置解析出的生命周期边界。"""

    deleted_conversation_days: int
    deleted_attachment_days: int
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

    async def list_known_object_keys(self, *, tenant_id: UUID) -> frozenset[str]: ...


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
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage
        self._configuration_service = configuration_service
        self._identity = identity

    async def policy(self) -> DataLifecyclePolicy:
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=self._identity.tenant_id,
            agent_id=self._identity.agent_id,
            user_id=self._identity.user_id,
        )
        values = configuration.values
        return DataLifecyclePolicy(
            deleted_conversation_days=self._integer(
                values, "data.retention.deleted_conversation_days"
            ),
            deleted_attachment_days=self._integer(values, "data.retention.deleted_attachment_days"),
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
        deleted_before = datetime.now(UTC) - timedelta(days=policy.deleted_conversation_days)
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
            attachment_cutoff = datetime.now(UTC) - timedelta(days=policy.deleted_attachment_days)
            attachment_metadata_purged = await self._repository.purge_expired_attachment_metadata(
                tenant_id=self._identity.tenant_id,
                deleted_before=attachment_cutoff,
                limit=policy.batch_size,
            )
            counters = {
                "candidates": len(candidates),
                "conversations_purged": conversations_purged,
                "attachment_metadata_purged": attachment_metadata_purged,
                "objects_deleted": objects_deleted,
                "candidates_failed": candidates_failed,
            }
            return await self._repository.finish_run(
                run.id,
                tenant_id=self._identity.tenant_id,
                status=(
                    LifecycleRunStatus.FAILED if candidates_failed else LifecycleRunStatus.SUCCEEDED
                ),
                counters=counters,
                evidence={"cutoff": deleted_before.isoformat()},
                error_code="object_cleanup_failed" if candidates_failed else None,
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
    "DataExportArtifact",
    "DataLifecycleNotFoundError",
    "DataLifecycleOperationError",
    "DataLifecyclePolicy",
    "DataLifecycleRepository",
    "DataLifecycleService",
    "DataLifecycleValidationError",
    "ExportSnapshot",
    "ForgetResult",
    "RetentionCandidate",
]
