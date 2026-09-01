"""add assertion-level report counters"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("assertion_total", "assertion_passed", "assertion_failed", "assertion_error_count", "assertion_skipped"):
        op.add_column("reports", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    op.add_column("reports", sa.Column("assertion_success_rate", sa.Numeric(5, 2), nullable=False, server_default="0"))


def downgrade() -> None:
    for name in ("assertion_success_rate", "assertion_skipped", "assertion_error_count", "assertion_failed", "assertion_passed", "assertion_total"):
        op.drop_column("reports", name)
