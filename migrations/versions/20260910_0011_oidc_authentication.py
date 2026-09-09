"""增加 OIDC 外部身份、角色分配与安全管理会话。

Revision ID: 20260910_0011
Revises: 20260910_0010
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0011"
down_revision: str | Sequence[str] | None = "20260910_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建不保存访问令牌明文的正式身份与权限数据结构。"""
    op.create_table(
        "external_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_external_identities_issuer_subject",
        ),
    )
    op.create_index(
        "ix_external_identities_tenant_user",
        "external_identities",
        ["tenant_id", "user_id"],
    )

    op.create_table(
        "role_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('admin', 'operator', 'viewer')",
            name="ck_role_assignments_role",
        ),
        sa.CheckConstraint("source IN ('oidc')", name="ck_role_assignments_source"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            name="uq_role_assignments_tenant_user",
        ),
    )
    op.create_index(
        "ix_role_assignments_tenant_role",
        "role_assignments",
        ["tenant_id", "role"],
    )

    op.create_table(
        "admin_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["external_identity_id"],
            ["external_identities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_admin_sessions_tenant_user_seen",
        "admin_sessions",
        ["tenant_id", "user_id", "last_seen_at"],
    )
    op.create_index("ix_admin_sessions_expires", "admin_sessions", ["expires_at"])


def downgrade() -> None:
    """按依赖顺序移除管理会话、角色分配与外部身份。"""
    op.drop_index("ix_admin_sessions_expires", table_name="admin_sessions")
    op.drop_index("ix_admin_sessions_tenant_user_seen", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_index("ix_role_assignments_tenant_role", table_name="role_assignments")
    op.drop_table("role_assignments")
    op.drop_index(
        "ix_external_identities_tenant_user",
        table_name="external_identities",
    )
    op.drop_table("external_identities")
