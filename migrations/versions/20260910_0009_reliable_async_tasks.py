"""增加可恢复异步任务、事务事件、调度与社交预算。

Revision ID: 20260910_0009
Revises: 20260910_0008
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0009"
down_revision: str | Sequence[str] | None = "20260910_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建以 PostgreSQL 为真相源的 Inbox/Outbox、任务和主动行为表。"""
    op.create_table(
        "inbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'dead_letter', 'canceled')",
            name="ck_inbox_events_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_inbox_events_job"),
        sa.UniqueConstraint("tenant_id", "event_key", name="uq_inbox_events_tenant_key"),
    )
    op.create_index(
        "ix_inbox_events_status",
        "inbox_events",
        ["tenant_id", "status", "received_at"],
    )

    op.create_table(
        "background_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("queue", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deduplication_key", sa.String(length=255), nullable=False),
        sa.Column("source_inbox_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_seconds", sa.Integer(), nullable=False),
        sa.Column("retry_base_seconds", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_summary", sa.String(length=500), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("replayed_from_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('reflection', 'episode_consolidation', 'memory_extraction', "
            "'embedding_rebuild', 'relationship_update', 'scheduled_action')",
            name="ck_background_jobs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retrying', 'succeeded', 'failed', "
            "'dead_letter', 'canceled')",
            name="ck_background_jobs_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20 "
            "AND attempt_count <= max_attempts",
            name="ck_background_jobs_attempts",
        ),
        sa.CheckConstraint(
            "lease_seconds BETWEEN 1 AND 86400 AND retry_base_seconds BETWEEN 1 AND 86400",
            name="ck_background_jobs_timing",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_background_jobs_lease",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["replayed_from_id"], ["background_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_inbox_id"], ["inbox_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "deduplication_key", name="uq_background_jobs_deduplication"
        ),
        sa.UniqueConstraint("source_inbox_id", name="uq_background_jobs_source_inbox"),
    )
    op.create_foreign_key(
        "fk_inbox_events_job",
        "inbox_events",
        "background_jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "ix_background_jobs_queue_due",
        "background_jobs",
        ["status", "queue", "available_at"],
    )
    op.create_index(
        "ix_background_jobs_tenant_created",
        "background_jobs",
        ["tenant_id", "created_at"],
    )
    op.create_index("ix_background_jobs_lease", "background_jobs", ["status", "lease_expires_at"])

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'retrying', "
            "'dead_letter', 'canceled')",
            name="ck_outbox_events_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_outbox_events_attempts",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_outbox_events_lease",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["background_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_events_due", "outbox_events", ["status", "available_at"])
    op.create_index("ix_outbox_events_job", "outbox_events", ["tenant_id", "job_id", "created_at"])

    op.create_table(
        "job_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_summary", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'timed_out', 'canceled')",
            name="ck_job_attempts_status",
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_job_attempts_number"),
        sa.ForeignKeyConstraint(["job_id"], ["background_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempts_number"),
    )
    op.create_index(
        "ix_job_attempts_tenant_job",
        "job_attempts",
        ["tenant_id", "job_id", "started_at"],
    )

    op.create_table(
        "scheduled_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("social_cost", sa.Integer(), nullable=False),
        sa.Column("decision_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('follow_up', 'proactive_message', 'reflection')",
            name="ck_scheduled_actions_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched', 'completed', 'suppressed', "
            "'canceled', 'expired', 'failed')",
            name="ck_scheduled_actions_status",
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 1", name="ck_scheduled_actions_score"
        ),
        sa.CheckConstraint("social_cost BETWEEN 0 AND 20", name="ck_scheduled_actions_social_cost"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["background_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_scheduled_actions_job"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_scheduled_actions_idempotency"
        ),
    )
    op.create_index(
        "ix_scheduled_actions_due",
        "scheduled_actions",
        ["tenant_id", "status", "scheduled_for"],
    )
    op.create_index(
        "ix_scheduled_actions_user",
        "scheduled_actions",
        ["tenant_id", "agent_id", "user_id", "created_at"],
    )

    op.create_table(
        "social_budget_usages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("budget_date", sa.Date(), nullable=False),
        sa.Column("scheduled_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cost BETWEEN 0 AND 20", name="ck_social_budget_usages_cost"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["scheduled_action_id"], ["scheduled_actions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scheduled_action_id", name="uq_social_budget_usages_action"),
    )
    op.create_index(
        "ix_social_budget_usages_daily",
        "social_budget_usages",
        ["tenant_id", "agent_id", "user_id", "budget_date"],
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("queues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["current_job_id"], ["background_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index("ix_worker_heartbeats_seen", "worker_heartbeats", ["last_seen_at"])


def downgrade() -> None:
    """按依赖顺序移除异步任务与主动行为数据结构。"""
    op.drop_index("ix_worker_heartbeats_seen", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_social_budget_usages_daily", table_name="social_budget_usages")
    op.drop_table("social_budget_usages")
    op.drop_index("ix_scheduled_actions_user", table_name="scheduled_actions")
    op.drop_index("ix_scheduled_actions_due", table_name="scheduled_actions")
    op.drop_table("scheduled_actions")
    op.drop_index("ix_job_attempts_tenant_job", table_name="job_attempts")
    op.drop_table("job_attempts")
    op.drop_index("ix_outbox_events_job", table_name="outbox_events")
    op.drop_index("ix_outbox_events_due", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_background_jobs_lease", table_name="background_jobs")
    op.drop_index("ix_background_jobs_tenant_created", table_name="background_jobs")
    op.drop_index("ix_background_jobs_queue_due", table_name="background_jobs")
    op.drop_constraint("fk_inbox_events_job", "inbox_events", type_="foreignkey")
    op.drop_table("background_jobs")
    op.drop_index("ix_inbox_events_status", table_name="inbox_events")
    op.drop_table("inbox_events")
