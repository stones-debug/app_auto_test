from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models import Agent, Execution

router = APIRouter(prefix="/internal", tags=["内部接口"])


async def _check_internal_token(x_internal_token: str = Header(...)) -> str:
    if x_internal_token != settings.internal_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="内部令牌无效")
    return x_internal_token


class InternalStateBody(BaseModel):
    status: str


class AgentMessageBody(BaseModel):
    type: str
    execution_id: int | None = None
    payload: dict | None = None


@router.post("/executions/{execution_id}/state")
async def set_execution_state(
    execution_id: int,
    body: InternalStateBody,
    _token: str = Depends(_check_internal_token),
    db: AsyncSession = Depends(get_db),
):
    """Worker → FastAPI 状态推进通知（Step 18 用于 WS 广播）。"""
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行记录不存在")
    now = datetime.now(UTC)
    execution.status = body.status
    if body.status == "running" and execution.started_at is None:
        execution.started_at = now
    if body.status in {"passed", "failed", "error", "stopped", "cancelled"}:
        execution.finished_at = now
    await db.commit()
    return {"execution_id": execution.id, "status": execution.status}


@router.post("/ws/agents/{agent_id}/send")
async def forward_to_agent(
    agent_id: int,
    body: AgentMessageBody,
    _token: str = Depends(_check_internal_token),
    db: AsyncSession = Depends(get_db),
):
    """Worker → Agent 消息中转。Step 18 接入 WS 网关后实现真正的转发。"""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 不存在")
    if agent.status != "online":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent 不在线")
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WS 网关未就绪（Step 18 实现）")
