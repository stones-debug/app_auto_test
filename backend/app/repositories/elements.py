"""测试模块、元素和元素页面分组的数据访问。"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ElementGroup, Project, TestCase, TestElement, TestModule, User


async def list_modules(
    db: AsyncSession, *, project_id: int, parent_id: int | None
) -> list[TestModule]:
    conditions = [TestModule.project_id == project_id, TestModule.deleted_at.is_(None)]
    if parent_id is not None:
        conditions.append(TestModule.parent_id == parent_id)
    rows = await db.execute(
        select(TestModule).where(*conditions).order_by(TestModule.sort_order, TestModule.id)
    )
    return list(rows.scalars().all())


async def load_modules_for_parent_validation(
    db: AsyncSession, project_id: int
) -> list[TestModule]:
    rows = await db.execute(
        select(TestModule).where(
            TestModule.project_id == project_id, TestModule.deleted_at.is_(None)
        )
    )
    return list(rows.scalars().all())


async def get_module(db: AsyncSession, module_id: int) -> TestModule | None:
    return await db.get(TestModule, module_id)


async def create_module(
    db: AsyncSession, *, project_id: int, name: str, parent_id: int | None, sort_order: int
) -> TestModule:
    module = TestModule(
        project_id=project_id, name=name, parent_id=parent_id, sort_order=sort_order
    )
    db.add(module)
    return module


async def update_module(
    module: TestModule, *, fields: set[str], name: str | None, parent_id: int | None, sort_order: int | None
) -> TestModule:
    if "name" in fields:
        if name is not None:
            module.name = name
    if "parent_id" in fields:
        module.parent_id = parent_id
    if "sort_order" in fields:
        if sort_order is not None:
            module.sort_order = sort_order
    return module


async def reparent_module_children(
    db: AsyncSession, *, module_id: int, parent_id: int | None
) -> None:
    await db.execute(
        update(TestModule)
        .where(TestModule.parent_id == module_id, TestModule.deleted_at.is_(None))
        .values(parent_id=parent_id)
    )


async def ungroup_module_cases(db: AsyncSession, *, module_id: int) -> None:
    await db.execute(
        update(TestCase)
        .where(TestCase.module_id == module_id, TestCase.deleted_at.is_(None))
        .values(module_id=None)
    )


async def soft_delete_module(module: TestModule, deleted_at: datetime) -> TestModule:
    # 删除模块后保留子模块和用例的数据可见性：子模块提升到当前模块的父级，
    # 归属于当前模块的用例转为未分组，避免留下指向已删除模块的悬挂引用。
    # 具体更新由 service 在同一事务中调用，repository 只负责持久化变更。
    module.deleted_at = deleted_at
    return module


def _ungrouped_condition():
    return or_(TestElement.page_name.is_(None), func.btrim(TestElement.page_name) == "")


def _element_conditions(
    *,
    keyword: str = "",
    platform: str = "",
    page_name: str = "",
    locator_type: str = "",
    project_id: int | None = None,
):
    conditions: list[Any] = [TestElement.deleted_at.is_(None)]
    if project_id is not None:
        conditions.append(TestElement.project_id == project_id)
    if keyword:
        conditions.append(TestElement.name.ilike(f"%{keyword}%"))
    if platform:
        conditions.append((TestElement.platform == platform) | (TestElement.platform == "both"))
    if page_name:
        conditions.append(
            _ungrouped_condition() if page_name == "未分组" else TestElement.page_name == page_name
        )
    if locator_type:
        conditions.append(TestElement.locator_type == locator_type)
    return conditions


async def list_page(
    db: AsyncSession,
    *,
    keyword: str = "",
    platform: str = "",
    page_name: str = "",
    locator_type: str = "",
    project_id: int | None = None,
    offset: int,
    limit: int,
) -> tuple[int, list[tuple[TestElement, Project, User | None]]]:
    conditions = _element_conditions(
        keyword=keyword,
        platform=platform,
        page_name=page_name,
        locator_type=locator_type,
        project_id=project_id,
    )
    count = await db.scalar(
        select(func.count())
        .select_from(TestElement)
        .join(Project, TestElement.project_id == Project.id)
        .where(*conditions)
    )
    rows = await db.execute(
        select(TestElement, Project, User)
        .join(Project, TestElement.project_id == Project.id)
        .outerjoin(User, TestElement.created_by == User.id)
        .where(*conditions)
        .order_by(TestElement.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return count or 0, list(rows.tuples().all())


async def export_rows(
    db: AsyncSession,
    *,
    keyword: str = "",
    platform: str = "",
    page_name: str = "",
    locator_type: str = "",
    project_id: int | None = None,
) -> tuple[int, list[tuple[TestElement, Project]]]:
    conditions = _element_conditions(
        keyword=keyword,
        platform=platform,
        page_name=page_name,
        locator_type=locator_type,
        project_id=project_id,
    )
    count = await db.scalar(select(func.count()).select_from(TestElement).where(*conditions))
    rows = await db.execute(
        select(TestElement, Project)
        .join(Project, TestElement.project_id == Project.id)
        .where(*conditions)
        .order_by(TestElement.created_at.desc())
    )
    return count or 0, list(rows.tuples().all())


async def get_by_id(db: AsyncSession, element_id: int) -> TestElement | None:
    return await db.get(TestElement, element_id)


async def get_enriched(
    db: AsyncSession, element_id: int
) -> tuple[TestElement, Project | None, User | None] | None:
    row = await db.execute(
        select(TestElement, Project, User)
        .outerjoin(Project, TestElement.project_id == Project.id)
        .outerjoin(User, TestElement.created_by == User.id)
        .where(TestElement.id == element_id)
    )
    return row.tuples().first()


async def get_project(db: AsyncSession, project_id: int) -> Project | None:
    return await db.get(Project, project_id)


async def create(
    db: AsyncSession,
    *,
    project_id: int,
    name: str,
    page_name: str | None,
    platform: str,
    scope: str,
    locator_type: str,
    locator_value: str | None,
    locator_config: dict[str, Any] | None,
    description: str | None,
    user_id: int,
) -> TestElement:
    element = TestElement(
        project_id=project_id,
        name=name,
        page_name=page_name,
        platform=platform,
        scope=scope,
        locator_type=locator_type,
        locator_value=locator_value,
        locator_config=locator_config,
        description=description,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(element)
    return element


async def update_fields(
    element: TestElement, *, fields: dict[str, Any], user_id: int
) -> TestElement:
    for field, value in fields.items():
        setattr(element, field, value)
    element.updated_by = user_id
    return element


async def soft_delete(element: TestElement, deleted_at: datetime) -> TestElement:
    element.deleted_at = deleted_at
    return element


async def copy(
    db: AsyncSession, source: TestElement, *, user_id: int
) -> TestElement:
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
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(element)
    return element


async def find_by_ids(db: AsyncSession, ids: set[int]) -> list[TestElement]:
    if not ids:
        return []
    rows = await db.execute(
        select(TestElement).where(TestElement.id.in_(ids), TestElement.deleted_at.is_(None))
    )
    return list(rows.scalars().all())


async def list_pages(db: AsyncSession, project_id: int | None = None):
    normalized_page = func.coalesce(func.nullif(func.btrim(TestElement.page_name), ""), "未分组")
    conditions: list[Any] = [TestElement.deleted_at.is_(None)]
    if project_id is not None:
        conditions.append(TestElement.project_id == project_id)
    rows = await db.execute(
        select(normalized_page, func.count()).where(*conditions).group_by(normalized_page)
    )
    counts = dict(rows.tuples().all())
    groups = await db.execute(select(ElementGroup).order_by(ElementGroup.id))
    group_items = list(groups.scalars().all())
    if project_id is not None:
        # 页面分组表是全局的，项目归属由当前项目实际使用的 page_name 确定。
        # 同时保留必要的父级节点，避免子页面因父级被过滤而无法正确显示层级。
        groups_by_id = {group.id: group for group in group_items}
        visible_ids = {
            group.id
            for group in group_items
            if group.project_id == project_id or (group.project_id is None and group.name in counts)
        }
        pending = list(visible_ids)
        while pending:
            parent_id = groups_by_id[pending.pop()].parent_id
            if parent_id is not None and parent_id in groups_by_id and parent_id not in visible_ids:
                visible_ids.add(parent_id)
                pending.append(parent_id)
        group_items = [group for group in group_items if group.id in visible_ids]
    return counts, group_items


async def create_group(
    db: AsyncSession, *, name: str, project_id: int | None, parent_id: int | None, user_id: int | None
) -> ElementGroup:
    group = ElementGroup(
        name=name, project_id=project_id, parent_id=parent_id, created_by=user_id
    )
    db.add(group)
    return group


async def rename_group(db: AsyncSession, *, group: ElementGroup, name: str) -> ElementGroup:
    element_conditions = [
        func.btrim(TestElement.page_name) == group.name,
        TestElement.deleted_at.is_(None),
    ]
    if group.project_id is not None:
        element_conditions.append(TestElement.project_id == group.project_id)
    await db.execute(
        update(TestElement)
        .where(*element_conditions)
        .values(page_name=name)
    )
    group.name = name
    return group


async def get_group_by_name(db: AsyncSession, name: str) -> ElementGroup | None:
    rows = await db.execute(select(ElementGroup).where(ElementGroup.name == name))
    return rows.scalar_one_or_none()


async def get_group(db: AsyncSession, group_id: int) -> ElementGroup | None:
    return await db.get(ElementGroup, group_id)


async def delete_group(db: AsyncSession, group: ElementGroup) -> None:
    element_conditions = [
        func.btrim(TestElement.page_name) == group.name,
        TestElement.deleted_at.is_(None),
    ]
    if group.project_id is not None:
        element_conditions.append(TestElement.project_id == group.project_id)
    await db.execute(
        update(TestElement)
        .where(*element_conditions)
        .values(page_name=None)
    )
    await db.execute(
        update(ElementGroup)
        .where(ElementGroup.parent_id == group.id)
        .values(parent_id=group.parent_id)
    )
    await db.delete(group)


async def list_usage_cases(db: AsyncSession, project_id: int) -> list[TestCase]:
    rows = await db.execute(
        select(TestCase).where(
            TestCase.project_id == project_id, TestCase.deleted_at.is_(None)
        )
    )
    return list(rows.scalars().all())


async def list_page_counts(db: AsyncSession, project_id: int) -> list[tuple[str, int]]:
    normalized_page = func.coalesce(func.nullif(func.btrim(TestElement.page_name), ""), "未分组")
    rows = await db.execute(
        select(normalized_page, func.count())
        .where(TestElement.project_id == project_id, TestElement.deleted_at.is_(None))
        .group_by(normalized_page)
    )
    return list(rows.tuples().all())


async def add_imported(
    db: AsyncSession, *, data: dict[str, Any], user_id: int
) -> TestElement:
    element = TestElement(created_by=user_id, updated_by=user_id, **data)
    db.add(element)
    return element


async def refresh_module(db: AsyncSession, module: TestModule) -> TestModule:
    await db.refresh(module)
    return module


async def refresh_element(db: AsyncSession, element: TestElement) -> TestElement:
    await db.refresh(element)
    return element


async def refresh_group(db: AsyncSession, group: ElementGroup) -> ElementGroup:
    await db.refresh(group)
    return group
