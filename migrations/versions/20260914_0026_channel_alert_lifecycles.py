"""增加渠道告警事件持久化生命周期。

Revision ID: 20260914_0026
Revises: 20260914_0025
创建日期：2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260914_0026"
down_revision: str | None = "20260914_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建按租户与 Agent 隔离的告警事件生命周期。"""
    op.create_table(
        "channel_alert_lifecycles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_key", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=24), nullable=False),
        sa.Column("first_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'resolved')",
            name="ck_channel_alert_lifecycles_status",
        ),
        sa.CheckConstraint(
            "severity IN ('warning', 'critical')",
            name="ck_channel_alert_lifecycles_severity",
        ),
        sa.CheckConstraint(
            "occurrences >= 1",
            name="ck_channel_alert_lifecycles_occurrences",
        ),
        sa.CheckConstraint(
            "recovery_duration_seconds IS NULL OR recovery_duration_seconds >= 0",
            name="ck_channel_alert_lifecycles_recovery_duration",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND resolved_at IS NULL AND recovery_duration_seconds IS NULL) "
            "OR (status = 'resolved' AND resolved_at IS NOT NULL "
            "AND recovery_duration_seconds IS NOT NULL)",
            name="ck_channel_alert_lifecycles_resolution",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_channel_alert_lifecycles_active_key",
        "channel_alert_lifecycles",
        ["tenant_id", "agent_id", "alert_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_channel_alert_lifecycles_tenant_agent_time",
        "channel_alert_lifecycles",
        ["tenant_id", "agent_id", "first_occurred_at"],
    )
    op.create_index(
        "ix_channel_alert_lifecycles_tenant_status",
        "channel_alert_lifecycles",
        ["tenant_id", "status", "updated_at"],
    )


def downgrade() -> None:
    """移除渠道告警事件生命周期。"""
    op.drop_index(
        "ix_channel_alert_lifecycles_tenant_status",
        table_name="channel_alert_lifecycles",
    )
    op.drop_index(
        "ix_channel_alert_lifecycles_tenant_agent_time",
        table_name="channel_alert_lifecycles",
    )
    op.drop_index(
        "uq_channel_alert_lifecycles_active_key",
        table_name="channel_alert_lifecycles",
    )
    op.drop_table("channel_alert_lifecycles")
