from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.config import reports_dir
from app.core.database import get_db
from app.models import Execution, Project, Report, TestCase, TestSuite, User
from app.schemas.report import (
    ReportCaseOut,
    ReportDetailOut,
    ReportExclusionOut,
    ReportListItem,
    ReportLogOut,
    ReportPage,
    ReportSuiteOut,
    ReportSummaryOut,
)
from app.services import report_service
from app.utils.pagination import get_pagination

router = APIRouter(tags=["报告管理"])


async def _get_report_or_404(report_id: int, db: AsyncSession) -> Report:
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")
    return report


async def _require_report_access(report: Report, user: User, db: AsyncSession) -> None:
    execution = await db.get(Execution, report.execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联执行不存在")
    await get_project_permission(execution.project_id, user, db)


@router.get("/reports", response_model=ReportPage)
async def list_reports(
    project_id: int | None = None,
    execution_id: int | None = None,
    status: str = "",
    type: str = "",
    app_profile_id: int | None = None,
    app_release_id: int | None = None,
    keyword: str = "",
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    pagination=Depends(get_pagination),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if project_id is not None:
        await get_project_permission(project_id, user, db)
        query = select(Report).join(Execution, Report.execution_id == Execution.id).where(
            Execution.project_id == project_id
        )
    else:
        # Step 9：owner/member/public viewer 使用统一可见范围（EXISTS，避免成员重复行）
        from app.services.access_scope import visible_project_ids

        query = (
            select(Report)
            .join(Execution, Report.execution_id == Execution.id)
            .where(Execution.project_id.in_(await visible_project_ids(db, user.id)))
        )
    if execution_id is not None:
        query = query.where(Report.execution_id == execution_id)
    if status:
        query = query.where(Execution.status == status)
    if type:
        query = query.where(Execution.type == type)
    if app_profile_id is not None:
        query = query.where(Execution.app_profile_id == app_profile_id)
    if app_release_id is not None:
        query = query.where(Execution.app_release_id == app_release_id)
    if created_from is not None:
        query = query.where(Report.created_at >= created_from)
    if created_to is not None:
        query = query.where(Report.created_at <= created_to)
    if keyword:
        # CR-15：keyword 按执行 ID 过滤（前端“按执行 ID 搜索”）
        try:
            keyword_int = int(keyword)
        except ValueError:
            keyword_int = -1
        query = query.where(Report.execution_id == keyword_int)

    # CR-15：count 与 items 从同一过滤后的 base query 派生
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)
    rows = (
        await db.execute(query.order_by(Report.id.desc()).offset(pagination.offset).limit(pagination.limit))
    ).scalars().all()

    exec_ids = {r.execution_id for r in rows}
    executions: dict[int, Execution] = {}
    if exec_ids:
        executions = {
            e.id: e
            for e in (await db.execute(select(Execution).where(Execution.id.in_(exec_ids)))).scalars().all()
        }
    case_ids = {e.case_id for e in executions.values() if e.case_id}
    suite_ids = {e.suite_id for e in executions.values() if e.suite_id}
    project_ids = {e.project_id for e in executions.values()}
    device_ids = {e.device_id for e in executions.values() if e.device_id}
    case_names: dict[int, str] = {}
    suite_names: dict[int, str] = {}
    project_names: dict[int, str] = {}
    device_names: dict[int, str] = {}
    if case_ids:
        case_names = {c.id: c.name for c in (await db.execute(select(TestCase).where(TestCase.id.in_(case_ids)))).scalars().all()}
    if suite_ids:
        suite_names = {s.id: s.name for s in (await db.execute(select(TestSuite).where(TestSuite.id.in_(suite_ids)))).scalars().all()}
    if project_ids:
        project_names = {p.id: p.name for p in (await db.execute(select(Project).where(Project.id.in_(project_ids)))).scalars().all()}
    if device_ids:
        from app.models import Device

        device_names = {d.id: d.name for d in (await db.execute(select(Device).where(Device.id.in_(device_ids)))).scalars().all()}

    items = []
    for row in rows:
        execution = executions.get(row.execution_id)
        item = ReportListItem.model_validate(row)
        if execution is not None:
            item.project_id = execution.project_id
            item.project_name = project_names.get(execution.project_id)
            item.device_name = device_names.get(execution.device_id) if execution.device_id else None
            item.execution_status = execution.status
            item.execution_type = execution.type
            item.case_name = case_names.get(execution.case_id) if execution.case_id else None
            item.suite_name = suite_names.get(execution.suite_id) if execution.suite_id else None
            item.finished_at = execution.finished_at
            # 方案 §7.3：档案/版本快照 + N/A 数量
            item.app_profile_name = execution.app_profile_name_snapshot
            item.app_release_version = execution.app_release_version_snapshot
            item.not_applicable = row.not_applicable or 0
        item.has_report = bool(row.report_path)
        items.append(item)
    return {"total": total or 0, "page": pagination.page, "page_size": pagination.page_size, "items": items}


@router.get("/reports/{report_id}", response_model=ReportDetailOut)
async def get_report(
    report_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report_or_404(report_id, db)
    await _require_report_access(report, user, db)
    return await _build_detail(db, report.execution_id)


async def _build_detail(db: AsyncSession, execution_id: int) -> ReportDetailOut:
    detail = await report_service.get_report_detail(db, execution_id)
    suites = [ReportSuiteOut(**s) for s in detail["suites"]]
    return ReportDetailOut(
        execution=detail["execution"],
        report=ReportSummaryOut(**detail["report"]),
        suites=suites,
        # suites 已完整包含用例树时不再通过 API 重复发送同一批步骤/断言。
        cases=[] if suites else [ReportCaseOut(**c) for c in detail["cases"]],
        exclusions=[ReportExclusionOut(**item) for item in detail["exclusions"]],
        logs=[ReportLogOut(**log_item) for log_item in detail["logs"]],
        logs_total=detail["logs_total"],
        logs_truncated=detail["logs_truncated"],
    )


@router.get("/reports/{report_id}/detail", response_model=ReportDetailOut)
async def get_report_detail(
    report_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report_or_404(report_id, db)
    await _require_report_access(report, user, db)
    return await _build_detail(db, report.execution_id)


@router.get("/reports/{report_id}/download")
async def download_report(
    report_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report_or_404(report_id, db)
    await _require_report_access(report, user, db)
    html_path = await report_service.render_report_html(db, report.execution_id)
    return FileResponse(
        html_path,
        media_type="text/html",
        filename=f"execution_{report.execution_id}_report.html",
    )


@router.get("/reports/{report_id}/files/{filename:path}")
async def report_file(
    report_id: int,
    filename: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report_or_404(report_id, db)
    await _require_report_access(report, user, db)
    base = (reports_dir() / f"execution_{report.execution_id}").resolve()
    target = (base / filename).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    return FileResponse(target)
