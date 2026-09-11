"""suite case variable overrides and occurrence-scoped node overrides

方案：套件与 APP 档案用例变量快捷展示及覆盖。

- ``test_suite_cases.variable_overrides``：每个编排项独立的变量覆盖。
- ``app_profile_node_overrides.suite_case_id``：步骤/断言覆盖绑定到具体编排项，
  解决同一用例在同一套件重复编排时的覆盖污染。

Revision ID: h4d5e6f7a8b9
Revises: d5f9d6696ee4
Create Date: 2026-09-11 10:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "h4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "d5f9d6696ee4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 套件用例编排项变量覆盖 ---
    op.add_column(
        "test_suite_cases",
        sa.Column(
            "variable_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_suite_cases_variable_overrides",
        "test_suite_cases",
        "jsonb_typeof(variable_overrides) = 'object'",
    )

    # --- 节点覆盖绑定到编排项（occurrence） ---
    op.add_column(
        "app_profile_node_overrides",
        sa.Column("suite_case_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_profile_node_override_suite_case",
        "app_profile_node_overrides",
        "test_suite_cases",
        ["suite_case_id"],
        ["id"],
    )
    # 开发环境：旧覆盖绑定到对应套件中排序最前的编排项；无有效编排关系的旧覆盖清理
    # （包含软删历史行，否则会违反新的 shape CHECK）。
    op.execute(
        "UPDATE app_profile_node_overrides AS o SET suite_case_id = m.id "
        "FROM (SELECT DISTINCT ON (suite_id, case_id) id, suite_id, case_id "
        "FROM test_suite_cases ORDER BY suite_id, case_id, sort_order, id) AS m "
        "WHERE o.target_type IN ('step', 'assertion') "
        "AND o.suite_id = m.suite_id AND o.case_id = m.case_id"
    )
    op.execute(
        "DELETE FROM app_profile_node_overrides "
        "WHERE target_type IN ('step', 'assertion') AND suite_case_id IS NULL"
    )
    op.drop_index("uq_profile_node_override_active", table_name="app_profile_node_overrides")
    op.drop_constraint(
        "ck_profile_node_override_shape", "app_profile_node_overrides", type_="check"
    )
    op.create_check_constraint(
        "ck_profile_node_override_shape",
        "app_profile_node_overrides",
        "(target_type IN ('step', 'assertion') AND suite_id IS NOT NULL AND case_id IS NOT NULL "
        "AND suite_case_id IS NOT NULL) "
        "OR (target_type = 'suite_step' AND suite_id IS NOT NULL AND case_id IS NULL "
        "AND suite_case_id IS NULL)",
    )
    op.create_index(
        "uq_profile_node_override_active",
        "app_profile_node_overrides",
        ["profile_id", "suite_case_id", "target_type", "node_key"],
        unique=True,
        postgresql_where=sa.text(
            "target_type IN ('step', 'assertion') AND deleted_at IS NULL"
        ),
    )
    op.create_index(
        "idx_profile_node_override_suite_case",
        "app_profile_node_overrides",
        ["suite_case_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND suite_case_id IS NOT NULL"),
    )

    # --- 批量节点覆盖审计动作 ---
    op.drop_constraint("ck_profile_audit_action", "app_profile_audit_logs", type_="check")
    op.create_check_constraint(
        "ck_profile_audit_action",
        "app_profile_audit_logs",
        "action IN ('profile_create', 'profile_update', 'profile_disable', 'release_create', "
        "'release_update', 'release_disable', 'skip_batch', 'restore_batch', "
        "'element_override_upsert', 'element_override_restore', 'variable_override_upsert', "
        "'variable_override_restore', 'node_override_upsert', 'node_override_restore', "
        "'node_override_batch')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_profile_audit_action", "app_profile_audit_logs", type_="check")
    op.create_check_constraint(
        "ck_profile_audit_action",
        "app_profile_audit_logs",
        "action IN ('profile_create', 'profile_update', 'profile_disable', 'release_create', "
        "'release_update', 'release_disable', 'skip_batch', 'restore_batch', "
        "'element_override_upsert', 'element_override_restore', 'variable_override_upsert', "
        "'variable_override_restore', 'node_override_upsert', 'node_override_restore')",
    )

    op.drop_index("idx_profile_node_override_suite_case", table_name="app_profile_node_overrides")
    op.drop_index("uq_profile_node_override_active", table_name="app_profile_node_overrides")
    op.drop_constraint(
        "ck_profile_node_override_shape", "app_profile_node_overrides", type_="check"
    )
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
    op.drop_constraint(
        "fk_profile_node_override_suite_case", "app_profile_node_overrides", type_="foreignkey"
    )
    op.drop_column("app_profile_node_overrides", "suite_case_id")

    op.drop_constraint("ck_suite_cases_variable_overrides", "test_suite_cases", type_="check")
    op.drop_column("test_suite_cases", "variable_overrides")
