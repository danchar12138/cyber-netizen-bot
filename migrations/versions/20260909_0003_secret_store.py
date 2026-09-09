"""扩展密钥引用为可审计的信封加密存储。

Revision ID: 20260909_0003
Revises: 20260909_0002
创建日期：2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260909_0003"
down_revision: str | Sequence[str] | None = "20260909_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加作用域、加密信封版本和完整性状态元数据。"""
    op.add_column("secret_references", sa.Column("key", sa.String(length=255), nullable=True))
    op.add_column("secret_references", sa.Column("scope_type", sa.String(length=24), nullable=True))
    op.add_column(
        "secret_references",
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "secret_references", sa.Column("encrypted_value", sa.LargeBinary(), nullable=True)
    )
    op.add_column("secret_references", sa.Column("nonce", sa.LargeBinary(), nullable=True))
    op.add_column(
        "secret_references",
        sa.Column("key_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "secret_references",
        sa.Column(
            "integrity_status",
            sa.String(length=24),
            server_default=sa.text("'untested'"),
            nullable=False,
        ),
    )
    op.add_column(
        "secret_references",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "secret_references",
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 兼容早期可能已存在、但尚未归属配置键的外部引用；不会尝试迁移明文。
    op.execute("UPDATE secret_references SET key = 'legacy.' || id::text WHERE key IS NULL")
    op.execute("UPDATE secret_references SET scope_type = 'system' WHERE scope_type IS NULL")
    op.execute("UPDATE secret_references SET masked_hint = '••••' WHERE masked_hint IS NULL")
    op.alter_column("secret_references", "key", nullable=False)
    op.alter_column("secret_references", "scope_type", nullable=False)
    op.alter_column("secret_references", "masked_hint", nullable=False)

    op.create_check_constraint(
        "ck_secret_references_scope_type",
        "secret_references",
        "scope_type IN ('system', 'tenant', 'agent', 'channel', 'user')",
    )
    op.create_check_constraint(
        "ck_secret_references_integrity_status",
        "secret_references",
        "integrity_status IN ('untested', 'valid', 'invalid')",
    )
    op.create_check_constraint(
        "ck_secret_references_envelope",
        "secret_references",
        "(encrypted_value IS NULL) = (nonce IS NULL)",
    )
    op.create_unique_constraint(
        "uq_secret_references_scope_key",
        "secret_references",
        ["key", "scope_type", "scope_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_secret_references_key", "secret_references", ["key"])


def downgrade() -> None:
    """恢复最初的不透明密钥引用结构。"""
    op.drop_index("ix_secret_references_key", table_name="secret_references")
    op.drop_constraint("uq_secret_references_scope_key", "secret_references", type_="unique")
    op.drop_constraint("ck_secret_references_envelope", "secret_references", type_="check")
    op.drop_constraint("ck_secret_references_integrity_status", "secret_references", type_="check")
    op.drop_constraint("ck_secret_references_scope_type", "secret_references", type_="check")
    op.drop_column("secret_references", "last_tested_at")
    op.drop_column("secret_references", "created_at")
    op.drop_column("secret_references", "integrity_status")
    op.drop_column("secret_references", "key_version")
    op.drop_column("secret_references", "nonce")
    op.drop_column("secret_references", "encrypted_value")
    op.drop_column("secret_references", "scope_id")
    op.drop_column("secret_references", "scope_type")
    op.drop_column("secret_references", "key")
