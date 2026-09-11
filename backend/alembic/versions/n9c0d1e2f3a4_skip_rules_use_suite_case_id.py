"""use suite_case occurrence identity for profile skip rules"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "n9c0d1e2f3a4"
down_revision: str | Sequence[str] | None = "m8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 开发环境尚未上线。旧规则只保存 suite_id + case_id，无法在重复编排时
    # 无歧义地选择 occurrence，因此清空而不是静默映射到某个编排项。
    op.execute("DELETE FROM app_profile_skip_rules")
    for index_name in (
        "uq_profile_skip_case_active",
        "uq_profile_skip_node_active",
        "idx_profile_skip_case",
    ):
        op.drop_index(index_name, table_name="app_profile_skip_rules")
    op.drop_constraint("ck_profile_skip_target_shape", "app_profile_skip_rules", type_="check")
    op.drop_column("app_profile_skip_rules", "case_id")
    op.add_column(
        "app_profile_skip_rules",
        sa.Column("suite_case_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_profile_skip_suite_case",
        "app_profile_skip_rules",
        "test_suite_cases",
        ["suite_case_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_profile_skip_target_shape",
        "app_profile_skip_rules",
        "(target_type = 'suite' AND suite_id IS NOT NULL AND suite_case_id IS NULL AND node_key IS NULL)"
        " OR (target_type = 'case' AND suite_id IS NULL AND suite_case_id IS NOT NULL AND node_key IS NULL)"
        " OR (target_type = 'suite_step' AND suite_id IS NOT NULL AND suite_case_id IS NULL AND node_key IS NOT NULL)"
        " OR (target_type IN ('step', 'assertion') AND suite_id IS NULL AND suite_case_id IS NOT NULL AND node_key IS NOT NULL)",
    )
    op.create_index(
        "uq_profile_skip_case_active",
        "app_profile_skip_rules",
        ["profile_id", "suite_case_id"],
        unique=True,
        postgresql_where=sa.text("target_type = 'case' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_profile_skip_node_active",
        "app_profile_skip_rules",
        ["profile_id", "suite_case_id", "target_type", "node_key"],
        unique=True,
        postgresql_where=sa.text(
            "target_type IN ('step', 'assertion') AND deleted_at IS NULL"
        ),
    )
    op.create_index(
        "idx_profile_skip_suite_case",
        "app_profile_skip_rules",
        ["suite_case_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.add_column(
        "execution_exclusions",
        sa.Column("suite_case_id_snapshot", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_execution_exclusions_suite_case",
        "execution_exclusions",
        ["suite_case_id_snapshot"],
        postgresql_where=sa.text("suite_case_id_snapshot IS NOT NULL"),
    )


def downgrade() -> None:
    # 新旧 occurrence 身份不可无损互转，回退时同样清除规则数据。
    op.execute("DELETE FROM app_profile_skip_rules")
    op.drop_index("idx_execution_exclusions_suite_case", table_name="execution_exclusions")
    op.drop_column("execution_exclusions", "suite_case_id_snapshot")
    op.drop_index("idx_profile_skip_suite_case", table_name="app_profile_skip_rules")
    op.drop_index("uq_profile_skip_node_active", table_name="app_profile_skip_rules")
    op.drop_index("uq_profile_skip_case_active", table_name="app_profile_skip_rules")
    op.drop_constraint("ck_profile_skip_target_shape", "app_profile_skip_rules", type_="check")
    op.drop_constraint("fk_profile_skip_suite_case", "app_profile_skip_rules", type_="foreignkey")
    op.drop_column("app_profile_skip_rules", "suite_case_id")
    op.add_column(
        "app_profile_skip_rules",
        sa.Column("case_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_profile_skip_case",
        "app_profile_skip_rules",
        "test_cases",
        ["case_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_profile_skip_target_shape",
        "app_profile_skip_rules",
        "(target_type = 'suite' AND suite_id IS NOT NULL AND case_id IS NULL AND node_key IS NULL)"
        " OR (target_type = 'case' AND suite_id IS NOT NULL AND case_id IS NOT NULL AND node_key IS NULL)"
        " OR (target_type = 'suite_step' AND suite_id IS NOT NULL AND case_id IS NULL AND node_key IS NOT NULL)"
        " OR (target_type IN ('step', 'assertion') AND suite_id IS NOT NULL AND case_id IS NOT NULL AND node_key IS NOT NULL)",
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
    op.create_index(
        "idx_profile_skip_case",
        "app_profile_skip_rules",
        ["case_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
