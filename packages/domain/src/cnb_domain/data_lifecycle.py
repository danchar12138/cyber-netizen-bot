"""数据导出、遗忘、保留期和备份演练的纯领域记录。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.configuration import JsonValue


class LifecycleRunKind(StrEnum):
    """管理后台可触发或登记的数据生命周期动作。"""

    USER_EXPORT = "user_export"
    USER_FORGET = "user_forget"
    RETENTION_CLEANUP = "retention_cleanup"
    ORPHAN_CLEANUP = "orphan_cleanup"
    BACKUP_RESTORE_DRILL = "backup_restore_drill"


class LifecycleRunStatus(StrEnum):
    """生命周期动作的最终状态；运行中记录可帮助发现进程中断。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LifecycleRun:
    """不包含用户正文、密钥、Prompt 或对象键的安全运行证据。"""

    id: UUID
    tenant_id: UUID
    actor_id: UUID | None
    subject_user_id: UUID | None
    kind: LifecycleRunKind
    status: LifecycleRunStatus
    counters: dict[str, int]
    evidence: dict[str, JsonValue]
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None


__all__ = ["LifecycleRun", "LifecycleRunKind", "LifecycleRunStatus"]
