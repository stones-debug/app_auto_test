"""store historical sensitive execution variable names"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "k6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column(
            "sensitive_variable_names",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("executions", "sensitive_variable_names")
