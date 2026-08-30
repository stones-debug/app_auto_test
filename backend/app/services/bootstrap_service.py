"""应用初始化编排。"""

from app.core.security import hash_password
from app.models import User
from app.repositories import auth as auth_repo


async def ensure_admin(db, *, username: str, email: str, password: str) -> tuple[User, bool]:
    user = await auth_repo.get_user_by_username(db, username)
    if user is not None:
        changed = not user.is_admin
        if changed:
            await auth_repo.set_admin(user)
            await db.commit()
        return user, changed
    user = await auth_repo.create_user(
        db, username=username, email=email, password_hash=hash_password(password)
    )
    await auth_repo.set_admin(user)
    await db.commit()
    return user, True
