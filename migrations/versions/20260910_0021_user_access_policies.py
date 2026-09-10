"""增加用户访问策略、可信角色覆盖与原子请求窗口。

Revision ID: 20260910_0021
Revises: 20260910_0020
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0021"
down_revision: str | None = "20260910_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展用户和角色治理字段，并建立单行分钟请求计数。"""
    op.add_column("users", sa.Column("request_rate_limit_per_minute", sa.Integer()))
    op.add_column("users", sa.Column("suspended_until", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("suspension_reason", sa.String(length=500)))
    op.add_column("users", sa.Column("access_policy_updated_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_users_request_rate_limit",
        "users",
        "request_rate_limit_per_minute IS NULL OR "
        "request_rate_limit_per_minute BETWEEN 1 AND 10000",
    )
    op.create_check_constraint(
        "ck_users_suspension_pair",
        "users",
        "(suspended_until IS NULL) = (suspension_reason IS NULL)",
    )
    op.create_index("ix_users_suspended", "users", ["tenant_id", "suspended_until"])

    op.add_column("role_assignments", sa.Column("trusted_role", sa.String(length=24)))
    op.add_column(
        "role_assignments",
        sa.Column("overridden_by", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "role_assignments",
        sa.Column("override_expires_at", sa.DateTime(timezone=True)),
    )
    op.execute(sa.text("UPDATE role_assignments SET trusted_role = role"))
    op.alter_column("role_assignments", "trusted_role", nullable=False)
    op.create_foreign_key(
        "fk_role_assignments_overridden_by_users",
        "role_assignments",
        "users",
        ["overridden_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_role_assignments_source", "role_assignments", type_="check")
    op.create_check_constraint(
        "ck_role_assignments_source",
        "role_assignments",
        "source IN ('oidc', 'manual')",
    )
    op.create_check_constraint(
        "ck_role_assignments_trusted_role",
        "role_assignments",
        "trusted_role IN ('admin', 'operator', 'viewer')",
    )
    op.create_check_constraint(
        "ck_role_assignments_override",
        "role_assignments",
        "(source = 'oidc' AND role = trusted_role AND overridden_by IS NULL "
        "AND override_expires_at IS NULL) OR "
        "(source = 'manual' AND overridden_by IS NOT NULL)",
    )

    op.create_table(
        "user_request_rate_limit_windows",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("used_count >= 1", name="ck_user_request_rate_window_used"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "user_id"),
    )
    op.create_index(
        "ix_user_request_rate_windows_updated",
        "user_request_rate_limit_windows",
        ["updated_at"],
    )


def downgrade() -> None:
    """恢复可信角色并移除访问策略数据。"""
    op.drop_index(
        "ix_user_request_rate_windows_updated",
        table_name="user_request_rate_limit_windows",
    )
    op.drop_table("user_request_rate_limit_windows")

    op.execute(
        sa.text(
            "UPDATE role_assignments SET role = trusted_role, source = 'oidc', "
            "overridden_by = NULL, override_expires_at = NULL"
        )
    )
    op.drop_constraint("ck_role_assignments_override", "role_assignments", type_="check")
    op.drop_constraint("ck_role_assignments_trusted_role", "role_assignments", type_="check")
    op.drop_constraint("ck_role_assignments_source", "role_assignments", type_="check")
    op.create_check_constraint(
        "ck_role_assignments_source",
        "role_assignments",
        "source IN ('oidc')",
    )
    op.drop_constraint(
        "fk_role_assignments_overridden_by_users",
        "role_assignments",
        type_="foreignkey",
    )
    op.drop_column("role_assignments", "override_expires_at")
    op.drop_column("role_assignments", "overridden_by")
    op.drop_column("role_assignments", "trusted_role")

    op.drop_index("ix_users_suspended", table_name="users")
    op.drop_constraint("ck_users_suspension_pair", "users", type_="check")
    op.drop_constraint("ck_users_request_rate_limit", "users", type_="check")
    op.drop_column("users", "access_policy_updated_at")
    op.drop_column("users", "suspension_reason")
    op.drop_column("users", "suspended_until")
    op.drop_column("users", "request_rate_limit_per_minute")
