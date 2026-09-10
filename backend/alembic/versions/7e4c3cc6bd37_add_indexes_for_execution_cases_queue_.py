"""add indexes for execution cases queue and agent heartbeat"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e4c3cc6bd37"
down_revision: str | Sequence[str] | None = "g3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 终态汇总/快照重建按 execution_id 过滤 execution_cases，此前只有
    # execution_suite_id 索引，大执行量下退化为序列扫描。
    op.create_index("idx_exec_cases_execution", "execution_cases", ["execution_id"], unique=False)
    # reclaim_stale_claimed 按 status='claimed' 扫描过期认领，补 partial 索引；
    # 原有 idx_queue_pending 只覆盖 pending。
    op.create_index(
        "idx_queue_claimed",
        "execution_queue",
        ["status", "claimed_at"],
        unique=False,
        postgresql_where=sa.text("status = 'claimed'"),
    )
    # agent_heartbeat_scan 按 status + last_heartbeat 过滤。
    op.create_index(
        "idx_agents_status_heartbeat", "agents", ["status", "last_heartbeat"], unique=False
    )


def downgrade() -> None:
    op.drop_index("idx_agents_status_heartbeat", table_name="agents")
    op.drop_index(
        "idx_queue_claimed",
        table_name="execution_queue",
        postgresql_where=sa.text("status = 'claimed'"),
    )
    op.drop_index("idx_exec_cases_execution", table_name="execution_cases")
