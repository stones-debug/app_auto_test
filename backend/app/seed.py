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
        user = existing.scalar_one_or_none()
        if user:
            # Windows 方案 §2：已存在 admin 时更新管理员标记（幂等），不重复创建
            if not user.is_admin:
                user.is_admin = True
                await session.commit()
                print(f"已更新 admin 用户管理员标记 (id={user.id})")
            else:
                print("admin 用户已存在且为管理员，跳过")
            return

        user = User(
            username=DEFAULT_ADMIN["username"],
            email=DEFAULT_ADMIN["email"],
            password_hash=hash_password(DEFAULT_ADMIN["password"]),
            is_admin=True,
        )
        session.add(user)
        await session.commit()
        print(f"已创建 admin 用户 (id={user.id})")


if __name__ == "__main__":
    asyncio.run(seed())
