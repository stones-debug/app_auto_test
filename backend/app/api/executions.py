from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission, require_project_write
from app.core.database import get_db
from app.models import Execution, ExecutionCase, TestCase, TestSuite, User
from app.schemas.execution import (
    BatchExecutionCreate,
    ExecutionCaseOut,
    ExecutionCreate,
    ExecutionDetail,
    ExecutionListItem,
    ExecutionLogOut,
    ExecutionLogPage,
    ExecutionOut,
    ExecutionPage,
)
from app.services import execution_service
from app.utils.pagination import get_pagination

router = APIRouter(tags=["执行管理"])


async def _get_execution_or_404(execution_id: int, db: AsyncSession) -> Execution:
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行记录不存在")
    return execution


async def _require_execution_access(execution: Execution, user: User, db: AsyncSession) -> None:
    """读权限：项目可访问即可（viewer 只读）。"""
    await get_project_permission(execution.project_id, user, db)


async def _require_execution_write(execution: Execution, user: User, db: AsyncSession) -> None:
    """写权限（CR-04）：创建/停止/重试要求 owner/admin/member。"""
    await require_project_write(execution.project_id, user, db)


async def _get_case_or_404(case_id: int, db: AsyncSession) -> TestCase:
    case = await db.get(TestCase, case_id)
    if case is None or case.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
    return case


async def _get_suite_or_404(suite_id: int, db: AsyncSession) -> TestSuite:
    suite = await db.get(TestSuite, suite_id)
    if suite is None or suite.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
    return suite


@router.post(
    "/executions/cases/{case_id}",
    response_model=ExecutionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_execution(
    case_id: int,
    body: ExecutionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case_or_404(case_id, db)
    await require_project_write(case.project_id, user, db)
    return await execution_service.create_case_execution(
        db, case, user, body.device_id, body.parameters, body.timeout_seconds
    )


@router.post(
    "/executions/suites/batch",
    response_model=ExecutionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch_execution(
    body: BatchExecutionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suites: list[TestSuite] = []
    project_id: int | None = None
    for sid in body.suite_ids:
        suite = await _get_suite_or_404(sid, db)
        if project_id is None:
            project_id = suite.project_id
        elif suite.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="批量执行的套件必须属于同一项目")
        suites.append(suite)
    if project_id is not None:
        await require_project_write(project_id, user, db)
    return await execution_service.create_batch_execution(
        db, suites, user, body.device_id, body.parameters, body.timeout_seconds
    )


@router.post(
    "/executions/suites/{suite_id}",
    response_model=ExecutionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_suite_execution(
    suite_id: int,
    body: ExecutionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await _get_suite_or_404(suite_id, db)
    await require_project_write(suite.project_id, user, db)
    return await execution_service.create_suite_execution(
        db, suite, user, body.device_id, body.parameters, body.timeout_seconds
    )


@router.get("/executions", response_model=ExecutionPage)
async def list_executions(
    project_id: int | None = None,
    status_filter: str = "",
    execution_type: str = "",
    pagination=Depends(get_pagination),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models import Project, ProjectMember

    if project_id is not None:
        await get_project_permission(project_id, user, db)
        query = select(Execution).where(Execution.project_id == project_id)
        count_query = select(func.count()).select_from(Execution).where(Execution.project_id == project_id)
    else:
        member_projects = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        owner_projects = select(Project.id).where(Project.owner_id == user.id)
        query = select(Execution).where(
            (Execution.project_id.in_(owner_projects)) | (Execution.project_id.in_(member_projects))
        )
        count_query = select(func.count()).select_from(query.subquery())

    if status_filter:
        query = query.where(Execution.status == status_filter)
    if execution_type:
        query = query.where(Execution.type == execution_type)

    total = await db.scalar(count_query)
    rows = (
        await db.execute(
            query.order_by(Execution.id.desc()).offset(pagination.offset).limit(pagination.limit)
        )
    ).scalars().all()

    case_ids = {r.case_id for r in rows if r.case_id}
    suite_ids = {r.suite_id for r in rows if r.suite_id}
    case_names: dict[int, str] = {}
    suite_names: dict[int, str] = {}
    if case_ids:
        cases = (await db.execute(select(TestCase).where(TestCase.id.in_(case_ids)))).scalars().all()
        case_names = {c.id: c.name for c in cases}
    if suite_ids:
        suites = (await db.execute(select(TestSuite).where(TestSuite.id.in_(suite_ids)))).scalars().all()
        suite_names = {s.id: s.name for s in suites}

    items = []
    for row in rows:
        item = ExecutionListItem.model_validate(row)
        item.case_name = case_names.get(row.case_id) if row.case_id else None
        item.suite_name = suite_names.get(row.suite_id) if row.suite_id else None
        items.append(item)
    return {"total": total or 0, "page": pagination.page, "page_size": pagination.page_size, "items": items}


@router.get("/executions/{execution_id}", response_model=ExecutionDetail)
async def get_execution(
    execution_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_access(execution, user, db)
    cases = (
        await db.execute(
            select(ExecutionCase)
            .where(ExecutionCase.execution_id == execution_id)
            .order_by(ExecutionCase.id)
        )
    ).scalars().all()
    detail = ExecutionDetail.model_validate(execution)
    detail.cases = [ExecutionCaseOut.model_validate(c) for c in cases]
    return detail


@router.get("/executions/{execution_id}/logs", response_model=ExecutionLogPage)
async def get_execution_logs(
    execution_id: int,
    after_timestamp: datetime | None = None,
    pagination=Depends(get_pagination),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_access(execution, user, db)
    rows, total = await execution_service.get_execution_logs(
        db, execution_id, after_timestamp, pagination.offset, pagination.limit
    )
    items = [ExecutionLogOut.model_validate(r) for r in rows]
    return {"total": total, "page": pagination.page, "page_size": pagination.page_size, "items": items}


@router.post("/executions/{execution_id}/stop")
async def stop_execution(
    execution_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_write(execution, user, db)
    new_status = await execution_service.stop_execution(db, execution)
    return {"execution_id": execution.id, "status": new_status}


@router.post("/executions/{execution_id}/retry", response_model=ExecutionOut, status_code=status.HTTP_201_CREATED)
async def retry_execution(
    execution_id: int,
    device_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_write(execution, user, db)
    return await execution_service.retry_execution(db, execution, user, device_id)
