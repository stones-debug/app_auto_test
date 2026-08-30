"""内部执行状态和 Agent 执行绑定查询。"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, Execution


async def get_by_id(db: AsyncSession, execution_id: int) -> Execution | None:
    return await db.get(Execution, execution_id)


async def update_state(
    execution: Execution, *, status: str, now: datetime
) -> Execution:
    execution.status = status
    if status == "running" and execution.started_at is None:
        execution.started_at = now
    if status in {"passed", "failed", "error", "stopped", "cancelled"}:
        execution.finished_at = now
    return execution


async def get_device_for_execution(
    db: AsyncSession, execution: Execution
) -> Device | None:
    return await db.get(Device, execution.device_id) if execution.device_id else None
