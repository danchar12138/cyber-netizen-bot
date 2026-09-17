"""增加评测决策终态审批与 Ed25519 签名证明存储。

Revision ID: 20260917_0037
Revises: 20260917_0036
创建日期：2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0037"
down_revision: str | Sequence[str] | None = "20260917_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存每个决策唯一、不可变且不含私钥的签名审批证明。"""
    op.create_table(
        "evaluation_decision_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "decision_id",
            sa.Uuid(),
            sa.ForeignKey("evaluation_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
            "approved_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("release_environment", sa.String(length=24)),
        sa.Column("change_reference", sa.String(length=120)),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "decision_id",
            name="uq_evaluation_decision_approvals_decision",
        ),
        sa.CheckConstraint(
            "outcome IN ('approved', 'rejected')",
            name="ck_evaluation_decision_approvals_outcome",
        ),
        sa.CheckConstraint(
            "reason IN ('evidence_confirmed', 'risk_unresolved', "
            "'governance_blocked', 'release_not_ready')",
            name="ck_evaluation_decision_approvals_reason",
        ),
        sa.CheckConstraint(
            "release_environment IS NULL OR "
            "release_environment IN ('development', 'staging', 'production')",
            name="ck_evaluation_decision_approvals_environment",
        ),
        sa.CheckConstraint(
            "(release_environment IS NULL AND change_reference IS NULL) OR "
            "(release_environment IS NOT NULL AND change_reference IS NOT NULL)",
            name="ck_evaluation_decision_approvals_release_pair",
        ),
        sa.CheckConstraint(
            "outcome <> 'rejected' OR (release_environment IS NULL AND change_reference IS NULL)",
            name="ck_evaluation_decision_approvals_rejected_release",
        ),
    )
    op.create_index(
        "ix_evaluation_decision_approvals_scope_time",
        "evaluation_decision_approvals",
        ["tenant_id", "agent_id", "approved_at"],
    )


def downgrade() -> None:
    """删除审批证明表。"""
    op.drop_index(
        "ix_evaluation_decision_approvals_scope_time",
        table_name="evaluation_decision_approvals",
    )
    op.drop_table("evaluation_decision_approvals")
