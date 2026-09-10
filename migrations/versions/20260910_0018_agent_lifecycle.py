"""增加 Agent 归档、软删除与保留期状态。

Revision ID: 20260910_0018
Revises: 20260910_0017
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0018"
down_revision: str | Sequence[str] | None = "20260910_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展生命周期状态并为后续保留期清理建立索引。"""
    op.add_column("agents", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("agents", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("agents", sa.Column("purge_after", sa.DateTime(timezone=True)))
    op.drop_constraint("ck_agents_status", "agents", type_="check")
    op.create_check_constraint(
        "ck_agents_status",
        "agents",
        "status IN ('active', 'disabled', 'archived', 'deleted')",
    )
    op.create_check_constraint(
        "ck_agents_lifecycle_timestamps",
        "agents",
        "(status = 'archived' AND archived_at IS NOT NULL "
        "AND deleted_at IS NULL AND purge_after IS NULL) OR "
        "(status = 'deleted' AND archived_at IS NOT NULL "
        "AND deleted_at IS NOT NULL AND purge_after IS NOT NULL "
        "AND purge_after >= deleted_at) OR "
        "(status IN ('active', 'disabled') AND archived_at IS NULL "
        "AND deleted_at IS NULL AND purge_after IS NULL)",
    )
    op.create_index("ix_agents_purge_after", "agents", ["tenant_id", "purge_after"])


def downgrade() -> None:
    """将非运行状态降级为停用并移除生命周期时间字段。"""
    op.drop_constraint("ck_agents_lifecycle_timestamps", "agents", type_="check")
    op.execute(
        sa.text("UPDATE agents SET status = 'disabled' WHERE status IN ('archived', 'deleted')")
    )
    op.drop_index("ix_agents_purge_after", table_name="agents")
    op.drop_constraint("ck_agents_status", "agents", type_="check")
    op.create_check_constraint(
        "ck_agents_status",
        "agents",
        "status IN ('active', 'disabled')",
    )
    op.drop_column("agents", "purge_after")
    op.drop_column("agents", "deleted_at")
    op.drop_column("agents", "archived_at")
