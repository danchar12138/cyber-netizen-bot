"""增加租户内 Agent 名称唯一约束。

Revision ID: 20260910_0016
Revises: 20260910_0015
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0016"
down_revision: str | Sequence[str] | None = "20260910_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """禁止同一租户创建仅大小写不同的同名 Agent。"""
    op.create_index(
        "uq_agents_tenant_name_ci",
        "agents",
        ["tenant_id", sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    """移除租户内 Agent 名称唯一索引。"""
    op.drop_index("uq_agents_tenant_name_ci", table_name="agents")
