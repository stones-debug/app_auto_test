from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.models import User
from app.repositories import dashboard as dashboard_repo
from app.schemas.dashboard import DashboardOverviewOut, DashboardStats

router = APIRouter(prefix="/dashboard", tags=["工作台"])

_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}
_STATUS_LABELS = ["queued", "running", "stopping", "passed", "failed", "error", "stopped", "cancelled"]


async def _my_project_ids(db: AsyncSession, user: User, project_id: int | None) -> list[int] | None:
    if project_id is not None:
        await get_project_permission(project_id, user, db)
        return [project_id]
    return await dashboard_repo.list_my_project_ids(db, user.id)


async def _status_counts(db: AsyncSession, project_ids: list[int], since: datetime | None) -> dict[str, int]:
    counts = {label: 0 for label in _STATUS_LABELS}
    counts.update(await dashboard_repo.load_status_counts(db, project_ids, since))
    return counts


async def _trend(db: AsyncSession, project_ids: list[int], since: datetime) -> list[dict]:
    by_day: dict[str, dict] = {}
    for day_value, status, count in await dashboard_repo.load_execution_trend(db, project_ids, since):
        key = str(day_value)
        bucket = by_day.setdefault(key, {"date": key, "total": 0, "passed": 0, "failed": 0, "error": 0})
        bucket["total"] += count
        if status in ("passed", "failed", "error"):
            bucket[status] += count
    output = []
    day = since.astimezone().date()
    today = datetime.now(UTC).astimezone().date()
    while day <= today:
        key = day.isoformat()
        output.append(by_day.pop(key, {"date": key, "total": 0, "passed": 0, "failed": 0, "error": 0}))
        day += timedelta(days=1)
    return output


async def _recent_executions(db: AsyncSession, project_ids: list[int], limit: int = 10) -> list[dict]:
    return [{"id": item.id, "project_id": item.project_id, "type": item.type, "status": item.status, "case_id": item.case_id, "suite_id": item.suite_id, "created_at": item.created_at.isoformat() if item.created_at else None} for item in await dashboard_repo.list_recent_executions(db, project_ids, limit)]


@router.get("/overview", response_model=DashboardOverviewOut)
async def dashboard_overview(range: str = "7d", project_id: int | None = None, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    days = _RANGE_DAYS.get(range, 7)
    effective_range = range if range in _RANGE_DAYS else "7d"
    since = datetime.now(UTC) - timedelta(days=days)
    project_ids = await _my_project_ids(db, user, project_id)
    if not project_ids:
        return DashboardOverviewOut(range=effective_range, generated_at=datetime.now(UTC).isoformat(), stats=DashboardStats(), status_counts={label: 0 for label in _STATUS_LABELS}, trend=[], recent_executions=[])
    counts = await _status_counts(db, project_ids, since)
    total = sum(counts.values())
    denominator = counts["passed"] + counts["failed"] + counts["error"]
    device_count, available_count = await dashboard_repo.load_device_counts(db, user_id=user.id, is_admin=user.is_admin)
    return DashboardOverviewOut(
        range=effective_range, generated_at=datetime.now(UTC).isoformat(),
        stats=DashboardStats(project_count=len(project_ids), period_execution_count=total, active_execution_count=counts["running"] + counts["stopping"], success_rate=round(counts["passed"] / denominator * 100, 2) if denominator else 0, available_device_count=available_count, device_count=device_count),
        status_counts=counts, trend=await _trend(db, project_ids, since), recent_executions=await _recent_executions(db, project_ids),
    )
