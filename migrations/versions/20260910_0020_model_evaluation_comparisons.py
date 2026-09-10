"""建立多模型同源回放实验与候选运行关联。

Revision ID: 20260910_0020
Revises: 20260910_0019
Create Date: 2026-09-10 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0020"
down_revision: str | None = "20260910_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存共享资源快照，并关联每个已冻结候选回放。"""
    op.create_table(
        "evaluation_comparisons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("suite_key", sa.String(length=120), nullable=False),
        sa.Column("suite_version", sa.Integer(), nullable=False),
        sa.Column("suite_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("persona_version", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("model_route_version", sa.Integer(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('completed')", name="ck_evaluation_comparisons_status"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["suite_id"], ["evaluation_suites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_comparisons_tenant_created",
        "evaluation_comparisons",
        ["tenant_id", "agent_id", "created_at"],
    )
    op.create_table(
        "evaluation_comparison_entries",
        sa.Column("comparison_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("profile_key", sa.String(length=120), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("position > 0", name="ck_evaluation_comparison_entries_position"),
        sa.CheckConstraint(
            "profile_version > 0",
            name="ck_evaluation_comparison_entries_profile_version",
        ),
        sa.ForeignKeyConstraint(
            ["comparison_id"], ["evaluation_comparisons.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("comparison_id", "run_id"),
        sa.UniqueConstraint("run_id"),
        sa.UniqueConstraint(
            "comparison_id",
            "position",
            name="uq_evaluation_comparison_entries_position",
        ),
        sa.UniqueConstraint(
            "comparison_id",
            "profile_key",
            name="uq_evaluation_comparison_entries_profile",
        ),
    )
    op.create_index(
        "ix_evaluation_comparison_entries_run",
        "evaluation_comparison_entries",
        ["run_id"],
    )


def downgrade() -> None:
    """按外键依赖逆序移除多模型对比表。"""
    op.drop_index(
        "ix_evaluation_comparison_entries_run",
        table_name="evaluation_comparison_entries",
    )
    op.drop_table("evaluation_comparison_entries")
    op.drop_index(
        "ix_evaluation_comparisons_tenant_created",
        table_name="evaluation_comparisons",
    )
    op.drop_table("evaluation_comparisons")
