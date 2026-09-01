"""add parent relationship to element page groups

Revision ID: b7c2d3e4f5a6
Revises: 9f6c2d1a4b80
Create Date: 2026-09-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "9f6c2d1a4b80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "element_page_groups",
        sa.Column("parent_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "element_page_groups_parent_id_fkey",
        "element_page_groups",
        "element_page_groups",
        ["parent_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "element_page_groups_parent_id_fkey",
        "element_page_groups",
        type_="foreignkey",
    )
    op.drop_column("element_page_groups", "parent_id")
