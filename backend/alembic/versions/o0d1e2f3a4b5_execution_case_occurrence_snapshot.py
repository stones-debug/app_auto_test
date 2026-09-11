"""persist suite-case occurrence identity in execution snapshots"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "o0d1e2f3a4b5"
down_revision: str | Sequence[str] | None = "n9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "execution_cases",
        sa.Column("suite_case_id_snapshot", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_cases", "suite_case_id_snapshot")
