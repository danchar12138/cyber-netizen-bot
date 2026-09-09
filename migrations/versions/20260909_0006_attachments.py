"""增加会话附件的预签名上传与生命周期元数据。

Revision ID: 20260909_0006
Revises: 20260909_0005
创建日期：2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260909_0006"
down_revision: str | Sequence[str] | None = "20260909_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建租户隔离且可清理的附件元数据表。"""
    op.create_table(
        "attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=160), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("validation_error", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'attached', 'rejected', 'deleted')",
            name="ck_attachments_status",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_attachments_size_positive"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_attachments_sha256"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_attachments_conversation_created",
        "attachments",
        ["conversation_id", "created_at"],
    )
    op.create_index("ix_attachments_expiry", "attachments", ["status", "expires_at"])
    op.create_index("ix_attachments_message", "attachments", ["message_id"])


def downgrade() -> None:
    """移除附件生命周期元数据。"""
    op.drop_index("ix_attachments_message", table_name="attachments")
    op.drop_index("ix_attachments_expiry", table_name="attachments")
    op.drop_index("ix_attachments_conversation_created", table_name="attachments")
    op.drop_table("attachments")
