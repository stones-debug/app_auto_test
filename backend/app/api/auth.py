from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.ratelimit import rate_limit
from app.models import User
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    _rl: None = Depends(rate_limit("auth")),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    return await auth_service.register_user(db, body)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    _rl: None = Depends(rate_limit("auth")),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    return await auth_service.authenticate_user(db, body)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    _rl: None = Depends(rate_limit("auth")),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    return await auth_service.refresh_tokens(db, body)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> None:
    await auth_service.logout(db, body)
