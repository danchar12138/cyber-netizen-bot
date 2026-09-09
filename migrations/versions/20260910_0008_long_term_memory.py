"""增加长期记忆、来源、关系与向量索引。

Revision ID: 20260910_0008
Revises: 20260909_0007
创建日期：2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0008"
down_revision: str | Sequence[str] | None = "20260909_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建可追溯、可纠正、可遗忘且支持混合召回的记忆数据结构。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_message_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'consolidated')",
            name="ck_episodes_status",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_episodes_scope_started",
        "episodes",
        ["tenant_id", "agent_id", "user_id", "started_at"],
    )

    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=24), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("emotional_weight", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.String(length=24), nullable=False),
        sa.Column("confirmation", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("embedding_version", sa.String(length=120), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('working', 'episodic', 'semantic', 'relational', "
            "'autobiographical', 'procedural')",
            name="ck_memories_kind",
        ),
        sa.CheckConstraint(
            "visibility IN ('user', 'agent', 'tenant')",
            name="ck_memories_visibility",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('normal', 'personal', 'sensitive', 'restricted')",
            name="ck_memories_sensitivity",
        ),
        sa.CheckConstraint(
            "confirmation IN ('unconfirmed', 'confirmed', 'disputed')",
            name="ck_memories_confirmation",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'forgotten')",
            name="ck_memories_status",
        ),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_memories_confidence"),
        sa.CheckConstraint("importance BETWEEN 0 AND 1", name="ck_memories_importance"),
        sa.CheckConstraint(
            "emotional_weight BETWEEN -1 AND 1",
            name="ck_memories_emotional_weight",
        ),
        sa.CheckConstraint(
            "(visibility <> 'user') OR user_id IS NOT NULL",
            name="ck_memories_user_visibility",
        ),
        sa.CheckConstraint(
            "(status = 'forgotten' AND content IS NULL) OR "
            "(status <> 'forgotten' AND content IS NOT NULL)",
            name="ck_memories_forgotten_content",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "lineage_id",
            "version",
            name="uq_memories_lineage_version",
        ),
    )
    op.create_index(
        "ix_memories_scope_event",
        "memories",
        ["tenant_id", "agent_id", "user_id", "status", "event_at"],
    )
    op.create_index("ix_memories_episode", "memories", ["episode_id"])
    op.create_index(
        "ix_memories_content_fts",
        "memories",
        [sa.text("to_tsvector('simple', coalesce(content, ''))")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_memories_content_trgm",
        "memories",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )

    op.create_table(
        "memory_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("is_verbatim", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('message', 'episode', 'user_statement', 'admin_correction', "
            "'reflection', 'import')",
            name="ck_memory_sources_kind",
        ),
        sa.CheckConstraint(
            "(NOT is_verbatim) OR excerpt IS NOT NULL",
            name="ck_memory_sources_verbatim_excerpt",
        ),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_sources_memory",
        "memory_sources",
        ["tenant_id", "memory_id", "created_at"],
    )

    op.create_table(
        "memory_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('related_to', 'conflicts_with', 'supersedes', 'derived_from')",
            name="ck_memory_links_kind",
        ),
        sa.CheckConstraint(
            "source_memory_id <> target_memory_id",
            name="ck_memory_links_distinct",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_memory_id"], ["memories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_memory_id"], ["memories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_memory_id",
            "target_memory_id",
            "kind",
            name="uq_memory_links_direction",
        ),
    )
    op.create_index(
        "ix_memory_links_source",
        "memory_links",
        ["tenant_id", "source_memory_id"],
    )
    op.create_index(
        "ix_memory_links_target",
        "memory_links",
        ["tenant_id", "target_memory_id"],
    )

    op.create_table(
        "memory_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding_version", sa.String(length=120), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(256), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("dimensions = 256", name="ck_memory_embeddings_dimensions"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "memory_id",
            "embedding_version",
            name="uq_memory_embeddings_version",
        ),
    )
    op.create_index(
        "uq_memory_embeddings_active",
        "memory_embeddings",
        ["memory_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_index(
        "ix_memory_embeddings_hnsw",
        "memory_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("affinity", sa.Float(), nullable=False),
        sa.Column("trust", sa.Float(), nullable=False),
        sa.Column("familiarity", sa.Float(), nullable=False),
        sa.Column("interaction_count", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("boundaries", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage IN ('stranger', 'acquaintance', 'familiar', 'trusted')",
            name="ck_relationships_stage",
        ),
        sa.CheckConstraint("affinity BETWEEN 0 AND 1", name="ck_relationships_affinity"),
        sa.CheckConstraint("trust BETWEEN 0 AND 1", name="ck_relationships_trust"),
        sa.CheckConstraint(
            "familiarity BETWEEN 0 AND 1",
            name="ck_relationships_familiarity",
        ),
        sa.CheckConstraint(
            "interaction_count >= 0",
            name="ck_relationships_interaction_count",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "user_id",
            name="uq_relationships_scope",
        ),
    )

    op.create_table(
        "relationship_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("affinity_delta", sa.Float(), nullable=False),
        sa.Column("trust_delta", sa.Float(), nullable=False),
        sa.Column("familiarity_delta", sa.Float(), nullable=False),
        sa.Column("evidence_memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "affinity_delta BETWEEN -1 AND 1",
            name="ck_relationship_events_affinity",
        ),
        sa.CheckConstraint(
            "trust_delta BETWEEN -1 AND 1",
            name="ck_relationship_events_trust",
        ),
        sa.CheckConstraint(
            "familiarity_delta BETWEEN -1 AND 1",
            name="ck_relationship_events_familiarity",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_memory_id"], ["memories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["relationship_id"], ["relationships.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_relationship_events_timeline",
        "relationship_events",
        ["tenant_id", "relationship_id", "created_at"],
    )

    op.create_table(
        "memory_index_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_embedding_version", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("processed_items", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_memory_index_jobs_status",
        ),
        sa.CheckConstraint(
            "total_items >= 0 AND processed_items >= 0 AND processed_items <= total_items",
            name="ck_memory_index_jobs_progress",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_index_jobs_scope",
        "memory_index_jobs",
        ["tenant_id", "agent_id", "created_at"],
    )


def downgrade() -> None:
    """按依赖关系逆序移除长期记忆数据；保留共享 PostgreSQL 扩展。"""
    op.drop_index("ix_memory_index_jobs_scope", table_name="memory_index_jobs")
    op.drop_table("memory_index_jobs")
    op.drop_index("ix_relationship_events_timeline", table_name="relationship_events")
    op.drop_table("relationship_events")
    op.drop_table("relationships")
    op.drop_index("ix_memory_embeddings_hnsw", table_name="memory_embeddings")
    op.drop_index("uq_memory_embeddings_active", table_name="memory_embeddings")
    op.drop_table("memory_embeddings")
    op.drop_index("ix_memory_links_target", table_name="memory_links")
    op.drop_index("ix_memory_links_source", table_name="memory_links")
    op.drop_table("memory_links")
    op.drop_index("ix_memory_sources_memory", table_name="memory_sources")
    op.drop_table("memory_sources")
    op.drop_index("ix_memories_content_trgm", table_name="memories")
    op.drop_index("ix_memories_content_fts", table_name="memories")
    op.drop_index("ix_memories_episode", table_name="memories")
    op.drop_index("ix_memories_scope_event", table_name="memories")
    op.drop_table("memories")
    op.drop_index("ix_episodes_scope_started", table_name="episodes")
    op.drop_table("episodes")
