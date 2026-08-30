"""仪表盘聚合查询。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentUser, Device, Execution, Project, ProjectMember


async def list_my_project_ids(db: AsyncSession, user_id: int) -> list[int]:
    owner_ids = select(Project.id).where(Project.owner_id == user_id)
    member_ids = select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
    rows = await db.execute(select(Project.id).where(Project.deleted_at.is_(None), (Project.id.in_(owner_ids)) | (Project.id.in_(member_ids))))
    return list(rows.scalars().all())


async def load_status_counts(db: AsyncSession, project_ids: list[int], since: datetime | None) -> dict[str, int]:
    condition = Execution.project_id.in_(project_ids)
    if since is not None:
        condition &= Execution.created_at >= since
    rows = await db.execute(select(Execution.status, func.count()).where(condition).group_by(Execution.status))
    return dict(rows.all())


async def load_execution_trend(db: AsyncSession, project_ids: list[int], since: datetime):
    day_col = func.to_char(func.date_trunc("day", Execution.created_at), "YYYY-MM-DD")
    rows = await db.execute(select(day_col.label("day"), Execution.status, func.count()).where(Execution.project_id.in_(project_ids), Execution.created_at >= since).group_by(day_col, Execution.status).order_by(day_col))
    return list(rows.all())


async def list_recent_executions(db: AsyncSession, project_ids: list[int], limit: int = 10):
    rows = await db.execute(select(Execution).where(Execution.project_id.in_(project_ids)).order_by(Execution.created_at.desc()).limit(limit))
    return list(rows.scalars().all())


async def load_device_counts(db: AsyncSession, *, user_id: int, is_admin: bool) -> tuple[int, int]:
    condition = True
    if not is_admin:
        bound_agents = select(AgentUser.agent_id).where(AgentUser.user_id == user_id)
        condition = Device.agent_id.in_(bound_agents)
    total = await db.scalar(select(func.count()).select_from(Device).where(condition)) or 0
    available = await db.scalar(select(func.count()).select_from(Device).where(condition, Device.status == "idle", Device.locked_by_execution.is_(None))) or 0
    return total, available
