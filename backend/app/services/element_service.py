"""测试模块、元素和页面分组的业务规则与事务编排。"""

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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


async def list_modules(db: AsyncSession, *, project_id: int, parent_id: int | None):
    return await elements_repo.list_modules(db, project_id=project_id, parent_id=parent_id)


async def validate_parent(
    db: AsyncSession, *, project_id: int, parent_id: int | None, module_id: int | None = None
) -> None:
    if parent_id in (None, 0):
        return
    if module_id is not None and parent_id == module_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父模块不能是自身")
    modules = await elements_repo.load_modules_for_parent_validation(db, project_id)
    by_id = {module.id: module for module in modules}
    if parent_id not in by_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="父模块不存在")
    seen: set[int] = set()
    current_id: int | None = parent_id
    while current_id is not None:
        if current_id in seen:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父模块形成循环")
        seen.add(current_id)
        if module_id is not None and current_id == module_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父模块形成循环")
        current = by_id.get(current_id)
        current_id = current.parent_id if current else None


async def create_module(
    db: AsyncSession, *, project_id: int, body: ModuleCreate
) -> TestModule:
    await validate_parent(db, project_id=project_id, parent_id=body.parent_id)
    try:
        module = await elements_repo.create_module(
            db,
            project_id=project_id,
            name=body.name,
            parent_id=body.parent_id,
            sort_order=body.sort_order,
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
            db, project_id=module.project_id, parent_id=new_parent, module_id=module.id
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
        await elements_repo.reparent_module_children(
            db, module_id=module.id, parent_id=module.parent_id
        )
        await elements_repo.ungroup_module_cases(db, module_id=module.id)
        await elements_repo.soft_delete_module(module, datetime.now(UTC))
        await asset_service.commit_asset_change(db, [module.project_id])
    except Exception:
        await _rollback_on_error(db)
        raise


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
