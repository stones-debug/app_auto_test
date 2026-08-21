"""add users.is_admin + reports constraints

Revision ID: 6a7fb396ae24
Revises: 8d67e8abca27
Create Date: 2026-08-21 10:24:29.095917

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a7fb396ae24'
down_revision: Union[str, Sequence[str], None] = '8d67e8abca27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'is_admin')
