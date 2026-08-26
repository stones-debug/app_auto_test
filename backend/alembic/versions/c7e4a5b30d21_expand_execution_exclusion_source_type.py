"""expand execution exclusion source type

Revision ID: c7e4a5b30d21
Revises: b6d3f4a29c10
Create Date: 2026-08-26 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7e4a5b30d21"
down_revision: Union[str, Sequence[str], None] = "b6d3f4a29c10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow the valid source type value ``empty_after_filter``."""
    op.alter_column(
        "execution_exclusions",
        "source_type",
        existing_type=sa.String(length=16),
        type_=sa.String(length=24),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Remove rows that cannot fit before restoring the former width."""
    op.execute(
        "DELETE FROM execution_exclusions WHERE length(source_type) > 16"
    )
    op.alter_column(
        "execution_exclusions",
        "source_type",
        existing_type=sa.String(length=24),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
