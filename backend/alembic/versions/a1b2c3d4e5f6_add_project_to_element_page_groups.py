"""associate new element page groups with projects"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = ("f1a2b3c4d5e6", "c4d5e6f7a8b9")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "element_page_groups",
        sa.Column("project_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "element_page_groups_project_id_fkey",
        "element_page_groups",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "element_page_groups_project_id_fkey",
        "element_page_groups",
        type_="foreignkey",
    )
    op.drop_column("element_page_groups", "project_id")
