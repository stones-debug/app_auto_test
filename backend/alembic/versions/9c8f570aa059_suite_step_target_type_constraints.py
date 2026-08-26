"""suite_step target type constraints

Revision ID: 9c8f570aa059
Revises: 5d17d5c3013b
Create Date: 2026-08-26 09:57:45.625969

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c8f570aa059'
down_revision: Union[str, Sequence[str], None] = '5d17d5c3013b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 新增索引（autogenerate 已检测）
    op.create_index('idx_profile_node_override_suite', 'app_profile_node_overrides', ['suite_id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL AND suite_id IS NOT NULL'))
    op.create_index('uq_profile_node_override_suite_step_active', 'app_profile_node_overrides', ['profile_id', 'suite_id', 'node_key'], unique=True, postgresql_where=sa.text("target_type = 'suite_step' AND deleted_at IS NULL"))
    op.create_check_constraint('ck_profile_node_override_shape', 'app_profile_node_overrides', "(target_type IN ('step', 'assertion') AND suite_id IS NULL AND case_id IS NOT NULL) OR (target_type = 'suite_step' AND suite_id IS NOT NULL AND case_id IS NULL)")
    op.create_index('uq_profile_skip_suite_step_active', 'app_profile_skip_rules', ['profile_id', 'suite_id', 'node_key'], unique=True, postgresql_where=sa.text("target_type = 'suite_step' AND deleted_at IS NULL"))

    # 扩展既有 CHECK 约束以支持 suite_step（autogenerate 不检测 CHECK 变更，需手工替换）
    op.drop_constraint('ck_profile_skip_target_type', 'app_profile_skip_rules', type_='check')
    op.create_check_constraint('ck_profile_skip_target_type', 'app_profile_skip_rules', "target_type IN ('suite', 'case', 'step', 'assertion', 'suite_step')")
    op.drop_constraint('ck_profile_skip_target_shape', 'app_profile_skip_rules', type_='check')
    op.create_check_constraint('ck_profile_skip_target_shape', 'app_profile_skip_rules', "(target_type = 'suite' AND suite_id IS NOT NULL AND case_id IS NULL AND node_key IS NULL) OR (target_type = 'case' AND suite_id IS NOT NULL AND case_id IS NOT NULL AND node_key IS NULL) OR (target_type = 'suite_step' AND suite_id IS NOT NULL AND case_id IS NULL AND node_key IS NOT NULL) OR (target_type IN ('step', 'assertion') AND suite_id IS NOT NULL AND case_id IS NOT NULL AND node_key IS NOT NULL)")
    op.drop_constraint('ck_execution_exclusion_target', 'execution_exclusions', type_='check')
    op.create_check_constraint('ck_execution_exclusion_target', 'execution_exclusions', "target_type IN ('suite', 'case', 'step', 'assertion', 'suite_step')")
    op.drop_constraint('ck_profile_node_override_type', 'app_profile_node_overrides', type_='check')
    op.create_check_constraint('ck_profile_node_override_type', 'app_profile_node_overrides', "target_type IN ('step', 'assertion', 'suite_step')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_profile_node_override_type', 'app_profile_node_overrides', type_='check')
    op.create_check_constraint('ck_profile_node_override_type', 'app_profile_node_overrides', "target_type IN ('step', 'assertion')")
    op.drop_constraint('ck_execution_exclusion_target', 'execution_exclusions', type_='check')
    op.create_check_constraint('ck_execution_exclusion_target', 'execution_exclusions', "target_type IN ('suite', 'case', 'step', 'assertion')")
    op.drop_constraint('ck_profile_skip_target_shape', 'app_profile_skip_rules', type_='check')
    op.create_check_constraint('ck_profile_skip_target_shape', 'app_profile_skip_rules', "(target_type = 'suite' AND suite_id IS NOT NULL AND case_id IS NULL AND node_key IS NULL) OR (target_type = 'case' AND suite_id IS NOT NULL AND case_id IS NOT NULL AND node_key IS NULL) OR (target_type IN ('step', 'assertion') AND suite_id IS NOT NULL AND case_id IS NOT NULL AND node_key IS NOT NULL)")
    op.drop_constraint('ck_profile_skip_target_type', 'app_profile_skip_rules', type_='check')
    op.create_check_constraint('ck_profile_skip_target_type', 'app_profile_skip_rules', "target_type IN ('suite', 'case', 'step', 'assertion')")
    op.drop_index('uq_profile_skip_suite_step_active', table_name='app_profile_skip_rules', postgresql_where=sa.text("target_type = 'suite_step' AND deleted_at IS NULL"))
    op.drop_constraint('ck_profile_node_override_shape', 'app_profile_node_overrides', type_='check')
    op.drop_index('uq_profile_node_override_suite_step_active', table_name='app_profile_node_overrides', postgresql_where=sa.text("target_type = 'suite_step' AND deleted_at IS NULL"))
    op.drop_index('idx_profile_node_override_suite', table_name='app_profile_node_overrides', postgresql_where=sa.text('deleted_at IS NULL AND suite_id IS NOT NULL'))
