"""scope profile skip rules by suite

Revision ID: 9c42e1e3ab77
Revises: c41d8e9426a1
Create Date: 2026-08-25 20:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c42e1e3ab77"
down_revision: str | Sequence[str] | None = "c41d8e9426a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 项目尚未上线，直接清除旧的无套件上下文规则，避免保留歧义数据。
    op.execute(
        """
        DELETE FROM app_profile_skip_rules
         WHERE target_type IN ('case', 'step', 'assertion')
        """
    )
    op.drop_index("uq_profile_skip_case_active", table_name="app_profile_skip_rules")
    op.drop_index("uq_profile_skip_node_active", table_name="app_profile_skip_rules")
    op.drop_constraint(
        "ck_profile_skip_target_shape",
        "app_profile_skip_rules",
        type_="check",
    )
    op.create_check_constraint(
        "ck_profile_skip_target_shape",
        "app_profile_skip_rules",
        "(target_type = 'suite' AND suite_id IS NOT NULL AND case_id IS NULL AND node_key IS NULL)"
        " OR (target_type = 'case' AND suite_id IS NOT NULL"
        " AND case_id IS NOT NULL AND node_key IS NULL)"
        " OR (target_type IN ('step', 'assertion') AND suite_id IS NOT NULL"
        " AND case_id IS NOT NULL AND node_key IS NOT NULL)",
    )
    op.create_index(
        "uq_profile_skip_case_active",
        "app_profile_skip_rules",
        ["profile_id", "suite_id", "case_id"],
        unique=True,
        postgresql_where=sa.text("target_type = 'case' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_profile_skip_node_active",
        "app_profile_skip_rules",
        ["profile_id", "suite_id", "target_type", "case_id", "node_key"],
        unique=True,
        postgresql_where=sa.text(
            "target_type IN ('step', 'assertion') AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    # 新模型中的套件级规则无法无损映射到旧的全局规则，开发环境直接清除。
    op.execute(
        """
        DELETE FROM app_profile_skip_rules
         WHERE target_type IN ('case', 'step', 'assertion')
        """
    )
    op.drop_index("uq_profile_skip_node_active", table_name="app_profile_skip_rules")
    op.drop_index("uq_profile_skip_case_active", table_name="app_profile_skip_rules")
    op.drop_constraint(
        "ck_profile_skip_target_shape",
        "app_profile_skip_rules",
        type_="check",
    )
    op.create_check_constraint(
        "ck_profile_skip_target_shape",
        "app_profile_skip_rules",
        "(target_type = 'suite' AND suite_id IS NOT NULL AND case_id IS NULL AND node_key IS NULL)"
        " OR (target_type = 'case' AND suite_id IS NULL AND case_id IS NOT NULL AND node_key IS NULL)"
        " OR (target_type IN ('step', 'assertion') AND suite_id IS NULL"
        " AND case_id IS NOT NULL AND node_key IS NOT NULL)",
    )
    op.create_index(
        "uq_profile_skip_case_active",
        "app_profile_skip_rules",
        ["profile_id", "case_id"],
        unique=True,
        postgresql_where=sa.text("target_type = 'case' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_profile_skip_node_active",
        "app_profile_skip_rules",
        ["profile_id", "target_type", "case_id", "node_key"],
        unique=True,
        postgresql_where=sa.text(
            "target_type IN ('step', 'assertion') AND deleted_at IS NULL"
        ),
    )
