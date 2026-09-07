"""allow repeated suite case occurrences"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "f2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("execution_exclusions", sa.Column("occurrence_order", sa.Integer(), nullable=True))
    op.drop_constraint("uq_execution_cases_suite_case", "execution_cases", type_="unique")
    op.drop_constraint("test_suite_cases_suite_id_case_id_key", "test_suite_cases", type_="unique")
    op.drop_index("idx_suite_cases_order", table_name="test_suite_cases")
    op.create_index(
        "idx_suite_cases_order_stable",
        "test_suite_cases",
        ["suite_id", "sort_order", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_column("execution_exclusions", "occurrence_order")
    op.drop_index("idx_suite_cases_order_stable", table_name="test_suite_cases")
    op.create_index("idx_suite_cases_order", "test_suite_cases", ["suite_id", "sort_order"], unique=False)
    op.create_unique_constraint(
        "test_suite_cases_suite_id_case_id_key",
        "test_suite_cases",
        ["suite_id", "case_id"],
    )
    op.create_unique_constraint(
        "uq_execution_cases_suite_case",
        "execution_cases",
        ["execution_suite_id", "case_id"],
    )
