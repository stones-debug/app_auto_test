"""Windows 方案 §3.2：当前用户的 Agent Key 管理（/api/me/agent-key）。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import (
    decrypt_user_key,
    encrypt_user_key,
    hash_user_key,
    new_user_agent_key,
)
from app.models import User, UserAgentKey
from app.schemas.agent import AgentKeyCreateResponse, AgentKeyOut

router = APIRouter(prefix="/me", tags=["个人中心"])


async def _get_key_or_none(db: AsyncSession, user_id: int) -> UserAgentKey | None:
    return (
        await db.execute(select(UserAgentKey).where(UserAgentKey.user_id == user_id))
    ).scalar_one_or_none()


@router.get("/agent-key", response_model=AgentKeyOut)
async def get_my_agent_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """解密并返回当前用户 Key；不存在时返回空状态。"""
    key = await _get_key_or_none(db, user.id)
    if key is None:
        return AgentKeyOut(exists=False)
    try:
        secret = decrypt_user_key(key.encrypted_secret)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return AgentKeyOut(exists=True, public_id=key.public_id, key=f"uak_{key.public_id}_{secret}")


@router.post("/agent-key", response_model=AgentKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_my_agent_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """首次生成 Key。"""
    if await _get_key_or_none(db, user.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Key 已存在，请使用 regenerate")
    public_id, secret, full_key = new_user_agent_key()
    db.add(
        UserAgentKey(
            user_id=user.id,
            public_id=public_id,
            key_hash=hash_user_key(secret),
            encrypted_secret=encrypt_user_key(secret),
        )
    )
    await db.commit()
    return AgentKeyCreateResponse(public_id=public_id, key=full_key)


@router.post("/agent-key/regenerate", response_model=AgentKeyCreateResponse)
async def regenerate_my_agent_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """生成新 Key；旧 Key 不能再新增绑定，但已有 Agent 绑定继续有效。"""
    key = await _get_key_or_none(db, user.id)
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="尚未生成 Key，请先 POST /me/agent-key")
    public_id, secret, full_key = new_user_agent_key()
    key.public_id = public_id
    key.key_hash = hash_user_key(secret)
    key.encrypted_secret = encrypt_user_key(secret)
    await db.commit()
    return AgentKeyCreateResponse(public_id=public_id, key=full_key)
