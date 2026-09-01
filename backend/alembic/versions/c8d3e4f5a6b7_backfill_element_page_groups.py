"""backfill named element pages as editable root groups

Revision ID: c8d3e4f5a6b7
Revises: b7c2d3e4f5a6
Create Date: 2026-09-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b7c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO element_page_groups (name, created_by, created_at, updated_at)
            SELECT DISTINCT btrim(page_name), NULL::integer, now()::timestamptz, now()::timestamptz
            FROM test_elements
            WHERE deleted_at IS NULL
              AND page_name IS NOT NULL
              AND btrim(page_name) <> ''
              AND btrim(page_name) <> '未分组'
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Legacy groups have no owner and are the rows introduced by this migration.
    op.execute(
        sa.text(
            """
            DELETE FROM element_page_groups
            WHERE created_by IS NULL
              AND parent_id IS NULL
              AND name IN (
                SELECT DISTINCT btrim(page_name)
                FROM test_elements
                WHERE deleted_at IS NULL
                  AND page_name IS NOT NULL
                  AND btrim(page_name) <> ''
                  AND btrim(page_name) <> '未分组'
              )
            """
        )
    )
