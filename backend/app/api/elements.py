from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.models import ElementGroup, Project, TestCase, TestElement, TestModule, User
from app.schemas.element import (
    ElementCreate,
    ElementGroupCreate,
    ElementGroupOut,
    ElementOut,
    ElementPage,
    ElementPageCount,
    ElementUpdate,
    ElementUsage,
    ModuleCreate,
    ModuleOut,
    ModuleUpdate,
)
from app.services.element_excel import (
    ELEMENT_CONTENT_TYPE,
    MAX_EXPORT_ROWS,
    MAX_IMPORT_BYTES,
    build_export,
    build_template,
    parse_import,
)
from app.services.profile_revision import touch_project_asset_revision
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
    await touch_project_asset_revision(db, project_id)
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
    await touch_project_asset_revision(db, module.project_id)
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
    await touch_project_asset_revision(db, module.project_id)
    await db.commit()


# ---------------- 元素（V3 全局元素库） ----------------
# V3：元素库跨项目通用，任意登录用户可浏览；仅创建者可编辑/删除；
# 元素归属创建时的项目，页面展示项目名并支持按项目筛选；页面分组可自定义。


async def _load_element_or_404(element_id: int, db: AsyncSession) -> TestElement:
    element = await db.get(TestElement, element_id)
    if element is None or element.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="元素不存在")
    return element


def _element_out(item: TestElement, project: Project | None, creator: User | None) -> ElementOut:
    """构造带项目名/创建者名的 ElementOut。"""
    out = ElementOut.model_validate(item)
    out.project_name = project.name if project else None
    out.created_by_name = creator.username if creator else None
    return out


async def _require_creator(element: TestElement, user: User) -> None:
    """V3：仅创建者可编辑/删除元素，其他成员只读。"""
    if element.created_by != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅元素创建者可修改")


def _ungrouped_element_condition():
    """兼容历史数据：NULL、空字符串和纯空格都属于“未分组”。"""
    return or_(TestElement.page_name.is_(None), func.btrim(TestElement.page_name) == "")


@router.get("/elements", response_model=ElementPage)
async def list_elements(
    pagination=Depends(get_pagination),
    keyword: str = "",
    platform: str = "",
    page_name: str = "",
    locator_type: str = "",
    project_id: int | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """V3：全库元素列表（跨项目通用），可按项目/关键字/平台/页面/定位方式筛选。"""
    conditions = [TestElement.deleted_at.is_(None)]
    if project_id is not None:
        conditions.append(TestElement.project_id == project_id)
    if keyword:
        conditions.append(TestElement.name.ilike(f"%{keyword}%"))
    if platform:
        conditions.append((TestElement.platform == platform) | (TestElement.platform == "both"))
    if page_name:
        conditions.append(
            _ungrouped_element_condition()
            if page_name == "未分组"
            else TestElement.page_name == page_name
        )
    if locator_type:
        conditions.append(TestElement.locator_type == locator_type)

    query = (
        select(TestElement, Project, User)
        .join(Project, TestElement.project_id == Project.id)
        .outerjoin(User, TestElement.created_by == User.id)
        .where(*conditions)
    )
    count_query = (
        select(func.count())
        .select_from(TestElement)
        .join(Project, TestElement.project_id == Project.id)
        .where(*conditions)
    )
    total = await db.scalar(count_query)
    rows = (
        await db.execute(
            query.order_by(TestElement.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.limit)
        )
    ).all()
    items = [_element_out(r[0], r[1], r[2]) for r in rows]
    return {"total": total or 0, "page": pagination.page, "page_size": pagination.page_size, "items": items}


def _download_response(content: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type=ELEMENT_CONTENT_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{filename}",
            "Content-Length": str(len(content)),
        },
    )


@router.get("/elements/export")
async def export_elements(
    keyword: str = "",
    platform: str = "",
    page_name: str = "",
    locator_type: str = "",
    project_id: int | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按当前筛选导出全部元素（不受列表分页限制）。"""
    conditions = [TestElement.deleted_at.is_(None)]
    if project_id is not None:
        conditions.append(TestElement.project_id == project_id)
    if keyword:
        conditions.append(TestElement.name.ilike(f"%{keyword}%"))
    if platform:
        conditions.append((TestElement.platform == platform) | (TestElement.platform == "both"))
    if page_name:
        conditions.append(
            _ungrouped_element_condition()
            if page_name == "未分组"
            else TestElement.page_name == page_name
        )
    if locator_type:
        conditions.append(TestElement.locator_type == locator_type)
    count = await db.scalar(select(func.count()).select_from(TestElement).where(*conditions)) or 0
    if count > MAX_EXPORT_ROWS:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="导出数据超过 20000 条，请缩小筛选范围")
    rows = (
        await db.execute(
            select(TestElement, Project)
            .join(Project, TestElement.project_id == Project.id)
            .where(*conditions)
            .order_by(TestElement.created_at.desc())
        )
    ).all()
    content = await run_in_threadpool(build_export, rows)
    return _download_response(content, "elements-export.xlsx")


@router.get("/projects/{project_id}/elements/import-template")
async def element_import_template(
    project_id: int,
    perm: tuple[Project, str | None] = Depends(get_project_permission),
):
    project, _role = perm
    content = await run_in_threadpool(build_template, project_id, project.name)
    return _download_response(content, "element-import-template.xlsx")


@router.post("/projects/{project_id}/elements/import")
async def import_elements(
    project_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project, role = await get_project_permission(project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="只支持 .xlsx 文件")
    content = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        rows, errors = await run_in_threadpool(parse_import, content, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "ELEMENT_IMPORT_INVALID", "message": str(exc), "error_count": 1, "errors": []}) from None
    if not rows and not errors:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "ELEMENT_IMPORT_INVALID", "message": "Excel 中没有可导入的数据", "error_count": 1, "errors": [{"row": 1, "field": "数据", "message": "请至少填写一行元素"}]})

    validated: list[tuple[Any, Any]] = []
    seen_ids: set[int] = set()
    for row in rows:
        try:
            model = ElementCreate(project_id=project_id, **row.data)
        except Exception as exc:
            message = str(exc).split("\n")[-1]
            errors.append({"row": row.row_number, "field": "数据", "message": message[:500]})
            continue
        if row.element_id is not None:
            if row.element_id in seen_ids:
                errors.append({"row": row.row_number, "field": "元素ID(element_id)", "message": "同一元素ID在文件中重复"})
            seen_ids.add(row.element_id)
        validated.append((row, model))

    existing_by_id: dict[int, TestElement] = {}
    if seen_ids:
        existing = (
            await db.execute(
                select(TestElement).where(
                    TestElement.id.in_(seen_ids),
                    TestElement.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        existing_by_id = {item.id: item for item in existing}
    for row, _model in validated:
        if row.element_id is None:
            continue
        element = existing_by_id.get(row.element_id)
        if element is None:
            errors.append({"row": row.row_number, "field": "元素ID(element_id)", "message": "元素不存在或已删除"})
        elif element.project_id != project_id:
            errors.append({"row": row.row_number, "field": "元素ID(element_id)", "message": "元素不属于当前项目"})
        elif element.created_by != user.id:
            errors.append({"row": row.row_number, "field": "元素ID(element_id)", "message": "仅元素创建者可批量更新"})
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "ELEMENT_IMPORT_INVALID", "message": "Excel 中存在校验错误，未导入任何元素", "error_count": len(errors), "errors": errors[:100]},
        )

    created_count = 0
    updated_count = 0
    for row, model in validated:
        data = model.model_dump()
        data["locator_config"] = model.locator_config.model_dump() if model.locator_config else None
        if row.element_id is None:
            db.add(TestElement(created_by=user.id, updated_by=user.id, **data))
            created_count += 1
            continue
        element = existing_by_id[row.element_id]
        for field in ("name", "page_name", "platform", "scope", "locator_type", "locator_value", "locator_config", "description"):
            setattr(element, field, data[field])
        element.updated_by = user.id
        updated_count += 1
    await touch_project_asset_revision(db, project_id)
    await db.commit()
    return {"created": created_count, "updated": updated_count, "total": created_count + updated_count}


@router.post("/elements", response_model=ElementOut, status_code=status.HTTP_201_CREATED)
async def create_element(
    body: ElementCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """V3：创建元素，需元素归属项目的编辑权限（owner/admin/member）。"""
    if body.project_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="project_id 必填")
    _project, role = await get_project_permission(body.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    element = TestElement(
        project_id=body.project_id,
        name=body.name,
        page_name=body.page_name,
        platform=body.platform,
        scope=body.scope,
        locator_type=body.locator_type,
        locator_value=body.locator_value,
        locator_config=body.locator_config.model_dump() if body.locator_config else None,
        description=body.description,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(element)
    await touch_project_asset_revision(db, body.project_id)
    await db.commit()
    await db.refresh(element)
    project = await db.get(Project, element.project_id)
    return _element_out(element, project, user)


# ---------------- 页面分组（V3：自定义分组 + 元素聚合） ----------------

# 注意：/elements/pages 与 /elements/groups 必须定义在 /elements/{element_id} 之前


@router.get("/elements/pages", response_model=list[ElementPageCount])
async def element_pages(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """V3：全库页面分组统计：自定义分组（含空分组）+ 元素 page_name 聚合，跨项目。"""
    normalized_page = func.coalesce(
        func.nullif(func.btrim(TestElement.page_name), ""),
        "未分组",
    )
    rows = (
        await db.execute(
            select(normalized_page, func.count())
            .where(TestElement.deleted_at.is_(None))
            .group_by(normalized_page)
        )
    ).all()
    counts = {page_name: count for page_name, count in rows}

    groups = (await db.execute(select(ElementGroup).order_by(ElementGroup.id))).scalars().all()
    items: list[ElementPageCount] = []
    for g in groups:
        items.append(ElementPageCount(page_name=g.name, count=counts.pop(g.name, 0), group_id=g.id))
    for name in sorted(counts):
        items.append(ElementPageCount(page_name=name, count=counts[name]))
    return items


@router.post("/elements/groups", response_model=ElementGroupOut, status_code=status.HTTP_201_CREATED)
async def create_element_group(
    body: ElementGroupCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """V3：新增自定义页面分组（重名 409）。"""
    existing = await db.execute(select(ElementGroup).where(ElementGroup.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分组已存在")
    group = ElementGroup(name=body.name, created_by=user.id)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


@router.delete("/elements/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_element_group(
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """V3：删除自定义分组（仅创建者；删除后元素不受影响）。"""
    group = await db.get(ElementGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")
    if group.created_by != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅分组创建者可删除")
    await db.delete(group)
    await db.commit()


@router.get("/elements/{element_id}", response_model=ElementOut)
async def get_element(
    element_id: int,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await _load_element_or_404(element_id, db)
    project = await db.get(Project, element.project_id)
    creator = await db.get(User, element.created_by) if element.created_by else None
    return _element_out(element, project, creator)


@router.put("/elements/{element_id}", response_model=ElementOut)
async def update_element(
    element_id: int,
    body: ElementUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await _load_element_or_404(element_id, db)
    await _require_creator(element, user)
    original_project_id = element.project_id
    # V3：编辑时可迁移元素所属项目（仅创建者；需目标项目写权限）
    if body.project_id is not None and body.project_id != element.project_id:
        _target, role = await get_project_permission(body.project_id, user, db)
        if role not in ("owner", "admin", "member"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="目标项目无编辑权限")
        element.project_id = body.project_id
    # page_name 允许显式 null/空白来清除分组；不能沿用“value is not None”判断。
    if "page_name" in body.model_fields_set:
        element.page_name = body.page_name
    for field in ("name", "platform", "scope", "locator_type", "locator_value", "locator_config", "description"):
        value = getattr(body, field)
        if value is not None:
            # locator_config 是 SmartLocatorConfig 对象，落库前转纯 dict（None 表示不修改）
            if field == "locator_config":
                value = value.model_dump()
            setattr(element, field, value)
    # 按最终 locator_type 归一化：满足 DB CHECK 约束并避免前端回显残留。
    # smart 定位 locator_value 必须为 NULL；普通定位 locator_config 必须为 NULL。
    # 这些赋值发生在 session flush 前，直接改 ORM 属性即可。
    if element.locator_type == "smart":
        element.locator_value = None
    else:
        element.locator_config = None
    element.updated_by = user.id
    await touch_project_asset_revision(db, original_project_id)
    if element.project_id != original_project_id:
        await touch_project_asset_revision(db, element.project_id)
    await db.commit()
    await db.refresh(element)
    project = await db.get(Project, element.project_id)
    creator = await db.get(User, element.created_by) if element.created_by else None
    return _element_out(element, project, creator)


@router.delete("/elements/{element_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_element(
    element_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await _load_element_or_404(element_id, db)
    await _require_creator(element, user)
    element.deleted_at = datetime.now(UTC)
    await touch_project_asset_revision(db, element.project_id)
    await db.commit()


@router.post("/elements/{element_id}/copy", response_model=ElementOut, status_code=status.HTTP_201_CREATED)
async def copy_element(
    element_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """V3：按当前登录用户复制一份相同元素（归属原项目）。"""
    source = await _load_element_or_404(element_id, db)
    element = TestElement(
        project_id=source.project_id,
        name=source.name,
        page_name=source.page_name,
        platform=source.platform,
        scope=source.scope,
        locator_type=source.locator_type,
        locator_value=source.locator_value,
        locator_config=source.locator_config,
        description=source.description,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(element)
    await touch_project_asset_revision(db, source.project_id)
    await db.commit()
    await db.refresh(element)
    project = await db.get(Project, element.project_id)
    return _element_out(element, project, user)


@router.get("/elements/{element_id}/usage", response_model=list[ElementUsage])
async def element_usage(
    element_id: int,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await _load_element_or_404(element_id, db)
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


# ---------------- 元素（旧项目作用域路由，兼容保留） ----------------


@router.get("/projects/{project_id}/elements", response_model=ElementPage)
async def list_elements_legacy(
    project_id: int,
    pagination=Depends(get_pagination),
    keyword: str = "",
    platform: str = "",
    page_name: str = "",
    locator_type: str = "",
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
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
            query = query.where(_ungrouped_element_condition())
            count_query = count_query.where(_ungrouped_element_condition())
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
async def create_element_legacy(
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
        scope=body.scope,
        locator_type=body.locator_type,
        locator_value=body.locator_value,
        locator_config=body.locator_config.model_dump() if body.locator_config else None,
        description=body.description,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(element)
    await touch_project_asset_revision(db, project_id)
    await db.commit()
    await db.refresh(element)
    return _element_out(element, project, user)


@router.get("/projects/{project_id}/element-pages", response_model=list[ElementPageCount])
async def element_pages_legacy(
    project_id: int,
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    """旧版元素库页面分组统计（兼容遗留）：按 page_name 分组，含"未分组"（NULL 归为未分组）。"""
    normalized_page = func.coalesce(
        func.nullif(func.btrim(TestElement.page_name), ""),
        "未分组",
    )
    rows = (
        await db.execute(
            select(normalized_page, func.count())
            .where(
                TestElement.project_id == project_id,
                TestElement.deleted_at.is_(None),
            )
            .group_by(normalized_page)
        )
    ).all()
    items: list[ElementPageCount] = []
    for page_name, count in rows:
        items.append(ElementPageCount(page_name=page_name, count=count))
    return items
