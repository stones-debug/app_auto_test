"""cascade execution node cleanup with its execution parent"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "execution_nodes_execution_suite_id_fkey", "execution_nodes", type_="foreignkey"
    )
    op.drop_constraint(
        "execution_nodes_execution_case_id_fkey", "execution_nodes", type_="foreignkey"
    )
    op.create_foreign_key(
        "execution_nodes_execution_suite_id_fkey",
        "execution_nodes",
        "execution_suites",
        ["execution_suite_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "execution_nodes_execution_case_id_fkey",
        "execution_nodes",
        "execution_cases",
        ["execution_case_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "execution_nodes_execution_case_id_fkey", "execution_nodes", type_="foreignkey"
    )
    op.drop_constraint(
        "execution_nodes_execution_suite_id_fkey", "execution_nodes", type_="foreignkey"
    )
    op.create_foreign_key(
        "execution_nodes_execution_suite_id_fkey",
        "execution_nodes",
        "execution_suites",
        ["execution_suite_id"],
        ["id"],
    )
    op.create_foreign_key(
        "execution_nodes_execution_case_id_fkey",
        "execution_nodes",
        "execution_cases",
        ["execution_case_id"],
        ["id"],
    )
