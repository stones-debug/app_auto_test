"""认证和当前用户 Agent Key 用例。"""

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ErrorCode, api_error
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_user_key,
    encrypt_user_key,
    hash_password,
    hash_user_key,
    new_user_agent_key,
    verify_password,
)
from app.models import User
from app.repositories import auth as auth_repo
from app.schemas.agent import AgentKeyCreateResponse, AgentKeyOut
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserOut


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_tokens(db: AsyncSession, user: User) -> TokenResponse:
    access_token = create_access_token(user.id, user.username)
    refresh_token = create_refresh_token(user.id, user.username)
    await auth_repo.create_refresh_token(
        db,
        token_hash=_hash_refresh_token(refresh_token),
        user_id=user.id,
        expires_at=datetime.now(UTC).replace(tzinfo=None)
        + timedelta(days=settings.jwt_refresh_token_expire_days),
    )
    await db.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut.model_validate(user),
    )


async def register_user(db: AsyncSession, body: RegisterRequest) -> TokenResponse:
    existing = await auth_repo.find_user_by_username_or_email(db, body.username, body.email)
    if existing is not None:
        detail = "用户名或邮箱已存在" if body.email else "用户名已存在"
        raise api_error(status.HTTP_409_CONFLICT, ErrorCode.AUTH_USER_EXISTS, detail)
    user = await auth_repo.create_user(
        db,
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
    )
    try:
        return await issue_tokens(db, user)
    except Exception:
        await db.rollback()
        raise


async def authenticate_user(db: AsyncSession, body: LoginRequest) -> TokenResponse:
    user = await auth_repo.get_user_by_username(db, body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.AUTH_CREDENTIALS_INVALID,
            "用户名或密码错误",
        )
    if user.status != "active":
        raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.AUTH_USER_DISABLED, "用户已被禁用")
    return await issue_tokens(db, user)


async def refresh_tokens(db: AsyncSession, body: RefreshRequest) -> TokenResponse:
    payload = decode_token(body.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_REFRESH_INVALID, "无效的 Refresh Token")
    stored = await auth_repo.get_refresh_token_for_update(
        db, _hash_refresh_token(body.refresh_token)
    )
    if stored is None or stored.revoked_at is not None:
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_REFRESH_INVALID, "Refresh Token 已失效")
    user = await auth_repo.get_user_by_id(db, int(payload["sub"]))
    if user is None or user.status != "active":
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_USER_DISABLED, "用户不存在或已禁用")
    await auth_repo.revoke_refresh_token(stored, datetime.now(UTC).replace(tzinfo=None))
    try:
        return await issue_tokens(db, user)
    except Exception:
        await db.rollback()
        raise


async def logout(db: AsyncSession, body: RefreshRequest) -> None:
    stored = await auth_repo.get_refresh_token(db, _hash_refresh_token(body.refresh_token))
    if stored is not None:
        await auth_repo.revoke_refresh_token(stored, datetime.now(UTC).replace(tzinfo=None))
        await db.commit()


async def get_agent_key(db: AsyncSession, user_id: int) -> AgentKeyOut:
    key = await auth_repo.get_user_agent_key(db, user_id)
    if key is None:
        return AgentKeyOut(exists=False)
    try:
        secret = decrypt_user_key(key.encrypted_secret)
    except ValueError as exc:
        raise api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "AGENT_KEY_DECRYPT_FAILED", str(exc)) from exc
    return AgentKeyOut(exists=True, public_id=key.public_id, key=f"uak_{key.public_id}_{secret}")


async def create_agent_key(db: AsyncSession, user_id: int) -> AgentKeyCreateResponse:
    if await auth_repo.get_user_agent_key(db, user_id) is not None:
        raise api_error(status.HTTP_409_CONFLICT, "AGENT_KEY_ALREADY_EXISTS", "Key 已存在，请使用 regenerate")
    public_id, secret, full_key = new_user_agent_key()
    await auth_repo.create_user_agent_key(
        db,
        user_id=user_id,
        public_id=public_id,
        key_hash=hash_user_key(secret),
        encrypted_secret=encrypt_user_key(secret),
    )
    await db.commit()
    return AgentKeyCreateResponse(public_id=public_id, key=full_key)


async def regenerate_agent_key(db: AsyncSession, user_id: int) -> AgentKeyCreateResponse:
    key = await auth_repo.get_user_agent_key(db, user_id)
    if key is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "AGENT_KEY_NOT_FOUND", "尚未生成 Key，请先 POST /me/agent-key")
    public_id, secret, full_key = new_user_agent_key()
    await auth_repo.replace_user_agent_key(
        key,
        public_id=public_id,
        key_hash=hash_user_key(secret),
        encrypted_secret=encrypt_user_key(secret),
    )
    await db.commit()
    return AgentKeyCreateResponse(public_id=public_id, key=full_key)
