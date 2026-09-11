"""add occurrence-scoped APP profile variable overrides"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "h4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # occurrence 审计动作比历史 action 列更长；开发环境直接升级字段与 CHECK。
    op.alter_column(
        "app_profile_audit_logs",
        "action",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.drop_constraint("ck_profile_audit_action", "app_profile_audit_logs", type_="check")
    op.create_check_constraint(
        "ck_profile_audit_action",
        "app_profile_audit_logs",
        "action IN ('profile_create', 'profile_update', 'profile_disable', "
        "'release_create', 'release_update', 'release_disable', 'skip_batch', "
        "'restore_batch', 'element_override_upsert', 'element_override_restore', "
        "'variable_override_upsert', 'variable_override_restore', 'node_override_upsert', "
        "'node_override_restore', 'node_override_batch', "
        "'occurrence_variable_override_batch')",
    )
    # 节点变量覆盖已迁移为 occurrence 级独立记录；开发环境无需保留旧 patch。
    op.execute(
        "DELETE FROM app_profile_node_overrides "
        "WHERE patch ? 'variable_overrides' AND patch - 'variable_overrides' = '{}'::jsonb"
    )
    op.execute(
        "UPDATE app_profile_node_overrides "
        "SET patch = patch - 'variable_overrides' "
        "WHERE patch ? 'variable_overrides'"
    )
    op.create_table(
        "app_profile_suite_case_variable_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("suite_case_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["app_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suite_case_id"], ["test_suite_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.CheckConstraint("name <> ''", name="ck_profile_suite_case_variable_name"),
    )
    op.create_index(
        "uq_profile_suite_case_variable_override_active",
        "app_profile_suite_case_variable_overrides",
        ["profile_id", "suite_case_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_profile_suite_case_variable_override_membership",
        "app_profile_suite_case_variable_overrides",
        ["suite_case_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_profile_suite_case_variable_override_membership",
        table_name="app_profile_suite_case_variable_overrides",
    )
    op.drop_index(
        "uq_profile_suite_case_variable_override_active",
        table_name="app_profile_suite_case_variable_overrides",
    )
    op.drop_table("app_profile_suite_case_variable_overrides")
    op.drop_constraint("ck_profile_audit_action", "app_profile_audit_logs", type_="check")
    op.execute(
        "DELETE FROM app_profile_audit_logs "
        "WHERE action = 'occurrence_variable_override_batch'"
    )
    op.alter_column(
        "app_profile_audit_logs",
        "action",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_profile_audit_action",
        "app_profile_audit_logs",
        "action IN ('profile_create', 'profile_update', 'profile_disable', "
        "'release_create', 'release_update', 'release_disable', 'skip_batch', "
        "'restore_batch', 'element_override_upsert', 'element_override_restore', "
        "'variable_override_upsert', 'variable_override_restore', 'node_override_upsert', "
        "'node_override_restore', 'node_override_batch')",
    )
