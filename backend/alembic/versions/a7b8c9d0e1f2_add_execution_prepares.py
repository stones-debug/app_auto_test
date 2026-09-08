"""store short-lived preview resolution snapshots"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_prepares",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("app_profile_id", sa.Integer(), nullable=False),
        sa.Column("app_release_id", sa.Integer(), nullable=False),
        sa.Column("app_release_version", sa.String(length=64), nullable=False),
        sa.Column("profile_revision", sa.BigInteger(), nullable=False),
        sa.Column("test_asset_revision", sa.BigInteger(), nullable=False),
        sa.Column("target", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("resolution_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("resolution_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["app_profile_id"], ["app_profiles.id"]),
        sa.ForeignKeyConstraint(["app_release_id"], ["app_profile_releases.id"]),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("idx_execution_prepares_user_project", "execution_prepares", ["user_id", "project_id"])
    op.create_index("idx_execution_prepares_expires", "execution_prepares", ["expires_at"])
    op.create_index("idx_execution_prepares_active", "execution_prepares", ["expires_at", "consumed_at"])


def downgrade() -> None:
    op.drop_index("idx_execution_prepares_active", table_name="execution_prepares")
    op.drop_index("idx_execution_prepares_expires", table_name="execution_prepares")
    op.drop_index("idx_execution_prepares_user_project", table_name="execution_prepares")
    op.drop_table("execution_prepares")
