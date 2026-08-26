"""scope node overrides by suite

Revision ID: b6d3f4a29c10
Revises: 67bf4a60ab85
Create Date: 2026-08-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6d3f4a29c10"
down_revision: Union[str, Sequence[str], None] = "67bf4a60ab85"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Require suite context for case step/assertion overrides."""
    # 当前项目尚未上线，不保留旧的无套件语境覆盖；这类数据无法安全判断原所属套件。
    op.execute(
        "DELETE FROM app_profile_node_overrides "
        "WHERE target_type IN ('step', 'assertion') AND suite_id IS NULL"
    )
    op.drop_constraint(
        "ck_profile_node_override_shape",
        "app_profile_node_overrides",
        type_="check",
    )
    op.drop_index("uq_profile_node_override_active", table_name="app_profile_node_overrides")
    op.create_check_constraint(
        "ck_profile_node_override_shape",
        "app_profile_node_overrides",
        "(target_type IN ('step', 'assertion') AND suite_id IS NOT NULL AND case_id IS NOT NULL) "
        "OR (target_type = 'suite_step' AND suite_id IS NOT NULL AND case_id IS NULL)",
    )
    op.create_index(
        "uq_profile_node_override_active",
        "app_profile_node_overrides",
        ["profile_id", "suite_id", "target_type", "case_id", "node_key"],
        unique=True,
        postgresql_where=sa.text(
            "target_type IN ('step', 'assertion') AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    """Restore the former case-only override shape."""
    op.execute(
        "DELETE FROM app_profile_node_overrides "
        "WHERE target_type IN ('step', 'assertion') AND suite_id IS NOT NULL"
    )
    op.drop_index("uq_profile_node_override_active", table_name="app_profile_node_overrides")
    op.drop_constraint(
        "ck_profile_node_override_shape",
        "app_profile_node_overrides",
        type_="check",
    )
    op.create_check_constraint(
        "ck_profile_node_override_shape",
        "app_profile_node_overrides",
        "(target_type IN ('step', 'assertion') AND suite_id IS NULL AND case_id IS NOT NULL) "
        "OR (target_type = 'suite_step' AND suite_id IS NOT NULL AND case_id IS NULL)",
    )
    op.create_index(
        "uq_profile_node_override_active",
        "app_profile_node_overrides",
        ["profile_id", "target_type", "case_id", "node_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND suite_id IS NULL"),
    )
