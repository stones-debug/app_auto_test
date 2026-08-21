from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.models import Project, TestCase, TestElement, TestModule, User
from app.schemas.case import CaseCreate, CaseListItem, CaseOut, CasePage, CaseUpdate
from app.utils.pagination import get_pagination

router = APIRouter(tags=["用例管理"])


async def _get_case_or_404(case_id: int, db: AsyncSession) -> TestCase:
    case = await db.get(TestCase, case_id)
    if case is None or case.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
    return case


async def _check_module_belongs(project_id: int, module_id: int | None, db: AsyncSession) -> None:
    if module_id is None:
        return
    module = await db.get(TestModule, module_id)
    if module is None or module.deleted_at is not None or module.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模块不存在")


async def _check_elements_belong(
    project_id: int, steps: list | None, assertions: list | None, db: AsyncSession
) -> None:
    """CR-09：步骤/断言引用的 element_id 必须存在且属于当前项目。"""
    ids: set[int] = set()
    for item in [*(steps or []), *(assertions or [])]:
        element_id = item.get("element_id") if isinstance(item, dict) else None
        if element_id is not None:
            ids.add(int(element_id))
    if not ids:
        return
    rows = (
        await db.execute(select(TestElement).where(TestElement.id.in_(ids)))
    ).scalars().all()
    found = {r.id for r in rows}
    missing = ids - found
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"元素不存在: {sorted(missing)}"
        )
    cross = [r.id for r in rows if r.project_id != project_id]
    if cross:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"元素不属于该项目: {sorted(cross)}"
        )


@router.get("/projects/{project_id}/cases", response_model=CasePage)
async def list_cases(
    project_id: int,
    pagination=Depends(get_pagination),
    module_id: int | None = None,
    keyword: str = "",
    status_filter: str = "",
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    query = select(TestCase).where(
        TestCase.project_id == project_id,
        TestCase.deleted_at.is_(None),
    )
    count_query = select(func.count()).select_from(TestCase).where(
        TestCase.project_id == project_id,
        TestCase.deleted_at.is_(None),
    )
    if module_id is not None:
        query = query.where(TestCase.module_id == module_id)
        count_query = count_query.where(TestCase.module_id == module_id)
    if keyword:
        query = query.where(TestCase.name.ilike(f"%{keyword}%"))
        count_query = count_query.where(TestCase.name.ilike(f"%{keyword}%"))
    if status_filter:
        query = query.where(TestCase.status == status_filter)
        count_query = count_query.where(TestCase.status == status_filter)

    total = await db.scalar(count_query)
    rows = (
        await db.execute(
            query.order_by(TestCase.updated_at.desc())
            .offset(pagination.offset)
            .limit(pagination.limit)
        )
    ).scalars().all()

    # 填充 module_name
    module_ids = {r.module_id for r in rows if r.module_id}
    modules: dict[int, str] = {}
    if module_ids:
        mod_rows = (
            await db.execute(select(TestModule).where(TestModule.id.in_(module_ids)))
        ).scalars().all()
        modules = {m.id: m.name for m in mod_rows}

    items = []
    for case in rows:
        item = CaseListItem.model_validate(case)
        item.module_name = modules.get(case.module_id) if case.module_id else None
        items.append(item)
    return {"total": total or 0, "page": pagination.page, "page_size": pagination.page_size, "items": items}


@router.post("/projects/{project_id}/cases", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def create_case(
    project_id: int,
    body: CaseCreate,
    project: Project = Depends(get_editable_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_module_belongs(project_id, body.module_id, db)
    await _check_elements_belong(project_id, body.steps, body.assertions, db)
    case = TestCase(
        project_id=project_id,
        module_id=body.module_id,
        name=body.name,
        description=body.description,
        status=body.status,
        steps=body.steps,
        assertions=body.assertions,
        variables=body.variables,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return case


@router.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case_or_404(case_id, db)
    await get_project_permission(case.project_id, user, db)
    return case


@router.put("/cases/{case_id}", response_model=CaseOut)
async def update_case(
    case_id: int,
    body: CaseUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case_or_404(case_id, db)
    _project, role = await get_project_permission(case.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    await _check_module_belongs(case.project_id, body.module_id, db)
    if body.steps is not None or body.assertions is not None:
        await _check_elements_belong(case.project_id, body.steps, body.assertions, db)
    for field in ("name", "module_id", "description", "status", "steps", "assertions", "variables"):
        value = getattr(body, field)
        if value is not None:
            setattr(case, field, value)
    case.updated_by = user.id
    await db.commit()
    await db.refresh(case)
    return case


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case_or_404(case_id, db)
    _project, role = await get_project_permission(case.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    case.deleted_at = datetime.now(UTC)
    await db.commit()


@router.post("/cases/{case_id}/clone", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def clone_case(
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """克隆用例：复制 steps/assertions/variables，名称加后缀。"""
    case = await _get_case_or_404(case_id, db)
    _project, role = await get_project_permission(case.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")

    new_case = TestCase(
        project_id=case.project_id,
        module_id=case.module_id,
        name=f"{case.name} (副本)",
        description=case.description,
        status="draft",
        steps=[dict(s) for s in (case.steps or [])],
        assertions=[dict(a) for a in (case.assertions or [])],
        variables=dict(case.variables or {}),
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(new_case)
    await db.commit()
    await db.refresh(new_case)
    return new_case
