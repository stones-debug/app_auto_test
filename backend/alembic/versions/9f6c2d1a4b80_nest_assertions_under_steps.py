"""nest assertions under execution steps

Revision ID: 9f6c2d1a4b80
Revises: 28157b8a3f8a
Create Date: 2026-08-28

当前系统仍处于开发阶段，本迁移不保留旧的用例级断言数据。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f6c2d1a4b80"
down_revision: str | Sequence[str] | None = "28157b8a3f8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM execution_assertions")
    op.drop_index("uq_exec_assertions_case_order", table_name="execution_assertions")
    op.drop_index("idx_exec_assertions_case", table_name="execution_assertions")
    op.drop_constraint(
        "fk_exec_assertions_case",
        "execution_assertions",
        type_="foreignkey",
    )
    op.add_column(
        "execution_assertions",
        sa.Column("execution_step_id", sa.Integer(), nullable=False),
    )
    op.create_foreign_key(
        "execution_assertions_execution_step_id_fkey",
        "execution_assertions",
        "execution_steps",
        ["execution_step_id"],
        ["id"],
    )
    op.drop_column("execution_assertions", "execution_case_id")
    op.create_index(
        "idx_exec_assertions_step", "execution_assertions", ["execution_step_id"]
    )
    op.create_index(
        "uq_exec_assertions_step_order",
        "execution_assertions",
        ["execution_step_id", "assertion_order"],
        unique=True,
    )
    op.drop_column("execution_cases", "assertions_snapshot")
    op.drop_column("test_cases", "assertions")


def downgrade() -> None:
    op.execute("DELETE FROM execution_assertions")
    op.add_column(
        "test_cases",
        sa.Column("assertions", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.add_column(
        "execution_cases",
        sa.Column(
            "assertions_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.drop_index("uq_exec_assertions_step_order", table_name="execution_assertions")
    op.drop_index("idx_exec_assertions_step", table_name="execution_assertions")
    op.drop_constraint(
        "execution_assertions_execution_step_id_fkey",
        "execution_assertions",
        type_="foreignkey",
    )
    op.add_column(
        "execution_assertions",
        sa.Column("execution_case_id", sa.Integer(), nullable=False),
    )
    op.create_foreign_key(
        "fk_exec_assertions_case",
        "execution_assertions",
        "execution_cases",
        ["execution_case_id"],
        ["id"],
    )
    op.drop_column("execution_assertions", "execution_step_id")
    op.create_index(
        "idx_exec_assertions_case", "execution_assertions", ["execution_case_id"]
    )
    op.create_index(
        "uq_exec_assertions_case_order",
        "execution_assertions",
        ["execution_case_id", "assertion_order"],
        unique=True,
    )
