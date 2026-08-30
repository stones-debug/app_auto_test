"""报告及报告详情数据访问。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Device,
    Execution,
    ExecutionExclusion,
    ExecutionLog,
    Project,
    Report,
    TestCase,
    TestSuite,
)


async def get_by_id(db: AsyncSession, report_id: int) -> Report | None:
    return await db.get(Report, report_id)


async def get_by_execution(db: AsyncSession, execution_id: int) -> Report | None:
    return (await db.execute(select(Report).where(Report.execution_id == execution_id))).scalar_one_or_none()


async def load_detail_rows(db: AsyncSession, execution_id: int):
    total = await db.scalar(select(func.count()).select_from(ExecutionLog).where(ExecutionLog.execution_id == execution_id)) or 0
    report = await get_by_execution(db, execution_id)
    exclusions = list((await db.execute(select(ExecutionExclusion).where(ExecutionExclusion.execution_id == execution_id).order_by(ExecutionExclusion.id))).scalars().all())
    return total, report, exclusions


async def list_logs(db: AsyncSession, execution_id: int, *, limit: int | None = None, reverse: bool = False):
    query = select(ExecutionLog).where(ExecutionLog.execution_id == execution_id).order_by(ExecutionLog.id.desc() if reverse else ExecutionLog.id)
    if limit is not None:
        query = query.limit(limit)
    rows = list((await db.execute(query)).scalars().all())
    return rows[::-1] if reverse else rows


async def update_path(db: AsyncSession, report: Report, path: str) -> Report:
    report.report_path = path
    return report


async def list_page(
    db: AsyncSession, *, project_ids: list[int] | tuple[int, ...], project_id: int | None,
    execution_id: int | None, status: str, type_: str, app_profile_id: int | None,
    app_release_id: int | None, keyword: str, created_from, created_to, offset: int, limit: int
):
    query = select(Report).join(Execution, Report.execution_id == Execution.id)
    query = query.where(Execution.project_id == project_id) if project_id is not None else query.where(Execution.project_id.in_(project_ids))
    if execution_id is not None:
        query = query.where(Report.execution_id == execution_id)
    if status:
        query = query.where(Execution.status == status)
    if type_:
        query = query.where(Execution.type == type_)
    if app_profile_id is not None:
        query = query.where(Execution.app_profile_id == app_profile_id)
    if app_release_id is not None:
        query = query.where(Execution.app_release_id == app_release_id)
    if created_from is not None:
        query = query.where(Report.created_at >= created_from)
    if created_to is not None:
        query = query.where(Report.created_at <= created_to)
    if keyword:
        try:
            keyword_int = int(keyword)
        except ValueError:
            keyword_int = -1
        query = query.where(Report.execution_id == keyword_int)
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    reports = list((await db.execute(query.order_by(Report.id.desc()).offset(offset).limit(limit))).scalars().all())
    execution_ids = {row.execution_id for row in reports}
    executions = {}
    if execution_ids:
        executions = {row.id: row for row in (await db.execute(select(Execution).where(Execution.id.in_(execution_ids)))).scalars().all()}
    case_ids = {row.case_id for row in executions.values() if row.case_id}
    suite_ids = {row.suite_id for row in executions.values() if row.suite_id}
    project_id_set = {row.project_id for row in executions.values()}
    device_ids = {row.device_id for row in executions.values() if row.device_id}
    cases = {row.id: row.name for row in (await db.execute(select(TestCase).where(TestCase.id.in_(case_ids)))).scalars().all()} if case_ids else {}
    suites = {row.id: row.name for row in (await db.execute(select(TestSuite).where(TestSuite.id.in_(suite_ids)))).scalars().all()} if suite_ids else {}
    projects = {row.id: row.name for row in (await db.execute(select(Project).where(Project.id.in_(project_id_set)))).scalars().all()} if project_id_set else {}
    devices = {row.id: row.name for row in (await db.execute(select(Device).where(Device.id.in_(device_ids)))).scalars().all()} if device_ids else {}
    return total, reports, executions, cases, suites, projects, devices
