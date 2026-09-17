"""增加告警建议替代动作与候选阈值回放所需的安全元数据。

Revision ID: 20260916_0035
Revises: 20260915_0034
创建日期：2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0035"
down_revision: str | Sequence[str] | None = "20260915_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为历史反馈增加可选的稳定替代动作标签。"""
    op.add_column(
        "observability_alert_recommendation_feedback",
        sa.Column("alternative_action", sa.String(length=24), nullable=True),
    )
    op.create_check_constraint(
        "ck_observability_recommendation_feedback_alternative_action",
        "observability_alert_recommendation_feedback",
        "alternative_action IS NULL OR alternative_action IN "
        "('acknowledge', 'suppress', 'observe')",
    )


def downgrade() -> None:
    """删除替代动作标签字段与约束。"""
    op.drop_constraint(
        "ck_observability_recommendation_feedback_alternative_action",
        "observability_alert_recommendation_feedback",
        type_="check",
    )
    op.drop_column("observability_alert_recommendation_feedback", "alternative_action")
