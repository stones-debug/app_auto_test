"""add current-user APP profile variable overrides"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j5e6f7a8b9c1"
down_revision: str | Sequence[str] | None = "i5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "variables",
        sa.Column("is_sensitive", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "user_app_profile_variable_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("app_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variable_id", sa.Integer(), sa.ForeignKey("variables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("value_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "profile_id", "variable_id", name="uq_user_profile_variable_override"),
    )
    op.create_index(
        "idx_user_profile_variable_overrides_profile_user",
        "user_app_profile_variable_overrides",
        ["profile_id", "user_id"],
    )
    op.drop_constraint("uq_profile_audit_request", "app_profile_audit_logs", type_="unique")
    op.create_unique_constraint(
        "uq_profile_audit_request_actor", "app_profile_audit_logs", ["profile_id", "actor_id", "request_id"]
    )
    op.drop_constraint("ck_profile_audit_action", "app_profile_audit_logs", type_="check")
    op.create_check_constraint(
        "ck_profile_audit_action",
        "app_profile_audit_logs",
        "action IN ('profile_create', 'profile_update', 'profile_disable',"
        " 'release_create', 'release_update', 'release_disable',"
        " 'skip_batch', 'restore_batch', 'element_override_upsert',"
        " 'element_override_restore', 'variable_override_upsert',"
        " 'variable_override_restore', 'node_override_upsert', 'node_override_restore',"
        " 'node_override_batch', 'occurrence_variable_override_batch', 'user_variable_override_batch')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_profile_audit_action", "app_profile_audit_logs", type_="check")
    op.create_check_constraint(
        "ck_profile_audit_action",
        "app_profile_audit_logs",
        "action IN ('profile_create', 'profile_update', 'profile_disable',"
        " 'release_create', 'release_update', 'release_disable',"
        " 'skip_batch', 'restore_batch', 'element_override_upsert',"
        " 'element_override_restore', 'variable_override_upsert',"
        " 'variable_override_restore', 'node_override_upsert', 'node_override_restore',"
        " 'node_override_batch', 'occurrence_variable_override_batch')",
    )
    op.drop_constraint("uq_profile_audit_request_actor", "app_profile_audit_logs", type_="unique")
    op.create_unique_constraint("uq_profile_audit_request", "app_profile_audit_logs", ["profile_id", "request_id"])
    op.drop_index("idx_user_profile_variable_overrides_profile_user", table_name="user_app_profile_variable_overrides")
    op.drop_table("user_app_profile_variable_overrides")
    op.drop_column("variables", "is_sensitive")
