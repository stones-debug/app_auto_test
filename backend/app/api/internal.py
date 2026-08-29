from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_internal_token
from app.core.database import get_db
from app.models import Agent, Execution

router = APIRouter(prefix="/internal", tags=["内部接口"])


class InternalStateBody(BaseModel):
    status: str


class AgentMessageBody(BaseModel):
    type: str
    execution_id: int | None = None
    model_config = {"extra": "allow"}

    def as_dict(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


@router.post("/executions/{execution_id}/state")
async def set_execution_state(
    execution_id: int,
    body: InternalStateBody,
    _token: str = Depends(require_internal_token),
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
    _token: str = Depends(require_internal_token),
    db: AsyncSession = Depends(get_db),
):
    """Worker → Agent 消息中转：经 WS 网关转发给 Agent。"""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 不存在")
    from app.ws.managers import agent_manager

    sent = await agent_manager.send(agent_id, body.as_dict())
    if not sent:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent 不在线")
    return {"sent": True, "agent_id": agent_id, "type": body.type}
