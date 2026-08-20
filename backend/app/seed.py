"""种子数据脚本：创建默认 admin 用户。

用法: uv run python -m app.seed
"""

import asyncio

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import User

DEFAULT_ADMIN = {"username": "admin", "email": "admin@tl-tek.com", "password": "admin123"}


async def seed() -> None:
    async with SessionLocal() as session:
        existing = await session.execute(select(User).where(User.username == DEFAULT_ADMIN["username"]))
        if existing.scalar_one_or_none():
            print("admin 用户已存在，跳过")
            return

        user = User(
            username=DEFAULT_ADMIN["username"],
            email=DEFAULT_ADMIN["email"],
            password_hash=hash_password(DEFAULT_ADMIN["password"]),
        )
        session.add(user)
        await session.commit()
        print(f"已创建 admin 用户 (id={user.id})")


if __name__ == "__main__":
    asyncio.run(seed())
