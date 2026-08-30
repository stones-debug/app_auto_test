from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.models import Project, User
from app.schemas.case import (
    CaseBatchDeleteRequest,
    CaseBatchDeleteResponse,
    CaseCreate,
    CaseListItem,
    CaseOut,
    CasePage,
    CaseUpdate,
)
from app.services import case_service
from app.utils.pagination import get_pagination

router = APIRouter(tags=["用例管理"])


def _ids_from_query_values(values: list[str]) -> list[int]:
    result: list[int] = []
    for raw in values:
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                result.append(int(part))
    return result


@router.get("/projects/{project_id}/cases", response_model=CasePage)
async def list_cases(
    project_id: int,
    pagination=Depends(get_pagination),
    module_id: int | None = None,
    keyword: str = "",
    status: str = "",
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    total, rows, modules, last_execution = await case_service.list_page(
        db,
        project_id=project_id,
        module_id=module_id,
        keyword=keyword,
        status_=status,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    items = []
    for case in rows:
        item = CaseListItem.model_validate(case)
        item.module_name = modules.get(case.module_id) if case.module_id else None
        item.step_count = len(case.steps or [])
        item.assertion_count = sum(
            len(step.get("assertions") or []) for step in (case.steps or [])
        )
        if case.id in last_execution:
            item.last_execution_status, item.last_execution_at = last_execution[case.id]
        items.append(item)
    return {
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "items": items,
    }


@router.post("/projects/{project_id}/cases", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def create_case(
    project_id: int,
    body: CaseCreate,
    project: Project = Depends(get_editable_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await case_service.create(db, project_id=project_id, body=body, user_id=user.id)


@router.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await case_service.get_or_404(db, case_id)
    await get_project_permission(case.project_id, user, db)
    return case


@router.put("/cases/{case_id}", response_model=CaseOut)
async def update_case(
    case_id: int,
    body: CaseUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await case_service.get_or_404(db, case_id)
    _project, role = await get_project_permission(case.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    return await case_service.update(db, case=case, body=body, user_id=user.id)


async def _batch_delete_cases(
    project_id: int, ids: list[int], user: User, db: AsyncSession
):
    _project, role = await get_project_permission(project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    return await case_service.soft_delete_many(db, ids=ids, project_id=project_id)


def _batch_response(rows) -> dict[str, object]:
    return {
        "deleted": len(rows),
        "deleted_count": len(rows),
        "ids": [case.id for case in rows],
    }


@router.post("/projects/{project_id}/cases/batch", response_model=CaseBatchDeleteResponse)
@router.post("/projects/{project_id}/cases/batch-delete", response_model=CaseBatchDeleteResponse)
async def batch_delete_cases(
    project_id: int,
    body: CaseBatchDeleteRequest | None = None,
    ids: list[str] = Query(default=[]),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    delete_ids = body.ids if body is not None and body.ids else _ids_from_query_values(ids)
    if not delete_ids:
        raise HTTPException(status_code=422, detail="请提供至少一个用例 ID")
    return _batch_response(await _batch_delete_cases(project_id, delete_ids, user, db))


@router.delete("/projects/{project_id}/cases/batch-delete", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/projects/{project_id}/cases/batch", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/projects/{project_id}/cases", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cases_batch(
    project_id: int,
    body: CaseBatchDeleteRequest | None = None,
    ids: list[str] = Query(default=[]),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    delete_ids = body.ids if body is not None and body.ids else _ids_from_query_values(ids)
    if not delete_ids:
        raise HTTPException(status_code=422, detail="请提供至少一个用例 ID")
    await _batch_delete_cases(project_id, delete_ids, user, db)


async def _batch_delete_cases_global(ids: list[int], user: User, db: AsyncSession):
    rows = await case_service.load_deletable(db, ids=ids)
    for project_id in {case.project_id for case in rows}:
        _project, role = await get_project_permission(project_id, user, db)
        if role not in ("owner", "admin", "member"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    return await case_service.soft_delete_many(db, ids=ids)


@router.post("/cases/batch", response_model=CaseBatchDeleteResponse)
@router.post("/cases/batch-delete", response_model=CaseBatchDeleteResponse)
async def batch_delete_cases_global(
    body: CaseBatchDeleteRequest | None = None,
    ids: list[str] = Query(default=[]),
    project_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    delete_ids = body.ids if body is not None and body.ids else _ids_from_query_values(ids)
    if not delete_ids:
        raise HTTPException(status_code=422, detail="请提供至少一个用例 ID")
    effective_project_id = (body.project_id if body is not None else None) or project_id
    rows = (
        await _batch_delete_cases(effective_project_id, delete_ids, user, db)
        if effective_project_id is not None
        else await _batch_delete_cases_global(delete_ids, user, db)
    )
    return _batch_response(rows)


@router.delete("/cases/batch-delete", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/cases/batch", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cases_batch_global(
    body: CaseBatchDeleteRequest | None = None,
    ids: list[str] = Query(default=[]),
    project_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    delete_ids = body.ids if body is not None and body.ids else _ids_from_query_values(ids)
    if not delete_ids:
        raise HTTPException(status_code=422, detail="请提供至少一个用例 ID")
    effective_project_id = (body.project_id if body is not None else None) or project_id
    if effective_project_id is not None:
        await _batch_delete_cases(effective_project_id, delete_ids, user, db)
    else:
        await _batch_delete_cases_global(delete_ids, user, db)


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await case_service.get_or_404(db, case_id)
    _project, role = await get_project_permission(case.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    await case_service.soft_delete(db, case=case)


@router.post("/cases/{case_id}/clone", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def clone_case(
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await case_service.get_or_404(db, case_id)
    _project, role = await get_project_permission(case.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    return await case_service.clone(db, source=case, user_id=user.id)
