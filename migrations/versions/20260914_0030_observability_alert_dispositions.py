"""增加通用告警确认与临时抑制处置记录。

Revision ID: 20260914_0030
Revises: 20260914_0029
创建日期：2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260914_0030"
down_revision: str | Sequence[str] | None = "20260914_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建按租户、Agent 和稳定来源键隔离的通用告警处置记录。"""
    op.create_table(
        "observability_alert_dispositions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('acknowledged', 'suppressed')",
            name="ck_observability_alert_dispositions_status",
        ),
        sa.CheckConstraint(
            "(status = 'acknowledged' AND expires_at IS NULL) OR "
            "(status = 'suppressed' AND expires_at IS NOT NULL)",
            name="ck_observability_alert_dispositions_expiry",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "source_type",
            "source_key",
            name="uq_observability_alert_disposition_key",
        ),
    )
    op.create_index(
        "ix_observability_alert_dispositions_tenant_agent",
        "observability_alert_dispositions",
        ["tenant_id", "agent_id", "updated_at"],
    )


def downgrade() -> None:
    """移除通用告警处置记录。"""
    op.drop_index(
        "ix_observability_alert_dispositions_tenant_agent",
        table_name="observability_alert_dispositions",
    )
    op.drop_table("observability_alert_dispositions")
