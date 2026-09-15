"""增加通用告警处置追加历史。

Revision ID: 20260915_0031
Revises: 20260914_0030
创建日期：2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0031"
down_revision: str | Sequence[str] | None = "20260914_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建按租户、Agent 与生命周期隔离的处置历史。"""
    op.create_table(
        "observability_alert_disposition_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lifecycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('acknowledged', 'suppressed', 'cleared')",
            name="ck_observability_alert_disposition_events_action",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["lifecycle_id"], ["observability_alert_lifecycles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_observability_alert_disposition_events_tenant_agent_time",
        "observability_alert_disposition_events",
        ["tenant_id", "agent_id", "occurred_at"],
    )
    op.create_index(
        "ix_observability_alert_disposition_events_lifecycle_time",
        "observability_alert_disposition_events",
        ["tenant_id", "agent_id", "lifecycle_id", "occurred_at"],
    )


def downgrade() -> None:
    """移除通用告警处置历史。"""
    op.drop_index(
        "ix_observability_alert_disposition_events_lifecycle_time",
        table_name="observability_alert_disposition_events",
    )
    op.drop_index(
        "ix_observability_alert_disposition_events_tenant_agent_time",
        table_name="observability_alert_disposition_events",
    )
    op.drop_table("observability_alert_disposition_events")
