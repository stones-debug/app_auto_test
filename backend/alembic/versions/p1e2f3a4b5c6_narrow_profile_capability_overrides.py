"""Remove superseded APP profile element, global-variable and node overrides.

The project is still in development; these obsolete configuration rows are
intentionally discarded rather than mapped to the remaining occurrence model.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "p1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "o0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ACTIONS = "'element_override_upsert','element_override_restore','variable_override_upsert','variable_override_restore','node_override_upsert','node_override_restore','node_override_batch'"
_ACTIONS_NEW = "'profile_create','profile_update','profile_disable','release_create','release_update','release_disable','skip_batch','restore_batch','occurrence_variable_override_batch','user_variable_override_batch'"


def upgrade() -> None:
    op.execute(f"DELETE FROM app_profile_audit_logs WHERE action IN ({_ACTIONS})")
    op.drop_constraint("ck_profile_audit_action", "app_profile_audit_logs", type_="check")
    op.create_check_constraint("ck_profile_audit_action", "app_profile_audit_logs", f"action IN ({_ACTIONS_NEW})")
    op.drop_table("app_profile_node_overrides")
    op.drop_table("app_profile_variable_overrides")
    op.drop_table("app_profile_element_overrides")


def downgrade() -> None:
    # Development-only migration: restore the table shapes for structural
    # rollback, without claiming to restore discarded configuration data.
    op.create_table(
        "app_profile_element_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("locator_type", sa.String(50), nullable=False),
        sa.Column("locator_value", sa.Text()),
        sa.Column("locator_config", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Integer()),
        sa.Column("updated_by", sa.Integer()),
        sa.CheckConstraint(
            "(locator_type = 'smart' AND locator_config IS NOT NULL AND locator_value IS NULL) "
            "OR (locator_type <> 'smart' AND locator_config IS NULL AND locator_value IS NOT NULL)",
            name="ck_profile_element_locator_mode",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["app_profiles.id"]),
        sa.ForeignKeyConstraint(["element_id"], ["test_elements.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
    )
    op.create_index("idx_profile_element_override_element", "app_profile_element_overrides", ["element_id"], postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_index("uq_profile_element_override_active", "app_profile_element_overrides", ["profile_id", "element_id"], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_table(
        "app_profile_variable_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Integer()),
        sa.Column("updated_by", sa.Integer()),
        sa.CheckConstraint("name <> ''", name="ck_profile_variable_name"),
        sa.ForeignKeyConstraint(["profile_id"], ["app_profiles.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
    )
    op.create_index("uq_profile_variable_override_active", "app_profile_variable_overrides", ["profile_id", "name"], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_table(
        "app_profile_node_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("suite_id", sa.Integer()),
        sa.Column("suite_case_id", sa.Integer()),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("case_id", sa.Integer()),
        sa.Column("node_key", sa.Uuid(), nullable=False),
        sa.Column("patch", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Integer()),
        sa.Column("updated_by", sa.Integer()),
        sa.ForeignKeyConstraint(["profile_id"], ["app_profiles.id"]),
        sa.ForeignKeyConstraint(["suite_id"], ["test_suites.id"]),
        sa.ForeignKeyConstraint(["suite_case_id"], ["test_suite_cases.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.CheckConstraint("target_type IN ('step', 'assertion', 'suite_step')", name="ck_profile_node_override_type"),
        sa.CheckConstraint("jsonb_typeof(patch) = 'object' AND patch <> '{}'::jsonb", name="ck_profile_node_override_patch"),
        sa.CheckConstraint(
            "(target_type IN ('step', 'assertion') AND suite_id IS NOT NULL AND case_id IS NOT NULL AND suite_case_id IS NOT NULL)"
            " OR (target_type = 'suite_step' AND suite_id IS NOT NULL AND case_id IS NULL AND suite_case_id IS NULL)",
            name="ck_profile_node_override_shape",
        ),
    )
    op.create_index("uq_profile_node_override_active", "app_profile_node_overrides", ["profile_id", "suite_case_id", "target_type", "node_key"], unique=True, postgresql_where=sa.text("target_type IN ('step', 'assertion') AND deleted_at IS NULL"))
    op.create_index("uq_profile_node_override_suite_step_active", "app_profile_node_overrides", ["profile_id", "suite_id", "node_key"], unique=True, postgresql_where=sa.text("target_type = 'suite_step' AND deleted_at IS NULL"))
    op.create_index("idx_profile_node_override_case", "app_profile_node_overrides", ["case_id"], postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_index("idx_profile_node_override_suite", "app_profile_node_overrides", ["suite_id"], postgresql_where=sa.text("deleted_at IS NULL AND suite_id IS NOT NULL"))
    op.create_index("idx_profile_node_override_suite_case", "app_profile_node_overrides", ["suite_case_id"], postgresql_where=sa.text("deleted_at IS NULL AND suite_case_id IS NOT NULL"))
    op.drop_constraint("ck_profile_audit_action", "app_profile_audit_logs", type_="check")
    op.create_check_constraint("ck_profile_audit_action", "app_profile_audit_logs", f"action IN ({_ACTIONS_NEW},{_ACTIONS})")
