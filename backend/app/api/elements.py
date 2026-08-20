from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.models import Project, TestCase, TestElement, TestModule, User
from app.schemas.element import (
    ElementCreate,
    ElementOut,
    ElementPage,
    ElementUpdate,
    ElementUsage,
    ModuleCreate,
    ModuleOut,
    ModuleUpdate,
)
from app.utils.pagination import get_pagination

router = APIRouter(tags=["模块与元素"])


async def _check_editable(project_id: int, user: User, db: AsyncSession) -> None:
    """校验当前用户对 project_id 是否有编辑权限（owner/admin/member）。"""
    _project, role = await get_project_permission(project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")


# ---------------- 模块 ----------------
@router.get("/projects/{project_id}/modules", response_model=list[ModuleOut])
async def list_modules(
    project_id: int,
    parent_id: int | None = 0,
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    query = select(TestModule).where(
        TestModule.project_id == project_id,
        TestModule.deleted_at.is_(None),
    )
    if parent_id is not None:
        query = query.where(TestModule.parent_id == (None if parent_id == 0 else parent_id))
    rows = (await db.execute(query.order_by(TestModule.sort_order, TestModule.id))).scalars().all()
    return rows


@router.post("/projects/{project_id}/modules", response_model=ModuleOut, status_code=status.HTTP_201_CREATED)
async def create_module(
    project_id: int,
    body: ModuleCreate,
    project: Project = Depends(get_editable_project),
    db: AsyncSession = Depends(get_db),
):
    if body.parent_id is not None:
        parent = await db.get(TestModule, body.parent_id)
        if parent is None or parent.deleted_at is not None or parent.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="父模块不存在")
    module = TestModule(
        project_id=project_id,
        name=body.name,
        parent_id=body.parent_id,
        sort_order=body.sort_order,
    )
    db.add(module)
    await db.commit()
    await db.refresh(module)
    return module


@router.put("/modules/{module_id}", response_model=ModuleOut)
async def update_module(
    module_id: int,
    body: ModuleUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    module = await db.get(TestModule, module_id)
    if module is None or module.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模块不存在")
    await _check_editable(module.project_id, user, db)
    if body.name is not None:
        module.name = body.name
    if body.parent_id is not None:
        module.parent_id = body.parent_id if body.parent_id != 0 else None
    if body.sort_order is not None:
        module.sort_order = body.sort_order
    await db.commit()
    await db.refresh(module)
    return module


@router.delete("/modules/{module_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_module(
    module_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    module = await db.get(TestModule, module_id)
    if module is None or module.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模块不存在")
    await _check_editable(module.project_id, user, db)
    module.deleted_at = datetime.now(UTC)
    await db.commit()


# ---------------- 元素 ----------------
@router.get("/projects/{project_id}/elements", response_model=ElementPage)
async def list_elements(
    project_id: int,
    pagination=Depends(get_pagination),
    keyword: str = "",
    platform: str = "",
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import func

    query = select(TestElement).where(
        TestElement.project_id == project_id,
        TestElement.deleted_at.is_(None),
    )
    count_query = select(func.count()).select_from(TestElement).where(
        TestElement.project_id == project_id,
        TestElement.deleted_at.is_(None),
    )
    if keyword:
        query = query.where(TestElement.name.ilike(f"%{keyword}%"))
        count_query = count_query.where(TestElement.name.ilike(f"%{keyword}%"))
    if platform:
        query = query.where((TestElement.platform == platform) | (TestElement.platform == "both"))
        count_query = count_query.where((TestElement.platform == platform) | (TestElement.platform == "both"))

    total = await db.scalar(count_query)
    rows = (
        await db.execute(
            query.order_by(TestElement.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.limit)
        )
    ).scalars().all()
    return {"total": total or 0, "page": pagination.page, "page_size": pagination.page_size, "items": rows}


@router.post("/projects/{project_id}/elements", response_model=ElementOut, status_code=status.HTTP_201_CREATED)
async def create_element(
    project_id: int,
    body: ElementCreate,
    project: Project = Depends(get_editable_project),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = TestElement(
        project_id=project_id,
        name=body.name,
        page_name=body.page_name,
        platform=body.platform,
        locator_type=body.locator_type,
        locator_value=body.locator_value,
        description=body.description,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(element)
    await db.commit()
    await db.refresh(element)
    return element


@router.get("/elements/{element_id}", response_model=ElementOut)
async def get_element(
    element_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await db.get(TestElement, element_id)
    if element is None or element.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="元素不存在")
    await get_project_permission(element.project_id, user, db)
    return element


@router.put("/elements/{element_id}", response_model=ElementOut)
async def update_element(
    element_id: int,
    body: ElementUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await db.get(TestElement, element_id)
    if element is None or element.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="元素不存在")
    await _check_editable(element.project_id, user, db)
    for field in ("name", "page_name", "platform", "locator_type", "locator_value", "description"):
        value = getattr(body, field)
        if value is not None:
            setattr(element, field, value)
    element.updated_by = user.id
    await db.commit()
    await db.refresh(element)
    return element


@router.delete("/elements/{element_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_element(
    element_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await db.get(TestElement, element_id)
    if element is None or element.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="元素不存在")
    await _check_editable(element.project_id, user, db)
    element.deleted_at = datetime.now(UTC)
    await db.commit()


@router.get("/elements/{element_id}/usage", response_model=list[ElementUsage])
async def element_usage(
    element_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await db.get(TestElement, element_id)
    if element is None or element.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="元素不存在")
    await get_project_permission(element.project_id, user, db)
    cases = (
        await db.execute(
            select(TestCase).where(
                TestCase.project_id == element.project_id,
                TestCase.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    usage: list[ElementUsage] = []
    for case in cases:
        steps = case.steps or []
        if any(
            str(step.get("element_id")) == str(element_id)
            for step in steps
            if isinstance(step, dict)
        ):
            usage.append(ElementUsage(case_id=case.id, case_name=case.name))
        for assertion in case.assertions or []:
            if isinstance(assertion, dict) and str(assertion.get("element_id")) == str(element_id):
                usage.append(ElementUsage(case_id=case.id, case_name=case.name))
                break
    return usage
