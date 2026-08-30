import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import ErrorCode, api_error
from app.core.ratelimit import rate_limit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import RefreshToken, User
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["认证"])


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _issue_tokens(db: AsyncSession, user: User) -> TokenResponse:
    access_token = create_access_token(user.id, user.username)
    refresh_token = create_refresh_token(user.id, user.username)

    db.add(
        RefreshToken(
            token_hash=_hash_refresh_token(refresh_token),
            user_id=user.id,
            expires_at=datetime.now(UTC).replace(tzinfo=None)
            + timedelta(days=settings.jwt_refresh_token_expire_days),
        )
    )
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    _rl: None = Depends(rate_limit("auth")),  # CR-21：认证接口限流
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    existing = await db.execute(
        select(User).where(
            (User.username == body.username)
            if not body.email
            else (User.username == body.username) | (User.email == body.email)
        )
    )
    if existing.scalar_one_or_none():
        detail = "用户名或邮箱已存在" if body.email else "用户名已存在"
        raise api_error(status.HTTP_409_CONFLICT, ErrorCode.AUTH_USER_EXISTS, detail)

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.flush()
    return await _issue_tokens(db, user)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    _rl: None = Depends(rate_limit("auth")),  # CR-21：认证接口限流
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    user = await db.execute(select(User).where(User.username == body.username))
    user = user.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_CREDENTIALS_INVALID, "用户名或密码错误")
    if user.status != "active":
        raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.AUTH_USER_DISABLED, "用户已被禁用")
    return await _issue_tokens(db, user)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    _rl: None = Depends(rate_limit("auth")),  # CR-21：认证接口限流
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    payload = decode_token(body.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_REFRESH_INVALID, "无效的 Refresh Token")

    stored = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == _hash_refresh_token(body.refresh_token)
        )
    )
    stored = stored.scalar_one_or_none()
    if stored is None or stored.revoked_at is not None:
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_REFRESH_INVALID, "Refresh Token 已失效")

    user = await db.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_USER_DISABLED, "用户不存在或已禁用")

    stored.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    return await _issue_tokens(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> None:
    stored = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh_token(body.refresh_token))
    )
    stored = stored.scalar_one_or_none()
    if stored is not None:
        stored.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()
