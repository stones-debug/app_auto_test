"""add module scope and suite module

测试套件模块分组（见 docs/测试套件模块分组设计方案.md）：
1. test_modules.scope 区分用例树（case）与套件树（suite），两棵树互相独立；
   现存模块全部属于用例树，回填 'case'。
2. test_suites.module_id 让套件挂到套件模块树上。
3. 模块筛选是列表页高频查询，补三条部分索引（跳过软删行）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5f9d6696ee4"
down_revision: str | Sequence[str] | None = "7e4c3cc6bd37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 模块归属：现存行（历史上只服务用例）统一回填 case
    op.add_column(
        "test_modules",
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="case"),
    )
    op.create_check_constraint("ck_test_modules_scope", "test_modules", "scope IN ('case','suite')")
    op.create_index(
        "idx_test_modules_scope_parent",
        "test_modules",
        ["project_id", "scope", "parent_id", "sort_order"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 套件挂载到套件模块树；跨表 scope 一致性（只能指向 scope='suite' 的模块）由服务层校验
    op.add_column("test_suites", sa.Column("module_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_test_suites_module_id", "test_suites", "test_modules", ["module_id"], ["id"]
    )
    op.create_index(
        "idx_test_suites_module",
        "test_suites",
        ["project_id", "module_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 用例侧原有的 module_id 也没有索引，一并在同一次迁移补齐
    op.create_index(
        "idx_test_cases_module",
        "test_cases",
        ["project_id", "module_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_test_cases_module", table_name="test_cases")
    op.drop_index("idx_test_suites_module", table_name="test_suites")
    # 必须先放开 test_suites.module_id 再清理套件模块，否则外键会挡住删除
    op.drop_constraint("fk_test_suites_module_id", "test_suites", type_="foreignkey")
    op.drop_column("test_suites", "module_id")
    # 开发环境尚未上线（AGENTS.md），无需兼容历史数据，直接清理套件模块；
    # 若将来上线后回滚，这里应改为 UPDATE ... SET deleted_at = NOW() 保留数据。
    op.execute("DELETE FROM test_modules WHERE scope = 'suite'")
    op.drop_index("idx_test_modules_scope_parent", table_name="test_modules")
    op.drop_constraint("ck_test_modules_scope", "test_modules", type_="check")
    op.drop_column("test_modules", "scope")
