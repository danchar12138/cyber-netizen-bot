"""增加渠道告警确认与抑制处置记录。

Revision ID: 20260913_0023
Revises: 20260913_0022
创建日期：2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260913_0023"
down_revision: str | None = "20260913_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建按租户和 Agent 隔离的告警处置记录。"""
    op.create_table(
        "channel_alert_dispositions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_key", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('acknowledged', 'suppressed')",
            name="ck_channel_alert_dispositions_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "alert_key",
            name="uq_channel_alert_disposition_key",
        ),
    )
    op.create_index(
        "ix_channel_alert_dispositions_tenant_agent",
        "channel_alert_dispositions",
        ["tenant_id", "agent_id", "updated_at"],
    )


def downgrade() -> None:
    """移除渠道告警处置记录。"""
    op.drop_index(
        "ix_channel_alert_dispositions_tenant_agent",
        table_name="channel_alert_dispositions",
    )
    op.drop_table("channel_alert_dispositions")
