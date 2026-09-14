"""增加通知投递后台任务种类。

Revision ID: 20260913_0024
Revises: 20260913_0023
创建日期：2026-09-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260913_0024"
down_revision: str | None = "20260913_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展后台任务种类约束，允许通知进入可靠队列。"""
    op.drop_constraint("ck_background_jobs_kind", "background_jobs", type_="check")
    op.create_check_constraint(
        "ck_background_jobs_kind",
        "background_jobs",
        "kind IN ('reflection', 'episode_consolidation', 'memory_extraction', "
        "'embedding_rebuild', 'relationship_update', 'scheduled_action', "
        "'inbound_message', 'notification_delivery')",
    )


def downgrade() -> None:
    """回退通知任务种类约束；调用方需先清理通知任务。"""
    op.drop_constraint("ck_background_jobs_kind", "background_jobs", type_="check")
    op.create_check_constraint(
        "ck_background_jobs_kind",
        "background_jobs",
        "kind IN ('reflection', 'episode_consolidation', 'memory_extraction', "
        "'embedding_rebuild', 'relationship_update', 'scheduled_action', "
        "'inbound_message')",
    )
