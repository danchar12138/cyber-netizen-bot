"""增加拟人回放、质量门与匿名人工盲评。

Revision ID: 20260910_0014
Revises: 20260910_0013
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0014"
down_revision: str | Sequence[str] | None = "20260910_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建立版本化评测集、冻结回放结果和来源盲化的人工评审。"""
    op.create_table(
        "evaluation_suites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("minimum_pass_rate", sa.Float(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'superseded')",
            name="ck_evaluation_suites_status",
        ),
        sa.CheckConstraint(
            "minimum_pass_rate BETWEEN 0 AND 100",
            name="ck_evaluation_suites_pass_rate",
        ),
        sa.CheckConstraint(
            "max_output_tokens BETWEEN 64 AND 32768",
            name="ck_evaluation_suites_output_tokens",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "key",
            "version",
            name="uq_evaluation_suites_version",
        ),
    )
    op.create_index(
        "ix_evaluation_suites_lookup",
        "evaluation_suites",
        ["tenant_id", "agent_id", "key", "status"],
    )
    op.create_index(
        "uq_evaluation_suites_published",
        "evaluation_suites",
        ["tenant_id", "agent_id", "key"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "evaluation_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_key", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("expected_action", sa.String(length=32), nullable=False),
        sa.Column("reference_response", sa.Text(), nullable=True),
        sa.Column(
            "required_phrases",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "forbidden_phrases",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.CheckConstraint("sort_order > 0", name="ck_evaluation_cases_sort_order"),
        sa.CheckConstraint(
            "expected_action IN ('reply', 'ask', 'wait', 'no_reply', 'tool')",
            name="ck_evaluation_cases_action",
        ),
        sa.ForeignKeyConstraint(["suite_id"], ["evaluation_suites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("suite_id", "case_key", name="uq_evaluation_cases_key"),
        sa.UniqueConstraint("suite_id", "sort_order", name="uq_evaluation_cases_order"),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("suite_key", sa.String(length=120), nullable=False),
        sa.Column("suite_version", sa.Integer(), nullable=False),
        sa.Column("suite_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("pass_rate", sa.Float(), nullable=False),
        sa.Column("gate_passed", sa.Boolean(), nullable=False),
        sa.Column("minimum_pass_rate", sa.Float(), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("persona_version", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("model_route_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('completed', 'failed')", name="ck_evaluation_runs_status"),
        sa.CheckConstraint(
            "passed >= 0 AND total > 0 AND passed <= total",
            name="ck_evaluation_runs_counts",
        ),
        sa.CheckConstraint(
            "pass_rate BETWEEN 0 AND 100 AND minimum_pass_rate BETWEEN 0 AND 100",
            name="ck_evaluation_runs_rates",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost_microusd >= 0",
            name="ck_evaluation_runs_usage",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["suite_id"], ["evaluation_suites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_runs_tenant_created",
        "evaluation_runs",
        ["tenant_id", "agent_id", "created_at"],
    )
    op.create_table(
        "evaluation_case_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_key", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("expected_action", sa.String(length=32), nullable=False),
        sa.Column("actual_action", sa.String(length=32), nullable=False),
        sa.Column("candidate_response", sa.Text(), nullable=True),
        sa.Column("reference_response", sa.Text(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("latency_ms >= 0", name="ck_evaluation_results_latency"),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "case_key", name="uq_evaluation_results_case"),
    )
    op.create_index("ix_evaluation_results_run", "evaluation_case_results", ["run_id"])
    op.create_table(
        "blind_review_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_is_a", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["result_id"], ["evaluation_case_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "result_id", "reviewer_id", name="uq_blind_assignments_result_reviewer"
        ),
    )
    op.create_index(
        "ix_blind_assignments_reviewer",
        "blind_review_assignments",
        ["tenant_id", "agent_id", "reviewer_id"],
    )
    op.create_table(
        "blind_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preference", sa.String(length=24), nullable=False),
        sa.Column(
            "candidate_score",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "reference_score",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "preference IN ('candidate', 'reference', 'tie')",
            name="ck_blind_reviews_preference",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["blind_review_assignments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["result_id"], ["evaluation_case_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id"),
    )
    op.create_index(
        "ix_blind_reviews_tenant_created",
        "blind_reviews",
        ["tenant_id", "agent_id", "created_at"],
    )


def downgrade() -> None:
    """按引用关系逆序移除拟人评测与盲评数据。"""
    op.drop_index("ix_blind_reviews_tenant_created", table_name="blind_reviews")
    op.drop_table("blind_reviews")
    op.drop_index("ix_blind_assignments_reviewer", table_name="blind_review_assignments")
    op.drop_table("blind_review_assignments")
    op.drop_index("ix_evaluation_results_run", table_name="evaluation_case_results")
    op.drop_table("evaluation_case_results")
    op.drop_index("ix_evaluation_runs_tenant_created", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluation_cases")
    op.drop_index("uq_evaluation_suites_published", table_name="evaluation_suites")
    op.drop_index("ix_evaluation_suites_lookup", table_name="evaluation_suites")
    op.drop_table("evaluation_suites")
