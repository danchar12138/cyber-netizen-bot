"""增加渠道实例的 Agent 归属与隔离约束。

Revision ID: 20260910_0017
Revises: 20260910_0016
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0017"
down_revision: str | Sequence[str] | None = "20260910_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """确定性回填现有渠道并建立不可变的 Agent 归属。"""
    op.add_column(
        "channel_instances",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE channel_instances AS channel
            SET agent_id = (
                SELECT agent.id
                FROM agents AS agent
                WHERE agent.tenant_id = channel.tenant_id
                ORDER BY agent.created_at, agent.id
                LIMIT 1
            )
            """
        )
    )
    op.alter_column("channel_instances", "agent_id", nullable=False)
    op.create_foreign_key(
        "fk_channel_instances_agent_id_agents",
        "channel_instances",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_channel_instances_tenant_name",
        "channel_instances",
        type_="unique",
    )
    op.drop_index("ix_channel_instances_tenant_platform", table_name="channel_instances")
    op.create_index(
        "uq_channel_instances_tenant_agent_name_ci",
        "channel_instances",
        ["tenant_id", "agent_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_index(
        "ix_channel_instances_tenant_agent_platform",
        "channel_instances",
        ["tenant_id", "agent_id", "platform", "status"],
    )


def downgrade() -> None:
    """恢复租户级名称约束并移除 Agent 归属。"""
    op.drop_index(
        "ix_channel_instances_tenant_agent_platform",
        table_name="channel_instances",
    )
    op.drop_index(
        "uq_channel_instances_tenant_agent_name_ci",
        table_name="channel_instances",
    )
    op.create_unique_constraint(
        "uq_channel_instances_tenant_name",
        "channel_instances",
        ["tenant_id", "name"],
    )
    op.create_index(
        "ix_channel_instances_tenant_platform",
        "channel_instances",
        ["tenant_id", "platform", "status"],
    )
    op.drop_constraint(
        "fk_channel_instances_agent_id_agents",
        "channel_instances",
        type_="foreignkey",
    )
    op.drop_column("channel_instances", "agent_id")
