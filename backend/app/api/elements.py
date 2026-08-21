from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.models import Project, TestCase, TestElement, TestModule, User
from app.schemas.element import (
    ElementCreate,
    ElementOut,
    ElementPage,
    ElementPageCount,
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


async def _check_parent_valid(
    db: AsyncSession,
    project_id: int,
    parent_id: int | None,
    module_id: int | None = None,
) -> None:
    """CR-18：父模块必须属于同项目、未删除、非自身且不构成祖先循环。"""
    if parent_id is None or parent_id == 0:
        return
    if module_id is not None and parent_id == module_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父模块不能是自身")
    parent = await db.get(TestModule, parent_id)
    if parent is None or parent.deleted_at is not None or parent.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="父模块不存在")
    # 沿祖先链上溯，遇 module_id 即为循环
    seen: set[int] = {parent_id}
    current = parent
    while current.parent_id is not None:
        if current.parent_id in seen:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父模块形成循环")
        seen.add(current.parent_id)
        if module_id is not None and current.parent_id == module_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父模块形成循环")
        current = await db.get(TestModule, current.parent_id)
        if current is None:
            break


@router.post("/projects/{project_id}/modules", response_model=ModuleOut, status_code=status.HTTP_201_CREATED)
async def create_module(
    project_id: int,
    body: ModuleCreate,
    project: Project = Depends(get_editable_project),
    db: AsyncSession = Depends(get_db),
):
    await _check_parent_valid(db, project_id, body.parent_id)
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
    # CR-25：model_fields_set 区分“未提交”与“显式 null”
    if "name" in body.model_fields_set:
        module.name = body.name
    if "parent_id" in body.model_fields_set:
        new_parent = body.parent_id if body.parent_id != 0 else None
        # CR-18：同项目、非自身、无循环
        await _check_parent_valid(db, module.project_id, new_parent, module_id)
        module.parent_id = new_parent
    if "sort_order" in body.model_fields_set:
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
    page_name: str = "",
    locator_type: str = "",
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
    if page_name:
        if page_name == "未分组":
            query = query.where(TestElement.page_name.is_(None))
            count_query = count_query.where(TestElement.page_name.is_(None))
        else:
            query = query.where(TestElement.page_name == page_name)
            count_query = count_query.where(TestElement.page_name == page_name)
    if locator_type:
        query = query.where(TestElement.locator_type == locator_type)
        count_query = count_query.where(TestElement.locator_type == locator_type)

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


@router.get("/projects/{project_id}/element-pages", response_model=list[ElementPageCount])
async def element_pages(
    project_id: int,
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    """元素库页面分组统计（V2 §5.8）：按 page_name 分组，含"未分组"（NULL 归为未分组）。"""
    rows = (
        await db.execute(
            select(TestElement.page_name, func.count())
            .where(
                TestElement.project_id == project_id,
                TestElement.deleted_at.is_(None),
            )
            .group_by(TestElement.page_name)
        )
    ).all()
    items: list[ElementPageCount] = []
    for page_name, count in rows:
        items.append(ElementPageCount(page_name=page_name or "未分组", count=count))
    return items


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
        step_orders: list[int] = []
        for step in steps:
            if isinstance(step, dict) and str(step.get("element_id")) == str(element_id):
                order = step.get("order")
                if order is not None:
                    step_orders.append(int(order))
        if step_orders:
            usage.append(
                ElementUsage(case_id=case.id, case_name=case.name, step_orders=sorted(step_orders))
            )
    return usage
