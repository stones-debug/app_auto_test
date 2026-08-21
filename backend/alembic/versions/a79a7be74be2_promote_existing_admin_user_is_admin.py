"""promote existing admin user is_admin

Revision ID: a79a7be74be2
Revises: 5f986ae0e6bc
Create Date: 2026-08-21 14:20:05.231860

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a79a7be74be2'
down_revision: Union[str, Sequence[str], None] = '5f986ae0e6bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Windows 方案 §2：已有 admin 用户提升为平台管理员（幂等）。"""
    op.execute("UPDATE users SET is_admin = TRUE WHERE username = 'admin'")


def downgrade() -> None:
    """回滚：将 seed 账号还原为普通用户（迁移前状态无法精确还原，仅处理默认 admin）。"""
    op.execute("UPDATE users SET is_admin = FALSE WHERE username = 'admin'")
