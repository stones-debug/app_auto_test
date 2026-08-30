from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_internal_token
from app.core.database import get_db
from app.services import agent_service

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
    return await agent_service.set_internal_execution_state(db, execution_id, body.status)


@router.post("/ws/agents/{agent_id}/send")
async def forward_to_agent(
    agent_id: int,
    body: AgentMessageBody,
    _token: str = Depends(require_internal_token),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.forward_to_agent(db, agent_id, body.as_dict())
