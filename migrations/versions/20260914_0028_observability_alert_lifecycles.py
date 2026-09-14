"""增加 Agent 请求归属与通用可观测告警生命周期。

Revision ID: 20260914_0028
Revises: 20260914_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_0028"
down_revision: str | None = "20260914_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_request_metrics",
        sa.Column("agent_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_api_request_metrics_agent_id_agents",
        "api_request_metrics",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_api_metrics_tenant_agent_occurred",
        "api_request_metrics",
        ["tenant_id", "agent_id", "occurred_at"],
    )
    op.create_table(
        "observability_alert_lifecycles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=24), nullable=False),
        sa.Column("first_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_level", sa.Integer(), nullable=False),
        sa.Column("last_escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('active', 'resolved')",
            name="ck_observability_alert_lifecycles_status",
        ),
        sa.CheckConstraint(
            "severity IN ('warning', 'critical')",
            name="ck_observability_alert_lifecycles_severity",
        ),
        sa.CheckConstraint(
            "occurrences >= 1",
            name="ck_observability_alert_lifecycles_occurrences",
        ),
        sa.CheckConstraint(
            "escalation_level >= 0 AND escalation_level <= 3",
            name="ck_observability_alert_lifecycles_escalation_level",
        ),
        sa.CheckConstraint(
            "recovery_duration_seconds IS NULL OR recovery_duration_seconds >= 0",
            name="ck_observability_alert_lifecycles_recovery_duration",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND resolved_at IS NULL "
            "AND recovery_duration_seconds IS NULL) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL "
            "AND recovery_duration_seconds IS NOT NULL)",
            name="ck_observability_alert_lifecycles_resolution",
        ),
    )
    op.create_index(
        "uq_observability_alert_lifecycles_active_key",
        "observability_alert_lifecycles",
        ["tenant_id", "agent_id", "source_type", "source_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_observability_alert_lifecycles_tenant_agent_time",
        "observability_alert_lifecycles",
        ["tenant_id", "agent_id", "updated_at"],
    )
    op.create_index(
        "ix_observability_alert_lifecycles_tenant_status",
        "observability_alert_lifecycles",
        ["tenant_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_observability_alert_lifecycles_tenant_status",
        table_name="observability_alert_lifecycles",
    )
    op.drop_index(
        "ix_observability_alert_lifecycles_tenant_agent_time",
        table_name="observability_alert_lifecycles",
    )
    op.drop_index(
        "uq_observability_alert_lifecycles_active_key",
        table_name="observability_alert_lifecycles",
    )
    op.drop_table("observability_alert_lifecycles")
    op.drop_index("ix_api_metrics_tenant_agent_occurred", table_name="api_request_metrics")
    op.drop_constraint(
        "fk_api_request_metrics_agent_id_agents",
        "api_request_metrics",
        type_="foreignkey",
    )
    op.drop_column("api_request_metrics", "agent_id")
