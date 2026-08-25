"""relax profile variable override name check to allow unicode

Revision ID: d44a8a650a55
Revises: 9c42e1e3ab77
Create Date: 2026-08-25 17:56:42.338951

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd44a8a650a55'
down_revision: Union[str, Sequence[str], None] = '9c42e1e3ab77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """放宽 app_profile_variable_overrides.name 的字符限制，与 variables 表/用例 ${...} 引用保持一致，
    允许中文等任意非空变量名（此前仅 ASCII，覆盖“我的设备ID”等中文变量会违反 CHECK 抛 IntegrityError）。"""
    package = "app_profile_variable_overrides"
    op.drop_constraint("ck_profile_variable_name", package, type_="check")
    op.create_check_constraint("ck_profile_variable_name", package, "name <> ''")


def downgrade() -> None:
    """恢复 ASCII 限定（回滚时需先消除非 ASCII 数据，否则约束创建失败）。"""
    package = "app_profile_variable_overrides"
    op.drop_constraint("ck_profile_variable_name", package, type_="check")
    op.create_check_constraint(
        "ck_profile_variable_name",
        package,
        "name ~ '^[A-Za-z_][A-Za-z0-9_]{0,99}$'",
    )
