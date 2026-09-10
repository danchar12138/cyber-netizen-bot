"""增加持久化多模态消息内容块。

Revision ID: 20260910_0015
Revises: 20260910_0014
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0015"
down_revision: str | Sequence[str] | None = "20260910_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建立内容块表，并为现有文本和已绑定附件补齐有序内容块。"""
    op.create_table(
        "message_parts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_type", sa.String(length=160), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("alt_text", sa.String(length=500), nullable=True),
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
        sa.CheckConstraint("position >= 0", name="ck_message_parts_position"),
        sa.CheckConstraint(
            "kind IN ('text', 'markdown', 'image', 'file')",
            name="ck_message_parts_kind",
        ),
        sa.CheckConstraint(
            "((kind IN ('text', 'markdown') AND text IS NOT NULL "
            "AND attachment_id IS NULL AND content_type IS NULL AND file_name IS NULL "
            "AND size_bytes IS NULL AND sha256 IS NULL) OR "
            "(kind IN ('image', 'file') AND text IS NULL AND content_type IS NOT NULL "
            "AND file_name IS NOT NULL AND size_bytes > 0 "
            "AND sha256 ~ '^[0-9a-f]{64}$'))",
            name="ck_message_parts_shape",
        ),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "position", name="uq_message_parts_position"),
    )
    op.create_index(
        "ix_message_parts_tenant_message",
        "message_parts",
        ["tenant_id", "message_id"],
    )
    op.create_index(
        "ix_message_parts_attachment",
        "message_parts",
        ["attachment_id"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO message_parts (
                id, tenant_id, message_id, position, kind, text, created_at, updated_at
            )
            SELECT
                gen_random_uuid(), tenant_id, id, 0, 'markdown', content, created_at, updated_at
            FROM messages
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO message_parts (
                id, tenant_id, message_id, position, kind, attachment_id,
                content_type, file_name, size_bytes, sha256, alt_text,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                tenant_id,
                message_id,
                row_number() OVER (
                    PARTITION BY message_id ORDER BY created_at, id
                )::integer,
                CASE WHEN content_type LIKE 'image/%' THEN 'image' ELSE 'file' END,
                id,
                content_type,
                original_name,
                size_bytes,
                sha256,
                CASE WHEN content_type LIKE 'image/%' THEN original_name ELSE NULL END,
                created_at,
                COALESCE(attached_at, uploaded_at, created_at)
            FROM attachments
            WHERE message_id IS NOT NULL AND status = 'attached'
            """
        )
    )


def downgrade() -> None:
    """移除消息内容块及其索引。"""
    op.drop_index("ix_message_parts_attachment", table_name="message_parts")
    op.drop_index("ix_message_parts_tenant_message", table_name="message_parts")
    op.drop_table("message_parts")
