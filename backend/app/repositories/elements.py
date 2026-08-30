"""测试模块、元素和元素页面分组的数据访问。"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
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
        module.name = name
    if "parent_id" in fields:
        module.parent_id = parent_id
    if "sort_order" in fields:
        module.sort_order = sort_order
    return module


async def soft_delete_module(module: TestModule, deleted_at: datetime) -> TestModule:
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
    conditions = [TestElement.deleted_at.is_(None)]
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
    return count or 0, list(rows.all())


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
    return count or 0, list(rows.all())


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
    return row.first()


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
    conditions = [TestElement.deleted_at.is_(None)]
    if project_id is not None:
        conditions.append(TestElement.project_id == project_id)
    rows = await db.execute(
        select(normalized_page, func.count()).where(*conditions).group_by(normalized_page)
    )
    counts = dict(rows.all())
    groups = await db.execute(select(ElementGroup).order_by(ElementGroup.id))
    return counts, list(groups.scalars().all())


async def create_group(db: AsyncSession, *, name: str, user_id: int) -> ElementGroup:
    group = ElementGroup(name=name, created_by=user_id)
    db.add(group)
    return group


async def get_group_by_name(db: AsyncSession, name: str) -> ElementGroup | None:
    rows = await db.execute(select(ElementGroup).where(ElementGroup.name == name))
    return rows.scalar_one_or_none()


async def get_group(db: AsyncSession, group_id: int) -> ElementGroup | None:
    return await db.get(ElementGroup, group_id)


async def delete_group(db: AsyncSession, group: ElementGroup) -> None:
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
    return list(rows.all())


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
