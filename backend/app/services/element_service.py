"""测试模块、元素和页面分组的业务规则与事务编排。"""

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, api_error
from app.models import ElementGroup, Project, TestElement, TestModule, User
from app.repositories import elements as elements_repo
from app.schemas.element import (
    ElementCreate,
    ElementGroupUpdate,
    ElementUpdate,
    ModuleCreate,
    ModuleUpdate,
)
from app.services import asset_service


async def _rollback_on_error(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        pass


MODULE_SCOPE_CASE = "case"
MODULE_SCOPE_SUITE = "suite"


async def list_modules(
    db: AsyncSession,
    *,
    project_id: int,
    scope: str = MODULE_SCOPE_CASE,
    parent_id: int | None = None,
    roots_only: bool = False,
):
    return await elements_repo.list_modules(
        db, project_id=project_id, scope=scope, parent_id=parent_id, roots_only=roots_only
    )


def _parent_reject_reason(
    modules: list[TestModule], *, parent_id: int | None, module_id: int | None
) -> str | None:
    """在已加载的模块集合上判断父级是否合法。

    返回 `'self'` / `'not_found'` / `'cycle'`，合法则返回 None。
    父级必须落在同一 scope 的集合里，因此跨 scope 挂载会被判为 not_found。
    """
    if parent_id is None:
        return None
    if module_id is not None and parent_id == module_id:
        return "self"
    by_id = {module.id: module for module in modules}
    if parent_id not in by_id:
        return "not_found"
    seen: set[int] = set()
    current_id: int | None = parent_id
    while current_id is not None:
        if current_id in seen:
            return "cycle"
        seen.add(current_id)
        if module_id is not None and current_id == module_id:
            return "cycle"
        current = by_id.get(current_id)
        current_id = current.parent_id if current else None
    return None


_POSITION_ERROR_MESSAGES = {
    "self": "父模块不能是自身",
    "not_found": "父模块不存在",
    "cycle": "父模块形成循环",
}


async def validate_parent(
    db: AsyncSession,
    *,
    project_id: int,
    scope: str = MODULE_SCOPE_CASE,
    parent_id: int | None,
    module_id: int | None = None,
) -> None:
    """存量接口的校验入口：沿用普通 HTTPException + 中文 detail。"""
    if parent_id in (None, 0):
        return
    modules = await elements_repo.list_modules(db, project_id=project_id, scope=scope)
    reason = _parent_reject_reason(modules, parent_id=parent_id, module_id=module_id)
    if reason == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="父模块不存在")
    if reason is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_POSITION_ERROR_MESSAGES[reason])


async def create_module(
    db: AsyncSession, *, project_id: int, body: ModuleCreate
) -> TestModule:
    scope = body.scope or MODULE_SCOPE_CASE
    await validate_parent(
        db, project_id=project_id, scope=scope, parent_id=body.parent_id
    )
    try:
        module = await elements_repo.create_module(
            db,
            project_id=project_id,
            name=body.name,
            parent_id=body.parent_id,
            sort_order=body.sort_order,
            scope=scope,
        )
        await asset_service.commit_asset_change(db, [project_id])
        await elements_repo.refresh_module(db, module)
        return module
    except Exception:
        await _rollback_on_error(db)
        raise


async def get_module_or_404(db: AsyncSession, module_id: int) -> TestModule:
    module = await elements_repo.get_module(db, module_id)
    if module is None or module.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模块不存在")
    return module


async def update_module(
    db: AsyncSession, *, module: TestModule, body: ModuleUpdate
) -> TestModule:
    new_parent = body.parent_id if body.parent_id not in (None, 0) else None
    if "parent_id" in body.model_fields_set:
        await validate_parent(
            db,
            project_id=module.project_id,
            scope=module.scope,
            parent_id=new_parent,
            module_id=module.id,
        )
    try:
        await elements_repo.update_module(
            module,
            fields=body.model_fields_set,
            name=body.name,
            parent_id=new_parent,
            sort_order=body.sort_order,
        )
        await asset_service.commit_asset_change(db, [module.project_id])
        await elements_repo.refresh_module(db, module)
        return module
    except Exception:
        await _rollback_on_error(db)
        raise


async def delete_module(db: AsyncSession, *, module: TestModule) -> None:
    try:
        # 删除模块后保留子模块与资产的数据可见性：子模块提升到当前模块的父级，
        # 归属当前模块的用例/套件转为未分组，避免留下指向已删除模块的悬挂引用。
        await elements_repo.reparent_module_children(
            db, module_id=module.id, parent_id=module.parent_id
        )
        # 两侧都解绑：正常数据里只有同 scope 的资产会挂在该模块下，
        # 多清一次是为了兜住历史上可能存在的脏数据，代价只有一条 UPDATE。
        await elements_repo.ungroup_module_cases(db, module_id=module.id)
        await elements_repo.ungroup_module_suites(db, module_id=module.id)
        await elements_repo.soft_delete_module(module, datetime.now(UTC))
        await asset_service.commit_asset_change(db, [module.project_id])
    except Exception:
        await _rollback_on_error(db)
        raise


async def move_module(
    db: AsyncSession, *, module: TestModule, parent_id: int | None, before_id: int | None
) -> list[TestModule]:
    """拖拽移动：把模块挂到 parent_id 下并排在 before_id 之前（None 表示追加末尾）。

    在同一个事务里锁住该 scope 的全部模块、校验、重排兄弟 order 后提交，
    避免前端自行计算 sort_order 造成的浮点漂移与并发错位。
    """
    target_parent = parent_id if parent_id not in (None, 0) else None
    project_id = module.project_id
    scope = module.scope
    try:
        modules = await elements_repo.list_modules_for_update(
            db, project_id=project_id, scope=scope
        )
        by_id = {item.id: item for item in modules}
        if module.id not in by_id:
            raise api_error(
                status.HTTP_404_NOT_FOUND, ErrorCode.MODULE_NOT_FOUND, "模块不存在"
            )
        reason = _parent_reject_reason(modules, parent_id=target_parent, module_id=module.id)
        if reason == "not_found":
            raise api_error(
                status.HTTP_404_NOT_FOUND,
                ErrorCode.MODULE_NOT_FOUND,
                _POSITION_ERROR_MESSAGES[reason],
            )
        if reason is not None:
            raise api_error(
                status.HTTP_400_BAD_REQUEST,
                ErrorCode.MODULE_PARENT_INVALID,
                _POSITION_ERROR_MESSAGES[reason],
            )

        old_parent = module.parent_id
        siblings = sorted(
            (
                item
                for item in modules
                if item.parent_id == target_parent and item.id != module.id
            ),
            key=lambda item: (item.sort_order, item.id),
        )
        if before_id is None:
            insert_at = len(siblings)
        else:
            insert_at = next(
                (index for index, item in enumerate(siblings) if item.id == before_id), None
            )
            if insert_at is None:
                raise api_error(
                    status.HTTP_400_BAD_REQUEST,
                    ErrorCode.MODULE_POSITION_INVALID,
                    "目标位置不在该父模块下",
                )
        siblings.insert(insert_at, module)
        module.parent_id = target_parent
        for order, item in enumerate(siblings):
            item.sort_order = order
        if old_parent != target_parent:
            # 源父级剩余兄弟重排，避免留下空洞
            remaining = sorted(
                (
                    item
                    for item in modules
                    if item.parent_id == old_parent and item.id != module.id
                ),
                key=lambda item: (item.sort_order, item.id),
            )
            for order, item in enumerate(remaining):
                item.sort_order = order

        await asset_service.commit_asset_change(db, [module.project_id])
    except Exception:
        await _rollback_on_error(db)
        raise
    # 提交后再查一次：直接返回服务端权威顺序，前端无需本地重排。
    # project_id/scope 提前取到局部变量，不依赖 commit 后仍可读 ORM 属性。
    return await elements_repo.list_modules(db, project_id=project_id, scope=scope)


async def module_ids_with_descendants(
    db: AsyncSession, *, project_id: int, scope: str, module_id: int
) -> set[int]:
    """模块及其全部子孙 id；用于“选中父模块时包含子模块内容”。"""
    return await elements_repo.module_subtree_ids(
        db, project_id=project_id, scope=scope, module_id=module_id
    )


def _element_out(
    item: TestElement, project: Project | None, creator: User | None
):
    from app.schemas.element import ElementOut

    result = ElementOut.model_validate(item)
    result.project_name = project.name if project else None
    result.created_by_name = creator.username if creator else None
    return result


async def get_or_404(db: AsyncSession, element_id: int) -> TestElement:
    element = await elements_repo.get_by_id(db, element_id)
    if element is None or element.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="元素不存在")
    return element


async def get_enriched_or_404(
    db: AsyncSession, element_id: int
) -> tuple[TestElement, Project | None, User | None]:
    row = await elements_repo.get_enriched(db, element_id)
    if row is None or row[0].deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="元素不存在")
    return row


async def _ensure_page_group(
    db: AsyncSession, *, page_name: str | None, project_id: int | None = None
) -> None:
    """Ensure every named page shown in the tree has a manageable group node."""
    if page_name is None:
        return
    if await elements_repo.get_group_by_name(db, page_name) is None:
        await elements_repo.create_group(
            db, name=page_name, project_id=project_id, parent_id=None, user_id=None
        )


async def create(
    db: AsyncSession, *, body: ElementCreate, user_id: int, project_id: int | None = None
) -> tuple[TestElement, Project | None, User | None]:
    target_project_id = project_id if project_id is not None else body.project_id
    if target_project_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="project_id 必填")
    await _ensure_page_group(db, page_name=body.page_name, project_id=target_project_id)
    element = await elements_repo.create(
        db,
        project_id=target_project_id,
        name=body.name,
        page_name=body.page_name,
        platform=body.platform,
        scope=body.scope,
        locator_type=body.locator_type,
        locator_value=body.locator_value,
        locator_config=body.locator_config.model_dump() if body.locator_config else None,
        description=body.description,
        user_id=user_id,
    )
    try:
        await asset_service.commit_asset_change(db, [target_project_id])
        await elements_repo.refresh_element(db, element)
        project = await elements_repo.get_project(db, target_project_id)
        return element, project, None
    except Exception:
        await _rollback_on_error(db)
        raise


async def update(
    db: AsyncSession,
    *,
    element: TestElement,
    body: ElementUpdate,
    user_id: int,
) -> tuple[TestElement, Project | None, User | None]:
    original_project_id = element.project_id
    fields: dict[str, Any] = {}
    if body.project_id is not None and body.project_id != element.project_id:
        fields["project_id"] = body.project_id
    if "page_name" in body.model_fields_set:
        fields["page_name"] = body.page_name
        target_project_id = body.project_id if body.project_id is not None else element.project_id
        await _ensure_page_group(db, page_name=body.page_name, project_id=target_project_id)
    for field in ("name", "platform", "scope", "locator_type", "locator_value", "locator_config", "description"):
        if field in body.model_fields_set and getattr(body, field) is not None:
            value = getattr(body, field)
            fields[field] = value.model_dump() if field == "locator_config" else value
    final_locator_type = fields.get("locator_type", element.locator_type)
    if final_locator_type == "smart":
        fields["locator_value"] = None
    else:
        fields["locator_config"] = None
    try:
        await elements_repo.update_fields(element, fields=fields, user_id=user_id)
        await asset_service.commit_asset_change(db, [original_project_id, element.project_id])
        await elements_repo.refresh_element(db, element)
        project = await elements_repo.get_project(db, element.project_id)
        row = await elements_repo.get_enriched(db, element.id)
        creator = row[2] if row else None
        return element, project, creator
    except Exception:
        await _rollback_on_error(db)
        raise


async def delete(db: AsyncSession, *, element: TestElement) -> None:
    references = await elements_repo.find_active_references(
        db, project_id=element.project_id, element_id=element.id
    )
    if references:
        raise api_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.ELEMENT_IN_USE,
            "元素仍被可执行资产引用，无法删除",
            {"references": references},
        )
    try:
        await elements_repo.soft_delete(element, datetime.now(UTC))
        await asset_service.commit_asset_change(db, [element.project_id])
    except Exception:
        await _rollback_on_error(db)
        raise


async def copy(
    db: AsyncSession, *, source: TestElement, user_id: int
) -> tuple[TestElement, Project | None, User | None]:
    try:
        await _ensure_page_group(db, page_name=source.page_name, project_id=source.project_id)
        element = await elements_repo.copy(db, source, user_id=user_id)
        await asset_service.commit_asset_change(db, [source.project_id])
        await elements_repo.refresh_element(db, element)
        project = await elements_repo.get_project(db, element.project_id)
        return element, project, None
    except Exception:
        await _rollback_on_error(db)
        raise


async def import_elements(
    db: AsyncSession,
    *,
    project_id: int,
    rows: list[tuple[Any, ElementCreate]],
    existing_by_id: dict[int, TestElement],
    user_id: int,
) -> tuple[int, int]:
    created_count = 0
    updated_count = 0
    try:
        for row, model in rows:
            data = model.model_dump()
            data["locator_config"] = model.locator_config.model_dump() if model.locator_config else None
            await _ensure_page_group(db, page_name=model.page_name, project_id=project_id)
            if row.element_id is None:
                await elements_repo.add_imported(db, data=data, user_id=user_id)
                created_count += 1
                continue
            element = existing_by_id[row.element_id]
            await elements_repo.update_fields(
                element,
                fields={
                    field: data[field]
                    for field in (
                        "name", "page_name", "platform", "scope", "locator_type",
                        "locator_value", "locator_config", "description",
                    )
                },
                user_id=user_id,
            )
            updated_count += 1
        await asset_service.commit_asset_change(db, [project_id])
        return created_count, updated_count
    except Exception:
        await _rollback_on_error(db)
        raise


async def validate_import_existing(
    db: AsyncSession,
    *,
    project_id: int,
    rows: list[tuple[Any, ElementCreate]],
    user_id: int,
) -> tuple[dict[int, TestElement], list[dict[str, Any]]]:
    ids = {row.element_id for row, _model in rows if row.element_id is not None}
    existing = await elements_repo.find_by_ids(db, ids)
    existing_by_id = {item.id: item for item in existing}
    errors: list[dict[str, Any]] = []
    for row, _model in rows:
        if row.element_id is None:
            continue
        element = existing_by_id.get(row.element_id)
        if element is None:
            errors.append({"row": row.row_number, "field": "元素ID(element_id)", "message": "元素不存在或已删除"})
        elif element.project_id != project_id:
            errors.append({"row": row.row_number, "field": "元素ID(element_id)", "message": "元素不属于当前项目"})
        elif element.created_by != user_id:
            errors.append({"row": row.row_number, "field": "元素ID(element_id)", "message": "仅元素创建者可批量更新"})
    return existing_by_id, errors


async def list_page(db: AsyncSession, **filters):
    return await elements_repo.list_page(db, **filters)


async def export_rows(db: AsyncSession, **filters):
    return await elements_repo.export_rows(db, **filters)


async def pages(db: AsyncSession, *, project_id: int | None = None):
    return await elements_repo.list_pages(db, project_id)


async def create_group(
    db: AsyncSession, *, name: str, project_id: int | None, parent_id: int | None, user_id: int
) -> ElementGroup:
    if await elements_repo.get_group_by_name(db, name) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分组已存在")
    if parent_id is not None:
        parent = await elements_repo.get_group(db, parent_id)
        if parent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="父级页面不存在")
        if project_id is not None and parent.project_id not in (None, project_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="父级页面不属于当前项目")
    group = await elements_repo.create_group(
        db, name=name, project_id=project_id, parent_id=parent_id, user_id=user_id
    )
    try:
        await db.commit()
        await elements_repo.refresh_group(db, group)
        return group
    except IntegrityError:
        await _rollback_on_error(db)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分组已存在") from None
    except Exception:
        await _rollback_on_error(db)
        raise


async def update_group(
    db: AsyncSession, *, group: ElementGroup, body: ElementGroupUpdate, user_id: int
) -> ElementGroup:
    if body.name != group.name and await elements_repo.get_group_by_name(db, body.name) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分组已存在")
    parent_id = group.parent_id
    if "parent_id" in body.model_fields_set:
        parent_id = body.parent_id
        if parent_id == group.id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="父级页面不能是自身")
        if parent_id is not None:
            parent = await elements_repo.get_group(db, parent_id)
            if parent is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="父级页面不存在")
            ancestor_id = parent.id
            while ancestor_id is not None:
                if ancestor_id == group.id:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不能移动到自身的子页面下")
                ancestor = await elements_repo.get_group(db, ancestor_id)
                ancestor_id = ancestor.parent_id if ancestor is not None else None
    try:
        if parent_id != group.parent_id:
            group.parent_id = parent_id
        await elements_repo.rename_group(db, group=group, name=body.name)
        await db.commit()
        await elements_repo.refresh_group(db, group)
        return group
    except IntegrityError:
        await _rollback_on_error(db)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分组已存在") from None
    except Exception:
        await _rollback_on_error(db)
        raise


async def get_group_or_404(db: AsyncSession, group_id: int) -> ElementGroup:
    group = await elements_repo.get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")
    return group


async def delete_group(db: AsyncSession, *, group: ElementGroup, user_id: int) -> None:
    try:
        await elements_repo.delete_group(db, group)
        await db.commit()
    except Exception:
        await _rollback_on_error(db)
        raise


async def usage(db: AsyncSession, *, element: TestElement, element_id: int):
    from app.schemas.element import ElementUsage

    result = []
    for case in await elements_repo.list_usage_cases(db, element.project_id):
        orders = [
            int(step["order"])
            for step in (case.flow_nodes or case.steps or [])
            if isinstance(step, dict)
            and str(step.get("element_id")) == str(element_id)
            and step.get("order") is not None
        ]
        if orders:
            result.append(ElementUsage(case_id=case.id, case_name=case.name, step_orders=sorted(orders)))
    return result


async def legacy_page_counts(db: AsyncSession, project_id: int):
    return await elements_repo.list_page_counts(db, project_id)
