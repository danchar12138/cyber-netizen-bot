"""增加渠道告警所需的安全健康趋势快照。

Revision ID: 20260913_0022
Revises: 20260910_0021
创建日期：2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260913_0022"
down_revision: str | None = "20260910_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建租户隔离的渠道健康安全快照。"""
    op.create_table(
        "channel_health_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("configured", sa.Boolean(), nullable=False),
        sa.Column("pending_update_count", sa.Integer(), nullable=False),
        sa.Column("remote_error_present", sa.Boolean(), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('healthy', 'degraded', 'not_configured', 'disabled')",
            name="ck_channel_health_snapshots_status",
        ),
        sa.CheckConstraint(
            "pending_update_count >= 0",
            name="ck_channel_health_snapshots_pending",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_channel_health_snapshots_tenant_time",
        "channel_health_snapshots",
        ["tenant_id", "sampled_at"],
    )
    op.create_index(
        "ix_channel_health_snapshots_channel_time",
        "channel_health_snapshots",
        ["channel_id", "sampled_at"],
    )


def downgrade() -> None:
    """移除渠道健康安全快照。"""
    op.drop_index(
        "ix_channel_health_snapshots_channel_time",
        table_name="channel_health_snapshots",
    )
    op.drop_index(
        "ix_channel_health_snapshots_tenant_time",
        table_name="channel_health_snapshots",
    )
    op.drop_table("channel_health_snapshots")
