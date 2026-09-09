"""增加拟人认知资源、状态快照与运行回放。

Revision ID: 20260909_0007
Revises: 20260909_0006
创建日期：2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260909_0007"
down_revision: str | Sequence[str] | None = "20260909_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建认知资源版本、人格状态、阶段、候选与模型调用表。"""
    op.drop_constraint("ck_messages_status", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_status",
        "messages",
        "status IN ('received', 'processing', 'streaming', 'completed', "
        "'suppressed', 'cancelled', 'failed')",
    )
    op.add_column(
        "agent_runs",
        sa.Column("policy_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "agent_runs",
        sa.Column("model_route_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_table(
        "cognition_resource_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('persona', 'prompt', 'model_profile', 'model_route', 'tool', 'policy')",
            name="ck_cognition_resource_versions_kind",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'superseded')",
            name="ck_cognition_resource_versions_status",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "kind",
            "key",
            "version",
            name="uq_cognition_resource_versions_number",
        ),
    )
    op.create_index(
        "ix_cognition_resource_versions_lookup",
        "cognition_resource_versions",
        ["tenant_id", "agent_id", "kind", "key", "status"],
    )
    op.create_index(
        "uq_cognition_resource_versions_published",
        "cognition_resource_versions",
        ["tenant_id", "agent_id", "kind", "key"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "persona_state_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("persona_version", sa.Integer(), nullable=False),
        sa.Column("valence", sa.Float(), nullable=False),
        sa.Column("arousal", sa.Float(), nullable=False),
        sa.Column("social_energy", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("valence BETWEEN -1 AND 1", name="ck_persona_state_valence"),
        sa.CheckConstraint("arousal BETWEEN 0 AND 1", name="ck_persona_state_arousal"),
        sa.CheckConstraint("social_energy BETWEEN 0 AND 1", name="ck_persona_state_social_energy"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        "ix_persona_state_conversation_created",
        "persona_state_snapshots",
        ["tenant_id", "agent_id", "conversation_id", "created_at"],
    )
    op.create_table(
        "run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence > 0", name="ck_run_steps_sequence"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_steps_sequence"),
    )
    op.create_index("ix_run_steps_tenant_run", "run_steps", ["tenant_id", "run_id"])
    op.create_table(
        "action_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason_summary", sa.Text(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence > 0", name="ck_action_candidates_sequence"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_action_candidates_confidence"),
        sa.CheckConstraint(
            "action IN ('reply', 'ask', 'wait', 'no_reply', 'tool')",
            name="ck_action_candidates_action",
        ),
        sa.CheckConstraint(
            "risk_level IN ('none', 'low', 'medium', 'high')",
            name="ck_action_candidates_risk",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_action_candidates_sequence"),
    )
    op.create_index("ix_action_candidates_tenant_run", "action_candidates", ["tenant_id", "run_id"])
    op.create_table(
        "model_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt > 0", name="ck_model_invocations_attempt"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'timed_out')",
            name="ck_model_invocations_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "purpose", "attempt", name="uq_model_invocations_attempt"),
    )
    op.create_index("ix_model_invocations_tenant_run", "model_invocations", ["tenant_id", "run_id"])


def downgrade() -> None:
    """按依赖关系逆序移除拟人认知数据。"""
    op.drop_index("ix_model_invocations_tenant_run", table_name="model_invocations")
    op.drop_table("model_invocations")
    op.drop_index("ix_action_candidates_tenant_run", table_name="action_candidates")
    op.drop_table("action_candidates")
    op.drop_index("ix_run_steps_tenant_run", table_name="run_steps")
    op.drop_table("run_steps")
    op.drop_index("ix_persona_state_conversation_created", table_name="persona_state_snapshots")
    op.drop_table("persona_state_snapshots")
    op.drop_index(
        "uq_cognition_resource_versions_published", table_name="cognition_resource_versions"
    )
    op.drop_index("ix_cognition_resource_versions_lookup", table_name="cognition_resource_versions")
    op.drop_table("cognition_resource_versions")
    op.drop_column("agent_runs", "model_route_version")
    op.drop_column("agent_runs", "policy_version")
    op.execute("UPDATE messages SET status = 'completed' WHERE status = 'suppressed'")
    op.drop_constraint("ck_messages_status", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_status",
        "messages",
        "status IN ('received', 'processing', 'streaming', 'completed', 'cancelled', 'failed')",
    )
