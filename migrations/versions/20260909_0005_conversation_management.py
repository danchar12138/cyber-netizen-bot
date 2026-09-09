"""增加完整会话管理、分支、反馈与全文搜索基础。

Revision ID: 20260909_0005
Revises: 20260909_0004
创建日期：2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260909_0005"
down_revision: str | Sequence[str] | None = "20260909_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展会话和消息，并允许同一触发消息产生多个可回放 Run。"""
    op.add_column(
        "conversations", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("branched_from_conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("branched_from_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_branch_conversation",
        "conversations",
        "conversations",
        ["branched_from_conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_conversations_branch_message",
        "conversations",
        "messages",
        ["branched_from_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversations_member_state",
        "conversations",
        ["deleted_at", "pinned_at", "updated_at"],
    )

    op.add_column(
        "messages",
        sa.Column("edited_from_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_edited_from",
        "messages",
        "messages",
        ["edited_from_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_messages_content_search",
        "messages",
        [sa.text("to_tsvector('simple', content)")],
        postgresql_using="gin",
    )
    op.drop_constraint("agent_runs_trigger_message_id_key", "agent_runs", type_="unique")
    op.create_index("ix_agent_runs_trigger", "agent_runs", ["trigger_message_id"])

    op.create_table(
        "message_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.String(length=24), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating IN ('positive', 'negative')", name="ck_message_feedback_rating"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "user_id", name="uq_message_feedback_user"),
    )
    op.create_index(
        "ix_message_feedback_conversation",
        "message_feedback",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    """移除会话管理扩展并恢复单触发消息单 Run 约束。"""
    op.drop_index("ix_message_feedback_conversation", table_name="message_feedback")
    op.drop_table("message_feedback")
    op.drop_index("ix_agent_runs_trigger", table_name="agent_runs")
    op.create_unique_constraint(
        "agent_runs_trigger_message_id_key", "agent_runs", ["trigger_message_id"]
    )
    op.drop_index("ix_messages_content_search", table_name="messages")
    op.drop_constraint("fk_messages_edited_from", "messages", type_="foreignkey")
    op.drop_column("messages", "edited_from_id")
    op.drop_index("ix_conversations_member_state", table_name="conversations")
    op.drop_constraint("fk_conversations_branch_message", "conversations", type_="foreignkey")
    op.drop_constraint("fk_conversations_branch_conversation", "conversations", type_="foreignkey")
    op.drop_column("conversations", "branched_from_message_id")
    op.drop_column("conversations", "branched_from_conversation_id")
    op.drop_column("conversations", "deleted_at")
    op.drop_column("conversations", "archived_at")
    op.drop_column("conversations", "pinned_at")
