"""建立外部身份、线程路由与版本化 Inbox 诊断字段。

Revision ID: 20260910_0019
Revises: 20260910_0018
Create Date: 2026-09-10 16:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0019"
down_revision: str | None = "20260910_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """补齐真实 IM 接入前必须稳定的映射、作用域和安全诊断数据。"""
    op.create_table(
        "external_identity_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("external_subject_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "platform IN ('web', 'feishu', 'discord', 'telegram')",
            name="ck_external_identity_mappings_platform",
        ),
        sa.CheckConstraint(
            "status IN ('enabled', 'disabled')",
            name="ck_external_identity_mappings_status",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "platform",
            "channel_id",
            "external_subject_id",
            name="uq_external_identity_mappings_subject",
        ),
    )
    op.create_index(
        "ix_external_identity_mappings_agent",
        "external_identity_mappings",
        ["tenant_id", "agent_id", "channel_id", "status"],
    )

    op.create_table(
        "external_conversation_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("external_conversation_id", sa.String(length=255), nullable=False),
        sa.Column("external_thread_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('direct', 'group')",
            name="ck_external_conversation_mappings_kind",
        ),
        sa.CheckConstraint(
            "platform IN ('web', 'feishu', 'discord', 'telegram')",
            name="ck_external_conversation_mappings_platform",
        ),
        sa.CheckConstraint(
            "status IN ('enabled', 'disabled')",
            name="ck_external_conversation_mappings_status",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["channel_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "platform",
            "channel_id",
            "external_conversation_id",
            "external_thread_id",
            name="uq_external_conversation_mappings_route",
        ),
        sa.UniqueConstraint(
            "channel_id",
            "conversation_id",
            name="uq_external_conversation_mappings_local",
        ),
    )
    op.create_index(
        "ix_external_conversation_mappings_agent",
        "external_conversation_mappings",
        ["tenant_id", "agent_id", "channel_id", "status"],
    )

    op.add_column(
        "inbox_events", sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "inbox_events", sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("inbox_events", sa.Column("schema_version", sa.String(length=16)))
    op.add_column("inbox_events", sa.Column("platform", sa.String(length=32)))
    for name in (
        "external_event_digest",
        "external_subject_digest",
        "external_conversation_digest",
        "external_thread_digest",
        "external_message_digest",
    ):
        op.add_column("inbox_events", sa.Column(name, sa.String(length=64)))
    op.add_column(
        "inbox_events", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "inbox_events",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "inbox_events",
        sa.Column(
            "content_kinds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "inbox_events",
        sa.Column("content_block_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        "fk_inbox_events_agent",
        "inbox_events",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_inbox_events_channel",
        "inbox_events",
        "channel_instances",
        ["channel_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_inbox_events_user",
        "inbox_events",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_inbox_events_conversation",
        "inbox_events",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_inbox_events_content_block_count",
        "inbox_events",
        "content_block_count >= 0",
    )
    op.create_index(
        "ix_inbox_events_agent_status",
        "inbox_events",
        ["tenant_id", "agent_id", "status", "received_at"],
    )
    op.drop_constraint("ck_background_jobs_kind", "background_jobs", type_="check")
    op.create_check_constraint(
        "ck_background_jobs_kind",
        "background_jobs",
        "kind IN ('reflection', 'episode_consolidation', 'memory_extraction', "
        "'embedding_rebuild', 'relationship_update', 'scheduled_action', 'inbound_message')",
    )


def downgrade() -> None:
    """恢复通用任务 Inbox，并移除外部路由映射。"""
    op.execute(
        "DELETE FROM inbox_events WHERE job_id IN "
        "(SELECT id FROM background_jobs WHERE kind = 'inbound_message')"
    )
    op.execute("DELETE FROM background_jobs WHERE kind = 'inbound_message'")
    op.drop_constraint("ck_background_jobs_kind", "background_jobs", type_="check")
    op.create_check_constraint(
        "ck_background_jobs_kind",
        "background_jobs",
        "kind IN ('reflection', 'episode_consolidation', 'memory_extraction', "
        "'embedding_rebuild', 'relationship_update', 'scheduled_action')",
    )
    op.drop_index("ix_inbox_events_agent_status", table_name="inbox_events")
    op.drop_constraint("ck_inbox_events_content_block_count", "inbox_events", type_="check")
    op.drop_constraint("fk_inbox_events_conversation", "inbox_events", type_="foreignkey")
    op.drop_constraint("fk_inbox_events_user", "inbox_events", type_="foreignkey")
    op.drop_constraint("fk_inbox_events_channel", "inbox_events", type_="foreignkey")
    op.drop_constraint("fk_inbox_events_agent", "inbox_events", type_="foreignkey")
    for name in (
        "content_block_count",
        "content_kinds",
        "conversation_id",
        "user_id",
        "external_message_digest",
        "external_thread_digest",
        "external_conversation_digest",
        "external_subject_digest",
        "external_event_digest",
        "platform",
        "schema_version",
        "channel_id",
        "agent_id",
    ):
        op.drop_column("inbox_events", name)
    op.drop_index(
        "ix_external_conversation_mappings_agent",
        table_name="external_conversation_mappings",
    )
    op.drop_table("external_conversation_mappings")
    op.drop_index(
        "ix_external_identity_mappings_agent",
        table_name="external_identity_mappings",
    )
    op.drop_table("external_identity_mappings")
