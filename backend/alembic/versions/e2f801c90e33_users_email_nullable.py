"""users.email nullable

Revision ID: e2f801c90e33
Revises: 7f33770e371e
Create Date: 2026-08-22 22:39:57.223127

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f801c90e33'
down_revision: Union[str, Sequence[str], None] = '7f33770e371e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 允许 email 为空（注册只需用户名+密码）；NULL 不受唯一约束限制
    op.alter_column('users', 'email',
               existing_type=sa.VARCHAR(length=255),
               nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # 回滚前删除无邮箱用户，否则 NOT NULL 会失败
    op.execute("DELETE FROM users WHERE email IS NULL")
    op.alter_column('users', 'email',
               existing_type=sa.VARCHAR(length=255),
               nullable=False)
