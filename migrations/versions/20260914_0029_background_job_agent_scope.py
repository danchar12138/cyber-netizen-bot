"""将后台任务 Agent 归属从载荷提升为独立列。

Revision ID: 20260914_0029
Revises: 20260914_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_0029"
down_revision: str | Sequence[str] | None = "20260914_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加任务归属列，并只回填可验证的同租户 Agent。"""
    op.add_column(
        "background_jobs",
        sa.Column("agent_id", sa.UUID(), nullable=True),
    )

    # 旧任务载荷可能来自系统任务或历史版本；仅接受格式正确且属于同一租户的 UUID。
    op.execute(
        sa.text(
            """
            UPDATE background_jobs AS job
            SET agent_id = (job.payload ->> 'agent_id')::uuid
            WHERE jsonb_typeof(job.payload -> 'agent_id') = 'string'
              AND job.payload ->> 'agent_id' ~*
                  '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                  '[0-9a-f]{4}-[0-9a-f]{12}$'
              AND EXISTS (
                  SELECT 1
                  FROM agents AS agent
                  WHERE agent.id = (job.payload ->> 'agent_id')::uuid
                    AND agent.tenant_id = job.tenant_id
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE background_jobs AS job
            SET agent_id = inbox.agent_id
            FROM inbox_events AS inbox
            JOIN agents AS agent ON agent.id = inbox.agent_id
            WHERE job.source_inbox_id = inbox.id
              AND job.agent_id IS NULL
              AND inbox.agent_id IS NOT NULL
              AND inbox.tenant_id = job.tenant_id
              AND agent.tenant_id = job.tenant_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE background_jobs AS job
            SET agent_id = action.agent_id
            FROM scheduled_actions AS action
            JOIN agents AS agent ON agent.id = action.agent_id
            WHERE action.job_id = job.id
              AND job.agent_id IS NULL
              AND action.tenant_id = job.tenant_id
              AND agent.tenant_id = job.tenant_id
            """
        )
    )
    op.create_foreign_key(
        "fk_background_jobs_agent_id_agents",
        "background_jobs",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_background_jobs_tenant_agent_status_available",
        "background_jobs",
        ["tenant_id", "agent_id", "status", "available_at"],
    )


def downgrade() -> None:
    """移除任务 Agent 归属列及其索引。"""
    op.drop_index(
        "ix_background_jobs_tenant_agent_status_available",
        table_name="background_jobs",
    )
    op.drop_constraint(
        "fk_background_jobs_agent_id_agents",
        "background_jobs",
        type_="foreignkey",
    )
    op.drop_column("background_jobs", "agent_id")
