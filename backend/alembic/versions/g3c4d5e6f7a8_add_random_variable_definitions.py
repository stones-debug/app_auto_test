"""add variable kinds and random specifications"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "g3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("variables", sa.Column("kind", sa.String(length=20), nullable=False, server_default="fixed"))
    op.add_column("variables", sa.Column("spec", postgresql.JSONB(), nullable=True))
    op.create_check_constraint("ck_variables_kind", "variables", "kind IN ('fixed','random_integer','random_choice')")


def downgrade() -> None:
    op.drop_constraint("ck_variables_kind", "variables", type_="check")
    op.drop_column("variables", "spec")
    op.drop_column("variables", "kind")
