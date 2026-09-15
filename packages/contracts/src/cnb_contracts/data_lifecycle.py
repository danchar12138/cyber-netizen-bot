"""数据生命周期管理、运行证据与高风险确认契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from cnb_domain import (
    BACKUP_RESTORE_CONFIRMATION,
    USER_DATA_FORGET_CONFIRMATION_PREFIX,
    JsonValue,
    LifecycleRunKind,
    LifecycleRunStatus,
)


class LifecyclePolicyResponse(BaseModel):
    """管理后台可查看、配置中心可修改的最终生效策略。"""

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


class LifecycleRunResponse(BaseModel):
    """不包含正文、Prompt、密钥、令牌或对象路径的运行证据。"""

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


class DataLifecycleOverviewResponse(BaseModel):
    """生命周期策略和最近运行的一屏摘要。"""

    policy: LifecyclePolicyResponse
    runs: tuple[LifecycleRunResponse, ...]


class UserDataExportCommand(BaseModel):
    """按租户隔离导出一个用户的白名单数据。"""

    user_id: UUID


class ObservabilityAlertHistoryExportCommand(BaseModel):
    """按受限时间窗口导出当前 Agent 的安全告警运营历史。"""

    window_minutes: int = Field(ge=5, le=525_600)


class UserDataForgetCommand(BaseModel):
    """要求输入包含目标 UUID 的不可逆确认短语。"""

    user_id: UUID
    confirmation: str = Field(
        min_length=len(USER_DATA_FORGET_CONFIRMATION_PREFIX) + 36,
        max_length=len(USER_DATA_FORGET_CONFIRMATION_PREFIX) + 36,
    )


class ConfirmedLifecycleCommand(BaseModel):
    """保留期与孤儿清理的显式确认。"""

    confirmed: bool


class BackupRestoreDrillCommand(BaseModel):
    """只登记在隔离环境中实际完成的备份恢复验证证据。"""

    manifest_sha256: str = Field(min_length=64, max_length=64)
    database_rows_verified: int = Field(ge=0)
    objects_verified: int = Field(ge=0)
    database_integrity_verified: bool
    object_integrity_verified: bool
    application_smoke_verified: bool
    confirmation: str = Field(
        min_length=len(BACKUP_RESTORE_CONFIRMATION),
        max_length=len(BACKUP_RESTORE_CONFIRMATION),
    )


__all__ = [
    "BackupRestoreDrillCommand",
    "ConfirmedLifecycleCommand",
    "DataLifecycleOverviewResponse",
    "LifecyclePolicyResponse",
    "LifecycleRunResponse",
    "ObservabilityAlertHistoryExportCommand",
    "UserDataExportCommand",
    "UserDataForgetCommand",
]
