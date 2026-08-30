"""认证、Refresh Token 和用户 Agent Key 的数据库访问。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User, UserAgentKey


async def find_user_by_username_or_email(
    db: AsyncSession, username: str, email: str | None
) -> User | None:
    condition = (User.username == username) if not email else (User.username == username) | (User.email == email)
    return (await db.execute(select(User).where(condition))).scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    return (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()


async def set_admin(user: User) -> User:
    user.is_admin = True
    return user


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def create_user(
    db: AsyncSession, *, username: str, email: str | None, password_hash: str
) -> User:
    user = User(username=username, email=email, password_hash=password_hash)
    db.add(user)
    await db.flush()
    return user


async def create_refresh_token(
    db: AsyncSession, *, token_hash: str, user_id: int, expires_at: datetime
) -> RefreshToken:
    token = RefreshToken(token_hash=token_hash, user_id=user_id, expires_at=expires_at)
    db.add(token)
    return token


async def get_refresh_token_for_update(
    db: AsyncSession, token_hash: str
) -> RefreshToken | None:
    return (
        await db.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def get_refresh_token(db: AsyncSession, token_hash: str) -> RefreshToken | None:
    return (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))).scalar_one_or_none()


async def revoke_refresh_token(token: RefreshToken, revoked_at: datetime) -> None:
    token.revoked_at = revoked_at


async def get_user_agent_key(db: AsyncSession, user_id: int) -> UserAgentKey | None:
    return (
        await db.execute(select(UserAgentKey).where(UserAgentKey.user_id == user_id))
    ).scalar_one_or_none()


async def get_user_agent_key_by_public_id(
    db: AsyncSession, public_id: str
) -> UserAgentKey | None:
    return (
        await db.execute(select(UserAgentKey).where(UserAgentKey.public_id == public_id))
    ).scalar_one_or_none()


async def create_user_agent_key(
    db: AsyncSession,
    *,
    user_id: int,
    public_id: str,
    key_hash: str,
    encrypted_secret: str,
) -> UserAgentKey:
    key = UserAgentKey(
        user_id=user_id,
        public_id=public_id,
        key_hash=key_hash,
        encrypted_secret=encrypted_secret,
    )
    db.add(key)
    return key


async def replace_user_agent_key(
    key: UserAgentKey,
    *,
    public_id: str,
    key_hash: str,
    encrypted_secret: str,
) -> UserAgentKey:
    key.public_id = public_id
    key.key_hash = key_hash
    key.encrypted_secret = encrypted_secret
    return key
