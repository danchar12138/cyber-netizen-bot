"""增加告警建议人工反馈。

Revision ID: 20260915_0034
Revises: 20260915_0033
创建日期：2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0034"
down_revision: str | Sequence[str] | None = "20260915_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建按生命周期唯一的追加式建议反馈。"""
    op.create_table(
        "observability_alert_recommendation_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lifecycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("recommendation_action", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "recommendation_action IN ('acknowledge', 'suppress', 'observe')",
            name="ck_observability_alert_recommendation_feedback_action",
        ),
        sa.CheckConstraint(
            "priority IN ('urgent', 'high', 'normal')",
            name="ck_observability_alert_recommendation_feedback_priority",
        ),
        sa.CheckConstraint(
            "decision IN ('accepted', 'rejected')",
            name="ck_observability_alert_recommendation_feedback_decision",
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["lifecycle_id"], ["observability_alert_lifecycles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "lifecycle_id",
            name="uq_observability_alert_recommendation_feedback_lifecycle",
        ),
    )
    op.create_index(
        "ix_observability_alert_recommendation_feedback_tenant_agent_time",
        "observability_alert_recommendation_feedback",
        ["tenant_id", "agent_id", "feedback_at"],
    )
    op.create_index(
        "ix_observability_alert_recommendation_feedback_tenant_time",
        "observability_alert_recommendation_feedback",
        ["tenant_id", "feedback_at"],
    )


def downgrade() -> None:
    """删除建议反馈与相关索引。"""
    op.drop_index(
        "ix_observability_alert_recommendation_feedback_tenant_time",
        table_name="observability_alert_recommendation_feedback",
    )
    op.drop_index(
        "ix_observability_alert_recommendation_feedback_tenant_agent_time",
        table_name="observability_alert_recommendation_feedback",
    )
    op.drop_table("observability_alert_recommendation_feedback")
