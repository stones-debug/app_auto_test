"""align execution node uniqueness and payload checks with the V3 model"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("execution_nodes", sa.Column("description", sa.Text(), nullable=True))
    op.drop_index("uq_execution_nodes_case_order", table_name="execution_nodes")
    op.drop_index("uq_execution_nodes_suite_order", table_name="execution_nodes")
    op.create_index(
        "uq_execution_nodes_suite_order",
        "execution_nodes",
        ["execution_suite_id", "phase", "node_order"],
        unique=True,
        postgresql_where=sa.text(
            "execution_suite_id IS NOT NULL AND execution_case_id IS NULL"
        ),
    )
    op.create_index(
        "uq_execution_nodes_case_order",
        "execution_nodes",
        ["execution_case_id", "phase", "node_order"],
        unique=True,
        postgresql_where=sa.text(
            "execution_suite_id IS NULL AND execution_case_id IS NOT NULL"
        ),
    )
    op.create_check_constraint(
        "ck_execution_nodes_payload",
        "execution_nodes",
        "(kind = 'action' AND action IS NOT NULL AND assertion_type IS NULL) "
        "OR (kind = 'assertion' AND action IS NULL AND assertion_type IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_execution_nodes_max_wait",
        "execution_nodes",
        "max_wait_seconds IS NULL OR max_wait_seconds BETWEEN 0 AND 300",
    )


def downgrade() -> None:
    op.drop_column("execution_nodes", "description")
    op.drop_constraint("ck_execution_nodes_max_wait", "execution_nodes", type_="check")
    op.drop_constraint("ck_execution_nodes_payload", "execution_nodes", type_="check")
    op.drop_index("uq_execution_nodes_case_order", table_name="execution_nodes")
    op.drop_index("uq_execution_nodes_suite_order", table_name="execution_nodes")
    op.create_index(
        "uq_execution_nodes_suite_order",
        "execution_nodes",
        ["execution_suite_id", "node_order"],
        unique=True,
        postgresql_where=sa.text(
            "execution_suite_id IS NOT NULL AND execution_case_id IS NULL"
        ),
    )
    op.create_index(
        "uq_execution_nodes_case_order",
        "execution_nodes",
        ["execution_case_id", "node_order"],
        unique=True,
        postgresql_where=sa.text(
            "execution_suite_id IS NULL AND execution_case_id IS NOT NULL"
        ),
    )
