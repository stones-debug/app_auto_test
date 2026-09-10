from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.config import reports_dir
from app.core.database import get_db
from app.models import Report, User
from app.repositories import executions as executions_repo
from app.repositories import reports as reports_repo
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
from app.services.access_scope import visible_project_ids
from app.utils.pagination import get_pagination

router = APIRouter(tags=["报告管理"])


async def _get_report_or_404(report_id: int, db: AsyncSession) -> Report:
    report = await reports_repo.get_by_id(db, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")
    return report


async def _require_report_access(report: Report, user: User, db: AsyncSession) -> None:
    execution = await executions_repo.get_by_id(db, report.execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联执行不存在")
    await get_project_permission(execution.project_id, user, db)


@router.get("/reports", response_model=ReportPage)
async def list_reports(project_id: int | None = None, execution_id: int | None = None, status: str = "", type: str = "", app_profile_id: int | None = None, app_release_id: int | None = None, keyword: str = "", created_from: datetime | None = None, created_to: datetime | None = None, pagination=Depends(get_pagination), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if project_id is not None:
        await get_project_permission(project_id, user, db)
        project_ids = [project_id]
    else:
        project_ids = list(await visible_project_ids(db, user.id))
    total, rows, executions, case_names, suite_names, project_names, device_names = await reports_repo.list_page(
        db, project_ids=project_ids, project_id=project_id, execution_id=execution_id,
        status=status, type_=type, app_profile_id=app_profile_id, app_release_id=app_release_id,
        keyword=keyword, created_from=created_from, created_to=created_to,
        offset=pagination.offset, limit=pagination.limit,
    )
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
            item.app_profile_name = execution.app_profile_name_snapshot
            item.app_release_version = execution.app_release_version_snapshot
            item.not_applicable = row.not_applicable or 0
        item.has_report = bool(row.report_path)
        items.append(item)
    return {"total": total, "page": pagination.page, "page_size": pagination.page_size, "items": items}


async def _build_detail(db: AsyncSession, execution_id: int) -> ReportDetailOut:
    detail = await report_service.get_report_detail(db, execution_id)
    suites = [ReportSuiteOut(**s) for s in detail["suites"]]
    return ReportDetailOut(execution=detail["execution"], report=ReportSummaryOut(**detail["report"]), suites=suites, cases=[] if suites else [ReportCaseOut(**c) for c in detail["cases"]], exclusions=[ReportExclusionOut(**item) for item in detail["exclusions"]], logs=[ReportLogOut(**item) for item in detail["logs"]], logs_total=detail["logs_total"], logs_truncated=detail["logs_truncated"])


@router.get("/reports/{report_id}", response_model=ReportDetailOut)
async def get_report(report_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    report = await _get_report_or_404(report_id, db)
    await _require_report_access(report, user, db)
    return await _build_detail(db, report.execution_id)


@router.get("/reports/{report_id}/detail", response_model=ReportDetailOut)
async def get_report_detail(report_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    report = await _get_report_or_404(report_id, db)
    await _require_report_access(report, user, db)
    return await _build_detail(db, report.execution_id)


@router.get("/reports/{report_id}/download")
async def download_report(report_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    report = await _get_report_or_404(report_id, db)
    await _require_report_access(report, user, db)
    # 报告 HTML 可能在首次下载或模板版本变化时重新生成。禁止浏览器复用旧的
    # Range/If-Range 缓存响应，避免缓存文件与当前文件内容不一致导致 ERR_FAILED 206。
    return FileResponse(
        await report_service.render_report_html(db, report.execution_id),
        media_type="text/html",
        filename=f"execution_{report.execution_id}_report.html",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/reports/{report_id}/files/{filename:path}")
async def report_file(report_id: int, filename: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    report = await _get_report_or_404(report_id, db)
    await _require_report_access(report, user, db)
    base = (reports_dir() / f"execution_{report.execution_id}").resolve()
    target = (base / filename).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    return FileResponse(target)
