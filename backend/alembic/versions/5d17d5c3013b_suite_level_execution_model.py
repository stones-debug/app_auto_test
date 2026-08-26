"""suite level execution model

Revision ID: 5d17d5c3013b
Revises: d44a8a650a55
Create Date: 2026-08-26 09:35:40.581109

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d17d5c3013b'
down_revision: Union[str, Sequence[str], None] = 'd44a8a650a55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """套件级执行模型（方案 §2）。

    开发环境未上线：先清理历史执行数据并解设备锁，再重建套件级层级。
    test_suites 保留（套件/用例/成员关系），前后置初始化为空数组。
    """
    # 1) 解除设备执行锁并恢复可用状态（devices.status busy/error -> idle）
    op.execute("UPDATE devices SET status = 'idle', locked_by_execution = NULL, updated_at = now() WHERE status IN ('busy', 'error')")

    # 2) 清理执行相关数据（自引用 executions.retry_of 已被执行表依赖，先删子表再删 executions）
    op.execute("DELETE FROM execution_exclusions")
    op.execute("DELETE FROM execution_assertions")
    op.execute("DELETE FROM execution_steps")
    op.execute("DELETE FROM execution_cases")
    op.execute("DELETE FROM execution_logs")
    op.execute("DELETE FROM execution_queue")
    op.execute("DELETE FROM reports")
    op.execute("ALTER SEQUENCE execution_exclusions_id_seq RESTART WITH 1")
    op.execute("ALTER SEQUENCE execution_assertions_id_seq RESTART WITH 1")
    op.execute("ALTER SEQUENCE execution_steps_id_seq RESTART WITH 1")
    op.execute("ALTER SEQUENCE execution_cases_id_seq RESTART WITH 1")
    op.execute("ALTER SEQUENCE execution_queue_id_seq RESTART WITH 1")
    op.execute("ALTER SEQUENCE reports_id_seq RESTART WITH 1")
    op.execute("DELETE FROM executions")
    op.execute("ALTER SEQUENCE executions_id_seq RESTART WITH 1")

    # 3) 建 execution_suites + 分层约束
    op.create_table('execution_suites',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('execution_id', sa.Integer(), nullable=False),
    sa.Column('suite_id', sa.Integer(), nullable=True),
    sa.Column('suite_name', sa.String(length=255), nullable=False),
    sa.Column('suite_order', sa.Integer(), nullable=False),
    sa.Column('is_virtual', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('setup_steps_snapshot', sa.dialects.postgresql.JSONB(), nullable=False),
    sa.Column('teardown_steps_snapshot', sa.dialects.postgresql.JSONB(), nullable=False),
    sa.Column('elements_snapshot', sa.dialects.postgresql.JSONB(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration', sa.Integer(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(setup_steps_snapshot) = 'array'", name='ck_execution_suites_setup_steps'),
    sa.CheckConstraint("jsonb_typeof(teardown_steps_snapshot) = 'array'", name='ck_execution_suites_teardown_steps'),
    sa.CheckConstraint("status IN ('pending','running','stopping','passed','failed','error','stopped','skipped')", name='ck_execution_suites_status'),
    sa.ForeignKeyConstraint(['execution_id'], ['executions.id'], ),
    sa.ForeignKeyConstraint(['suite_id'], ['test_suites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('execution_id', 'suite_order', name='uq_execution_suites_execution_order')
    )
    op.create_index('uq_execution_suites_execution_suite', 'execution_suites', ['execution_id', 'suite_id'], unique=True, postgresql_where=sa.text('suite_id IS NOT NULL'))

    # 4) 事务已清空，execution_cases/execution_assertions 可安全加非空列
    op.add_column('execution_assertions', sa.Column('execution_case_id', sa.Integer(), nullable=False))
    op.add_column('execution_assertions', sa.Column('assertion_order', sa.Integer(), nullable=False))
    op.create_index('idx_exec_assertions_case', 'execution_assertions', ['execution_case_id'], unique=False)
    op.create_index('uq_exec_assertions_case_order', 'execution_assertions', ['execution_case_id', 'assertion_order'], unique=True)
    op.drop_constraint(op.f('execution_assertions_execution_step_id_fkey'), 'execution_assertions', type_='foreignkey')
    op.create_foreign_key('fk_exec_assertions_case', 'execution_assertions', 'execution_cases', ['execution_case_id'], ['id'])
    op.drop_column('execution_assertions', 'execution_step_id')

    op.add_column('execution_cases', sa.Column('execution_suite_id', sa.Integer(), nullable=False))
    op.add_column('execution_cases', sa.Column('case_order', sa.Integer(), nullable=False))
    op.drop_index(op.f('idx_exec_cases_execution'), table_name='execution_cases')
    op.drop_constraint(op.f('uq_execution_cases_execution_case'), 'execution_cases', type_='unique')
    op.create_index('idx_exec_cases_suite', 'execution_cases', ['execution_suite_id'], unique=False)
    op.create_unique_constraint('uq_execution_cases_suite_case', 'execution_cases', ['execution_suite_id', 'case_id'])
    op.create_unique_constraint('uq_execution_cases_suite_order', 'execution_cases', ['execution_suite_id', 'case_order'])
    op.create_foreign_key('fk_exec_cases_suite', 'execution_cases', 'execution_suites', ['execution_suite_id'], ['id'])

    op.add_column('execution_steps', sa.Column('execution_suite_id', sa.Integer(), nullable=True))
    op.add_column('execution_steps', sa.Column('phase', sa.String(length=20), nullable=False))
    op.add_column('execution_steps', sa.Column('source_key', sa.String(length=255), nullable=True))
    op.add_column('execution_steps', sa.Column('source_order', sa.Integer(), nullable=True))
    op.alter_column('execution_steps', 'execution_case_id',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.drop_constraint(op.f('uq_execution_steps_case_order'), 'execution_steps', type_='unique')
    op.create_index('uq_execution_steps_case_order', 'execution_steps', ['execution_case_id', 'phase', 'step_order'], unique=True, postgresql_where=sa.text('execution_suite_id IS NULL AND execution_case_id IS NOT NULL'))
    op.create_index('uq_execution_steps_suite_order', 'execution_steps', ['execution_suite_id', 'phase', 'step_order'], unique=True, postgresql_where=sa.text('execution_suite_id IS NOT NULL AND execution_case_id IS NULL'))
    op.create_foreign_key('fk_exec_steps_suite', 'execution_steps', 'execution_suites', ['execution_suite_id'], ['id'])
    op.create_check_constraint('ck_execution_steps_parent_phase', 'execution_steps', "(phase IN ('suite_setup','suite_teardown') AND execution_suite_id IS NOT NULL AND execution_case_id IS NULL) OR (phase IN ('case_setup','case_main','case_teardown') AND execution_suite_id IS NULL AND execution_case_id IS NOT NULL)")
    op.create_check_constraint('ck_execution_steps_phase', 'execution_steps', "phase IN ('suite_setup','case_setup','case_main','case_teardown','suite_teardown')")

    # 5) 报告三层统计字段
    op.add_column('reports', sa.Column('suite_total', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('suite_passed', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('suite_failed', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('suite_error_count', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('suite_skipped', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('suite_success_rate', sa.Numeric(precision=5, scale=2), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('step_total', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('step_passed', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('step_failed', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('step_error_count', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('step_skipped', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('step_success_rate', sa.Numeric(precision=5, scale=2), server_default=sa.text('0'), nullable=False))
    op.add_column('reports', sa.Column('not_applicable_suites', sa.Integer(), server_default=sa.text('0'), nullable=False))

    # 6) 套件前后置（保留套件表；非空 JSONB 需要 server_default 支持存量行）+ 节点覆盖补 suite_id
    op.add_column('test_suites', sa.Column('setup_steps', sa.dialects.postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column('test_suites', sa.Column('teardown_steps', sa.dialects.postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.alter_column('test_suites', 'setup_steps', server_default=None)
    op.alter_column('test_suites', 'teardown_steps', server_default=None)
    op.create_check_constraint('ck_test_suites_setup_steps', 'test_suites', "jsonb_typeof(setup_steps) = 'array'")
    op.create_check_constraint('ck_test_suites_teardown_steps', 'test_suites', "jsonb_typeof(teardown_steps) = 'array'")
    # 方案 §2.3：节点覆盖补 suite_id（区分套件上下文）；旧记录删除无法确定套件上下文的覆盖
    op.add_column('app_profile_node_overrides', sa.Column('suite_id', sa.Integer(), nullable=True))
    # case_id 改可空：套件步骤覆盖时 case_id 为空（仅 suite_id + node_key）
    op.alter_column('app_profile_node_overrides', 'case_id', existing_type=sa.INTEGER(), nullable=True)
    op.create_foreign_key('fk_profile_node_override_suite', 'app_profile_node_overrides', 'test_suites', ['suite_id'], ['id'])
    op.execute("DELETE FROM app_profile_node_overrides WHERE suite_id IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_test_suites_teardown_steps', 'test_suites', type_='check')
    op.drop_constraint('ck_test_suites_setup_steps', 'test_suites', type_='check')
    op.drop_column('test_suites', 'teardown_steps')
    op.drop_column('test_suites', 'setup_steps')
    op.drop_column('reports', 'not_applicable_suites')
    op.drop_column('reports', 'step_success_rate')
    op.drop_column('reports', 'step_skipped')
    op.drop_column('reports', 'step_error_count')
    op.drop_column('reports', 'step_failed')
    op.drop_column('reports', 'step_passed')
    op.drop_column('reports', 'step_total')
    op.drop_column('reports', 'suite_success_rate')
    op.drop_column('reports', 'suite_skipped')
    op.drop_column('reports', 'suite_error_count')
    op.drop_column('reports', 'suite_failed')
    op.drop_column('reports', 'suite_passed')
    op.drop_column('reports', 'suite_total')
    op.drop_constraint('ck_execution_steps_phase', 'execution_steps', type_='check')
    op.drop_constraint('ck_execution_steps_parent_phase', 'execution_steps', type_='check')
    op.drop_constraint('fk_exec_steps_suite', 'execution_steps', type_='foreignkey')
    op.drop_index('uq_execution_steps_suite_order', table_name='execution_steps', postgresql_where=sa.text('execution_suite_id IS NOT NULL AND execution_case_id IS NULL'))
    op.drop_index('uq_execution_steps_case_order', table_name='execution_steps', postgresql_where=sa.text('execution_suite_id IS NULL AND execution_case_id IS NOT NULL'))
    op.create_unique_constraint(op.f('uq_execution_steps_case_order'), 'execution_steps', ['execution_case_id', 'step_order'], postgresql_nulls_not_distinct=False)
    op.alter_column('execution_steps', 'execution_case_id',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.drop_column('execution_steps', 'source_order')
    op.drop_column('execution_steps', 'source_key')
    op.drop_column('execution_steps', 'phase')
    op.drop_column('execution_steps', 'execution_suite_id')
    op.drop_constraint('fk_exec_cases_suite', 'execution_cases', type_='foreignkey')
    op.drop_constraint('uq_execution_cases_suite_order', 'execution_cases', type_='unique')
    op.drop_constraint('uq_execution_cases_suite_case', 'execution_cases', type_='unique')
    op.drop_index('idx_exec_cases_suite', table_name='execution_cases')
    op.create_unique_constraint(op.f('uq_execution_cases_execution_case'), 'execution_cases', ['execution_id', 'case_id'], postgresql_nulls_not_distinct=False)
    op.create_index(op.f('idx_exec_cases_execution'), 'execution_cases', ['execution_id'], unique=False)
    op.drop_column('execution_cases', 'case_order')
    op.drop_column('execution_cases', 'execution_suite_id')
    op.add_column('execution_assertions', sa.Column('execution_step_id', sa.INTEGER(), autoincrement=False, nullable=False))
    op.drop_constraint('fk_exec_assertions_case', 'execution_assertions', type_='foreignkey')
    op.create_foreign_key(op.f('execution_assertions_execution_step_id_fkey'), 'execution_assertions', 'execution_steps', ['execution_step_id'], ['id'])
    op.drop_index('uq_exec_assertions_case_order', table_name='execution_assertions')
    op.drop_index('idx_exec_assertions_case', table_name='execution_assertions')
    op.drop_column('execution_assertions', 'assertion_order')
    op.drop_column('execution_assertions', 'execution_case_id')
    op.drop_index('uq_execution_suites_execution_suite', table_name='execution_suites', postgresql_where=sa.text('suite_id IS NOT NULL'))
    op.drop_table('execution_suites')
    op.drop_constraint('fk_profile_node_override_suite', 'app_profile_node_overrides', type_='foreignkey')
    op.alter_column('app_profile_node_overrides', 'case_id', existing_type=sa.INTEGER(), nullable=False)
    op.drop_column('app_profile_node_overrides', 'suite_id')
