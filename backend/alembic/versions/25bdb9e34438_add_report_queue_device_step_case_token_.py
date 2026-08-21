"""add report/queue/device/step/case/token constraints + success_rate numeric

Revision ID: 25bdb9e34438
Revises: 948d9af2fd30
Create Date: 2026-08-21 11:44:54.166086

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '25bdb9e34438'
down_revision: Union[str, Sequence[str], None] = '948d9af2fd30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 清理重复 refresh token（保留最新一条）
    op.execute(
        "DELETE FROM refresh_tokens a USING refresh_tokens b "
        "WHERE a.token_hash = b.token_hash AND a.id < b.id"
    )
    # CR-10：success_rate 改为 DECIMAL(5,2)
    op.alter_column('reports', 'success_rate', type_=sa.Numeric(5, 2), postgresql_using='success_rate::numeric')
    # CR-11 / §5.2：唯一与 CHECK 约束
    op.create_unique_constraint('uq_reports_execution_id', 'reports', ['execution_id'])
    op.create_check_constraint(
        'ck_reports_counts_nonneg', 'reports',
        'total >= 0 AND passed >= 0 AND failed >= 0 AND error_count >= 0 AND skipped >= 0',
    )
    op.create_check_constraint('ck_reports_success_rate_range', 'reports', 'success_rate >= 0 AND success_rate <= 100')
    op.create_unique_constraint('uq_execution_queue_execution_id', 'execution_queue', ['execution_id'])
    op.create_check_constraint(
        'ck_execution_queue_status', 'execution_queue',
        "status IN ('pending', 'claimed', 'done', 'failed')",
    )
    op.create_unique_constraint('uq_devices_agent_udid', 'devices', ['agent_id', 'udid'])
    op.create_check_constraint(
        'ck_devices_status', 'devices',
        "status IN ('idle', 'busy', 'offline', 'error')",
    )
    op.create_unique_constraint('uq_execution_steps_case_order', 'execution_steps', ['execution_case_id', 'step_order'])
    op.create_unique_constraint('uq_execution_cases_execution_case', 'execution_cases', ['execution_id', 'case_id'])
    op.create_unique_constraint('uq_refresh_tokens_token_hash', 'refresh_tokens', ['token_hash'])
    op.create_index('idx_refresh_tokens_user', 'refresh_tokens', ['user_id', 'expires_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_refresh_tokens_user', table_name='refresh_tokens')
    op.drop_constraint('uq_refresh_tokens_token_hash', 'refresh_tokens', type_='unique')
    op.drop_constraint('uq_execution_cases_execution_case', 'execution_cases', type_='unique')
    op.drop_constraint('uq_execution_steps_case_order', 'execution_steps', type_='unique')
    op.drop_constraint('ck_devices_status', 'devices', type_='check')
    op.drop_constraint('uq_devices_agent_udid', 'devices', type_='unique')
    op.drop_constraint('ck_execution_queue_status', 'execution_queue', type_='check')
    op.drop_constraint('uq_execution_queue_execution_id', 'execution_queue', type_='unique')
    op.drop_constraint('ck_reports_success_rate_range', 'reports', type_='check')
    op.drop_constraint('ck_reports_counts_nonneg', 'reports', type_='check')
    op.drop_constraint('uq_reports_execution_id', 'reports', type_='unique')
    op.alter_column('reports', 'success_rate', type_=sa.Integer(), postgresql_using='success_rate::integer')
