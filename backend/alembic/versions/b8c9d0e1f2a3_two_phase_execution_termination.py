"""persist two-phase timeout termination state"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("timeout_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("executions", sa.Column("termination_reason", sa.String(length=30), nullable=True))
    op.add_column(
        "executions",
        sa.Column("stop_command_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_executions_termination_reason",
        "executions",
        "termination_reason IS NULL OR termination_reason IN ('user_stop','timeout')",
    )
    op.create_index(
        "idx_executions_timeout_stopping",
        "executions",
        ["status", "timeout_requested_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_executions_timeout_stopping", table_name="executions")
    op.drop_constraint("ck_executions_termination_reason", "executions", type_="check")
    op.drop_column("executions", "stop_command_sent_at")
    op.drop_column("executions", "termination_reason")
    op.drop_column("executions", "timeout_requested_at")
