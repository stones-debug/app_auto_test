"""enforce execution node parent and phase consistency"""

from collections.abc import Sequence

from alembic import op

revision: str = "e0f1a2b3c4d5"
down_revision: str | Sequence[str] | None = "d9e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_execution_nodes_parent_phase",
        "execution_nodes",
        "(phase IN ('suite_setup','suite_teardown') AND execution_suite_id IS NOT NULL AND execution_case_id IS NULL) "
        "OR (phase IN ('case_setup','case_main','case_teardown') AND execution_suite_id IS NULL AND execution_case_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_execution_nodes_parent_phase", "execution_nodes", type_="check")
