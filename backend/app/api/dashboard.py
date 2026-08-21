from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.models import (
    AgentUser,
    Device,
    Execution,
    Project,
    ProjectMember,
    User,
)
from app.schemas.dashboard import DashboardOverviewOut, DashboardStats

router = APIRouter(prefix="/dashboard", tags=["工作台"])

_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}
_STATUS_LABELS = ["queued", "running", "stopping", "passed", "failed", "error", "stopped", "cancelled"]


async def _visible_project_ids(db: AsyncSession, user: User, project_id: int | None) -> list[int] | None:
    """返回可见项目 id 列表；None 表示由查询条件限定（project_id 指定时单项目）。

    - 未指定 project_id：仅 owned/member 项目（公开未加入不进入个人工作台，V2 §5.2）。
    - 指定 project_id：校验 viewer+ 权限后返回 [project_id]。
    """
    if project_id is not None:
        await get_project_permission(project_id, user, db)
        return [project_id]
    owner_ids = select(Project.id).where(Project.owner_id == user.id)
    member_ids = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    rows = (
        await db.execute(
            select(Project.id).where(
                Project.deleted_at.is_(None),
                (Project.id.in_(owner_ids)) | (Project.id.in_(member_ids)),
            )
        )
    ).scalars().all()
    return list(rows)


async def _status_counts(db: AsyncSession, project_ids: list[int], since: datetime | None) -> dict[str, int]:
    cond = Execution.project_id.in_(project_ids)
    if since is not None:
        cond = cond & (Execution.created_at >= since)
    rows = (
        await db.execute(
            select(Execution.status, func.count()).where(cond).group_by(Execution.status)
        )
    ).all()
    counts = {label: 0 for label in _STATUS_LABELS}
    for st, cnt in rows:
        counts[st] = cnt
    return counts


async def _trend(db: AsyncSession, project_ids: list[int], since: datetime) -> list[dict]:
    day_col = func.date_trunc("day", Execution.created_at)
    rows = (
        await db.execute(
            select(day_col.label("day"), Execution.status, func.count())
            .where(Execution.project_id.in_(project_ids), Execution.created_at >= since)
            .group_by(day_col, Execution.status)
            .order_by(day_col)
        )
    ).all()
    by_day: dict = {}
    for day, st, cnt in rows:
        key = day.date().isoformat()
        bucket = by_day.setdefault(key, {"date": key, "total": 0, "passed": 0, "failed": 0, "error": 0})
        bucket["total"] += cnt
        if st == "passed":
            bucket["passed"] += cnt
        elif st == "failed":
            bucket["failed"] += cnt
        elif st == "error":
            bucket["error"] += cnt
    return list(by_day.values())


async def _recent_executions(db: AsyncSession, project_ids: list[int], limit: int = 10) -> list[dict]:
    rows = (
        await db.execute(
            select(Execution)
            .where(Execution.project_id.in_(project_ids))
            .order_by(Execution.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    items = []
    for e in rows:
        items.append(
            {
                "id": e.id,
                "project_id": e.project_id,
                "type": e.type,
                "status": e.status,
                "case_id": e.case_id,
                "suite_id": e.suite_id,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
        )
    return items


@router.get("/overview", response_model=DashboardOverviewOut)
async def dashboard_overview(
    range: str = "7d",
    project_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    days = _RANGE_DAYS.get(range, 7)
    effective_range = range if range in _RANGE_DAYS else "7d"
    since = datetime.now(UTC) - timedelta(days=days)
    project_ids = await _visible_project_ids(db, user, project_id)
    if not project_ids:
        return DashboardOverviewOut(
            range=effective_range,
            generated_at=datetime.now(UTC).isoformat(),
            stats=DashboardStats(),
            status_counts={label: 0 for label in _STATUS_LABELS},
            trend=[],
            recent_executions=[],
        )

    counts = await _status_counts(db, project_ids, since)
    period_total = sum(counts.values())
    denom = counts["passed"] + counts["failed"] + counts["error"]
    success_rate = round(counts["passed"] / denom * 100, 2) if denom else 0
    active = counts["running"] + counts["stopping"]

    # 设备统计：平台管理员看全部；普通用户看已绑定 Agent 下的设备
    device_cond = True
    if not user.is_admin:
        bound_agents = select(AgentUser.agent_id).where(AgentUser.user_id == user.id)
        device_cond = Device.agent_id.in_(bound_agents)
    device_count = await db.scalar(
        select(func.count()).select_from(Device).where(device_cond)
    ) or 0
    available_device_count = await db.scalar(
        select(func.count()).select_from(Device).where(
            device_cond, Device.status == "idle", Device.locked_by_execution.is_(None)
        )
    ) or 0

    project_count = len(project_ids)

    return DashboardOverviewOut(
        range=effective_range,
        generated_at=datetime.now(UTC).isoformat(),
        stats=DashboardStats(
            project_count=project_count,
            period_execution_count=period_total,
            active_execution_count=active,
            success_rate=success_rate,
            available_device_count=available_device_count,
            device_count=device_count,
        ),
        status_counts=counts,
        trend=await _trend(db, project_ids, since),
        recent_executions=await _recent_executions(db, project_ids),
    )
