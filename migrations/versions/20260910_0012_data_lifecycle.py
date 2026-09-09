"""增加数据生命周期运行证据。

Revision ID: 20260910_0012
Revises: 20260910_0011
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0012"
down_revision: str | Sequence[str] | None = "20260910_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建不含正文、Prompt、密钥和对象键的生命周期运行记录。"""
    op.create_table(
        "data_lifecycle_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("counters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('user_export', 'user_forget', 'retention_cleanup', "
            "'orphan_cleanup', 'backup_restore_drill')",
            name="ck_data_lifecycle_runs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_data_lifecycle_runs_status",
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_data_lifecycle_runs_tenant_started",
        "data_lifecycle_runs",
        ["tenant_id", "started_at"],
    )
    op.create_index(
        "ix_data_lifecycle_runs_subject",
        "data_lifecycle_runs",
        ["tenant_id", "subject_user_id", "started_at"],
    )


def downgrade() -> None:
    """移除数据生命周期运行证据。"""
    op.drop_index("ix_data_lifecycle_runs_subject", table_name="data_lifecycle_runs")
    op.drop_index("ix_data_lifecycle_runs_tenant_started", table_name="data_lifecycle_runs")
    op.drop_table("data_lifecycle_runs")
