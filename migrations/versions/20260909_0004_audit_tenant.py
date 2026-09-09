"""为审计日志增加租户查询边界。

Revision ID: 20260909_0004
Revises: 20260909_0003
创建日期：2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260909_0004"
down_revision: str | Sequence[str] | None = "20260909_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加可空租户字段；空值保留给系统级配置操作。"""
    op.add_column(
        "audit_logs",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_audit_logs_tenant_created",
        "audit_logs",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    """移除审计日志租户查询字段。"""
    op.drop_index("ix_audit_logs_tenant_created", table_name="audit_logs")
    op.drop_column("audit_logs", "tenant_id")
