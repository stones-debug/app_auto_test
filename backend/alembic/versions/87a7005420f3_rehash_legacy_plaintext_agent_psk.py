"""rehash legacy plaintext agent psk

Revision ID: 87a7005420f3
Revises: 25bdb9e34438
Create Date: 2026-08-21 13:06:08.422777

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '87a7005420f3'
down_revision: Union[str, Sequence[str], None] = '25bdb9e34438'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """把历史明文 Agent PSK 重哈希为 Argon2（CR-03 数据迁移）。"""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, agent_key FROM agents WHERE agent_key NOT LIKE '$argon2%'")
    ).fetchall()
    if not rows:
        return
    from app.core.security import hash_psk

    for row in rows:
        bind.execute(
            sa.text("UPDATE agents SET agent_key = :h WHERE id = :id"),
            {"h": hash_psk(row.agent_key), "id": row.id},
        )


def downgrade() -> None:
    """Downgrade schema.（数据不可逆：不还原明文）"""
    pass
