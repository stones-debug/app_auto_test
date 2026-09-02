"""add execution dispatch state machine"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column(
            "dispatch_state",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "executions",
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_executions_dispatch_state",
        "executions",
        "dispatch_state IN ('pending','reserved','dispatching','dispatched')",
    )

    op.drop_constraint("ck_devices_status", "devices", type_="check")
    op.create_check_constraint(
        "ck_devices_status",
        "devices",
        "status IN ('idle', 'busy', 'offline', 'unauthorized', 'error')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_devices_status", "devices", type_="check")
    op.create_check_constraint(
        "ck_devices_status",
        "devices",
        "status IN ('idle', 'busy', 'offline', 'error')",
    )
    op.drop_constraint("ck_executions_dispatch_state", "executions", type_="check")
    op.drop_column("executions", "dispatched_at")
    op.drop_column("executions", "dispatch_started_at")
    op.drop_column("executions", "dispatch_state")
