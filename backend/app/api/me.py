"""Windows 方案 §3.2：当前用户的 Agent Key 管理（/api/me/agent-key）。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.agent import AgentKeyCreateResponse, AgentKeyOut
from app.services import auth_service

router = APIRouter(prefix="/me", tags=["个人中心"])


@router.get("/agent-key", response_model=AgentKeyOut)
async def get_my_agent_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.get_agent_key(db, user.id)


@router.post("/agent-key", response_model=AgentKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_my_agent_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.create_agent_key(db, user.id)


@router.post("/agent-key/regenerate", response_model=AgentKeyCreateResponse)
async def regenerate_my_agent_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.regenerate_agent_key(db, user.id)
