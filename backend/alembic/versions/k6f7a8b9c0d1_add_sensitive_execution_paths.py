"""store exact sensitive parameter paths in execution snapshots"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "j5e6f7a8b9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("execution_nodes", "execution_steps", "execution_assertions"):
        op.add_column(
            table,
            sa.Column(
                "sensitive_parameter_paths",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )


def downgrade() -> None:
    for table in ("execution_assertions", "execution_steps", "execution_nodes"):
        op.drop_column(table, "sensitive_parameter_paths")
