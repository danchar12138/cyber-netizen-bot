"""增加渠道告警多级升级状态。

Revision ID: 20260914_0027
Revises: 20260914_0026
创建日期：2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_0027"
down_revision: str | None = "20260914_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为现有生命周期补充当前等级和最近升级时间。"""
    op.add_column(
        "channel_alert_lifecycles",
        sa.Column("escalation_level", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "channel_alert_lifecycles",
        sa.Column("last_escalated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_channel_alert_lifecycles_escalation_level",
        "channel_alert_lifecycles",
        "escalation_level >= 0 AND escalation_level <= 3",
    )
    op.execute(
        "UPDATE channel_alert_lifecycles "
        "SET escalation_level = 1, last_escalated_at = escalated_at "
        "WHERE escalated_at IS NOT NULL"
    )
    op.alter_column("channel_alert_lifecycles", "escalation_level", server_default=None)


def downgrade() -> None:
    """移除多级升级状态并保留原首次升级时间。"""
    op.drop_constraint(
        "ck_channel_alert_lifecycles_escalation_level",
        "channel_alert_lifecycles",
        type_="check",
    )
    op.drop_column("channel_alert_lifecycles", "last_escalated_at")
    op.drop_column("channel_alert_lifecycles", "escalation_level")
