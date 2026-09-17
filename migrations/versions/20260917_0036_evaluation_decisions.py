"""增加人工评测决策的不可变安全报告存储。

Revision ID: 20260917_0036
Revises: 20260916_0035
创建日期：2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0036"
down_revision: str | Sequence[str] | None = "20260916_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """原样保存经白名单序列化的 JSON 原始字节与 SHA-256。"""
    op.create_table(
        "evaluation_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('adopt_candidate', 'keep_baseline', 'wait_for_evidence')",
            name="ck_evaluation_decisions_outcome",
        ),
        sa.CheckConstraint(
            "reason IN ('quality_gain', 'regression_risk', "
            "'insufficient_evidence', 'manual_review')",
            name="ck_evaluation_decisions_reason",
        ),
    )
    op.create_index(
        "ix_evaluation_decisions_scope_created",
        "evaluation_decisions",
        ["tenant_id", "agent_id", "created_at"],
    )


def downgrade() -> None:
    """删除决策记录表。"""
    op.drop_index("ix_evaluation_decisions_scope_created", table_name="evaluation_decisions")
    op.drop_table("evaluation_decisions")
