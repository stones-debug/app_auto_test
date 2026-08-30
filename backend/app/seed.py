"""种子数据脚本：创建默认 admin 用户。

用法: uv run python -m app.seed
"""

import asyncio

from app.core.database import SessionLocal
from app.services.bootstrap_service import ensure_admin

DEFAULT_ADMIN = {"username": "admin", "email": "admin@tl-tek.com", "password": "admin123"}


async def seed() -> None:
    async with SessionLocal() as session:
        user, changed = await ensure_admin(
            session,
            username=DEFAULT_ADMIN["username"],
            email=DEFAULT_ADMIN["email"],
            password=DEFAULT_ADMIN["password"],
        )
        if user.id and changed:
            if user.is_admin:
                print(f"已更新 admin 用户管理员标记 (id={user.id})")
        else:
            print("admin 用户已存在且为管理员，跳过")


if __name__ == "__main__":
    asyncio.run(seed())
