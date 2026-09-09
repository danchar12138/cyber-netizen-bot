"""增加多模态渠道实例、限流与安全诊断。

Revision ID: 20260910_0010
Revises: 20260910_0009
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0010"
down_revision: str | Sequence[str] | None = "20260910_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建不保存凭证明文与消息正文的渠道控制平面数据结构。"""
    op.create_table(
        "channel_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("health_status", sa.String(length=24), nullable=False),
        sa.Column("health_detail", sa.String(length=500), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "platform IN ('web', 'feishu', 'discord', 'telegram')",
            name="ck_channel_instances_platform",
        ),
        sa.CheckConstraint(
            "status IN ('enabled', 'disabled')",
            name="ck_channel_instances_status",
        ),
        sa.CheckConstraint(
            "health_status IN ('healthy', 'degraded', 'not_configured', 'disabled')",
            name="ck_channel_instances_health_status",
        ),
        sa.CheckConstraint(
            "rate_limit_per_minute BETWEEN 1 AND 10000",
            name="ck_channel_instances_rate_limit",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_channel_instances_tenant_name"),
    )
    op.create_index(
        "ix_channel_instances_tenant_platform",
        "channel_instances",
        ["tenant_id", "platform", "status"],
    )

    op.create_table(
        "channel_diagnostic_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(length=24), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("payload_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("degradations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound', 'system')",
            name="ck_channel_diagnostic_events_direction",
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'delivered', 'degraded', 'rejected', 'failed', 'rate_limited')",
            name="ck_channel_diagnostic_events_status",
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_id",
            "direction",
            "idempotency_key",
            name="uq_channel_diagnostic_events_idempotency",
        ),
    )
    op.create_index(
        "ix_channel_diagnostic_events_tenant_time",
        "channel_diagnostic_events",
        ["tenant_id", "occurred_at"],
    )
    op.create_index(
        "ix_channel_diagnostic_events_channel_time",
        "channel_diagnostic_events",
        ["channel_id", "occurred_at"],
    )

    op.create_table(
        "channel_rate_limit_windows",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "used_count BETWEEN 1 AND 10000",
            name="ck_channel_rate_windows_used",
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "channel_id", "window_started_at"),
    )
    op.create_index(
        "ix_channel_rate_windows_time",
        "channel_rate_limit_windows",
        ["window_started_at"],
    )


def downgrade() -> None:
    """按依赖顺序移除渠道诊断、限流与实例表。"""
    op.drop_index("ix_channel_rate_windows_time", table_name="channel_rate_limit_windows")
    op.drop_table("channel_rate_limit_windows")
    op.drop_index(
        "ix_channel_diagnostic_events_channel_time",
        table_name="channel_diagnostic_events",
    )
    op.drop_index(
        "ix_channel_diagnostic_events_tenant_time",
        table_name="channel_diagnostic_events",
    )
    op.drop_table("channel_diagnostic_events")
    op.drop_index("ix_channel_instances_tenant_platform", table_name="channel_instances")
    op.drop_table("channel_instances")
