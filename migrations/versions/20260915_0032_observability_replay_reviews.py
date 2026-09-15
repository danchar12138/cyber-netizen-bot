"""增加通用告警通知重放复核事件。

Revision ID: 20260915_0032
Revises: 20260915_0031
创建日期：2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0032"
down_revision: str | Sequence[str] | None = "20260915_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建不含任务载荷的租户与 Agent 隔离复核历史。"""
    op.create_table(
        "observability_alert_replay_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(length=80), nullable=True),
        sa.Column("source_key", sa.String(length=255), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=48), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suppression_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('allowed', 'blocked')",
            name="ck_observability_alert_replay_reviews_decision",
        ),
        sa.CheckConstraint(
            "reason_code IN ('allowed_no_suppression', 'allowed_suppression_expired', "
            "'blocked_active_suppression', 'blocked_missing_source', "
            "'blocked_invalid_agent', 'blocked_agent_mismatch')",
            name="ck_observability_alert_replay_reviews_reason",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_job_id"], ["background_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_observability_alert_replay_reviews_tenant_agent_time",
        "observability_alert_replay_reviews",
        ["tenant_id", "agent_id", "reviewed_at"],
    )
    op.create_index(
        "ix_observability_alert_replay_reviews_tenant_decision_time",
        "observability_alert_replay_reviews",
        ["tenant_id", "decision", "reviewed_at"],
    )
    op.create_index(
        "ix_observability_alert_replay_reviews_source_job_time",
        "observability_alert_replay_reviews",
        ["source_job_id", "reviewed_at"],
    )


def downgrade() -> None:
    """移除重放复核事件。"""
    op.drop_index(
        "ix_observability_alert_replay_reviews_source_job_time",
        table_name="observability_alert_replay_reviews",
    )
    op.drop_index(
        "ix_observability_alert_replay_reviews_tenant_decision_time",
        table_name="observability_alert_replay_reviews",
    )
    op.drop_index(
        "ix_observability_alert_replay_reviews_tenant_agent_time",
        table_name="observability_alert_replay_reviews",
    )
    op.drop_table("observability_alert_replay_reviews")
