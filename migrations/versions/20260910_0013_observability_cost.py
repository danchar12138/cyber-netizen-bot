"""增加安全请求指标与模型成本快照。

Revision ID: 20260910_0013
Revises: 20260910_0012
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0013"
down_revision: str | Sequence[str] | None = "20260910_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存不含正文与凭证的 API 性能指标及调用时冻结成本。"""
    op.add_column(
        "model_invocations",
        sa.Column(
            "estimated_cost_microusd",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_model_invocations_estimated_cost",
        "model_invocations",
        "estimated_cost_microusd >= 0",
    )
    op.create_index(
        "ix_model_invocations_tenant_created",
        "model_invocations",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_agent_runs_tenant_completed",
        "agent_runs",
        ["tenant_id", "completed_at"],
    )
    op.create_index(
        "ix_background_jobs_tenant_status_available",
        "background_jobs",
        ["tenant_id", "status", "available_at"],
    )
    op.create_table(
        "api_request_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method", sa.String(length=12), nullable=False),
        sa.Column("route", sa.String(length=255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("duration_ms >= 0", name="ck_api_metrics_duration"),
        sa.CheckConstraint("status_code BETWEEN 100 AND 599", name="ck_api_metrics_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_api_metrics_tenant_occurred",
        "api_request_metrics",
        ["tenant_id", "occurred_at"],
    )


def downgrade() -> None:
    """移除安全请求指标和模型成本快照。"""
    op.drop_index("ix_api_metrics_tenant_occurred", table_name="api_request_metrics")
    op.drop_table("api_request_metrics")
    op.drop_index("ix_background_jobs_tenant_status_available", table_name="background_jobs")
    op.drop_index("ix_agent_runs_tenant_completed", table_name="agent_runs")
    op.drop_index("ix_model_invocations_tenant_created", table_name="model_invocations")
    op.drop_constraint("ck_model_invocations_estimated_cost", "model_invocations", type_="check")
    op.drop_column("model_invocations", "estimated_cost_microusd")
