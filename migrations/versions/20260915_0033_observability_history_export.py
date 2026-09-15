"""增加告警运营历史导出运行类型。

Revision ID: 20260915_0033
Revises: 20260915_0032
创建日期：2026-09-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260915_0033"
down_revision: str | Sequence[str] | None = "20260915_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """允许告警历史导出并为租户保留清理增加时间索引。"""
    op.drop_constraint(
        "ck_data_lifecycle_runs_kind",
        "data_lifecycle_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_data_lifecycle_runs_kind",
        "data_lifecycle_runs",
        "kind IN ('user_export', 'observability_alert_history_export', 'user_forget', "
        "'retention_cleanup', 'orphan_cleanup', 'backup_restore_drill')",
    )
    op.create_index(
        "ix_observability_alert_disposition_events_tenant_time",
        "observability_alert_disposition_events",
        ["tenant_id", "occurred_at"],
    )
    op.create_index(
        "ix_observability_alert_replay_reviews_tenant_time",
        "observability_alert_replay_reviews",
        ["tenant_id", "reviewed_at"],
    )


def downgrade() -> None:
    """恢复原运行类型约束；降级前需先处理新增类型记录。"""
    op.drop_index(
        "ix_observability_alert_replay_reviews_tenant_time",
        table_name="observability_alert_replay_reviews",
    )
    op.drop_index(
        "ix_observability_alert_disposition_events_tenant_time",
        table_name="observability_alert_disposition_events",
    )
    op.drop_constraint(
        "ck_data_lifecycle_runs_kind",
        "data_lifecycle_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_data_lifecycle_runs_kind",
        "data_lifecycle_runs",
        "kind IN ('user_export', 'user_forget', 'retention_cleanup', "
        "'orphan_cleanup', 'backup_restore_drill')",
    )
