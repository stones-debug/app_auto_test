"""测试模块、元素和元素页面分组的数据访问。"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppProfile,
    AppProfileElementOverride,
    AppProfileNodeOverride,
    ElementGroup,
    Project,
    TestCase,
    TestElement,
    TestModule,
    TestSuite,
    User,
)
from app.utils.element_refs import collect_element_ids


async def list_modules(
    db: AsyncSession,
    *,
    project_id: int,
    scope: str,
    parent_id: int | None = None,
    roots_only: bool = False,
) -> list[TestModule]:
    """列出某项目某个 scope 的模块。

    `parent_id is None` 且 `roots_only=False` 表示不过滤层级（返回该 scope 全部模块，
    前端在内存里建树）；`roots_only=True` 表示只要根层级（parent_id IS NULL）。
    """
    conditions = [
        TestModule.project_id == project_id,
        TestModule.scope == scope,
        TestModule.deleted_at.is_(None),
    ]
    if roots_only:
        conditions.append(TestModule.parent_id.is_(None))
    elif parent_id is not None:
        conditions.append(TestModule.parent_id == parent_id)
    rows = await db.execute(
        select(TestModule).where(*conditions).order_by(TestModule.sort_order, TestModule.id)
    )
    return list(rows.scalars().all())


async def list_modules_for_update(
    db: AsyncSession, *, project_id: int, scope: str
) -> list[TestModule]:
    """锁住该 scope 的全部模块行，用于拖拽移动时串行化兄弟排序重排。"""
    rows = await db.execute(
        select(TestModule)
        .where(
            TestModule.project_id == project_id,
            TestModule.scope == scope,
            TestModule.deleted_at.is_(None),
        )
        .order_by(TestModule.sort_order, TestModule.id)
        .with_for_update()
    )
    return list(rows.scalars().all())


async def module_subtree_ids(
    db: AsyncSession, *, project_id: int, scope: str, module_id: int
) -> set[int]:
    """返回该模块及其全部子孙模块 id（含自身）。

    模块表规模天然很小，一次拉全量在内存展开比递归 CTE 更简单；
    已访问集合同时起到防环作用，即使历史数据里出现环也不会死循环。
    """
    rows = await db.execute(
        select(TestModule.id, TestModule.parent_id).where(
            TestModule.project_id == project_id,
            TestModule.scope == scope,
            TestModule.deleted_at.is_(None),
        )
    )
    children: dict[int | None, list[int]] = {}
    for current_id, parent_id in rows.tuples().all():
        children.setdefault(parent_id, []).append(current_id)
    result: set[int] = set()
    pending = [module_id]
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        pending.extend(children.get(current, []))
    return result


async def module_belongs_to_project(
    db: AsyncSession, *, project_id: int, module_id: int | None, scope: str = "case"
) -> bool:
    """校验模块存在、未删除、属于该项目且 scope 匹配。"""
    if module_id is None:
        return True
    row = await db.execute(
        select(TestModule.id).where(
            TestModule.id == module_id,
            TestModule.project_id == project_id,
            TestModule.scope == scope,
            TestModule.deleted_at.is_(None),
        )
    )
    return row.scalar_one_or_none() is not None


async def get_module(db: AsyncSession, module_id: int) -> TestModule | None:
    return await db.get(TestModule, module_id)


async def create_module(
    db: AsyncSession,
    *,
    project_id: int,
    name: str,
    parent_id: int | None,
    sort_order: int,
    scope: str = "case",
) -> TestModule:
    module = TestModule(
        project_id=project_id, name=name, parent_id=parent_id, sort_order=sort_order, scope=scope
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


async def ungroup_module_suites(db: AsyncSession, *, module_id: int) -> None:
    await db.execute(
        update(TestSuite)
        .where(TestSuite.module_id == module_id, TestSuite.deleted_at.is_(None))
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


async def find_active_references(
    db: AsyncSession, *, project_id: int, element_id: int
) -> list[dict[str, object]]:
    """Return compact references from current executable assets only.

    Execution snapshots are intentionally not queried: they own their historical
    element data and must not prevent lifecycle changes to the shared element.
    """

    references: list[dict[str, object]] = []
    reference_keys: set[tuple[object, ...]] = set()

    def add_reference(reference: dict[str, object]) -> None:
        """Keep one compact entry per asset and reference kind."""
        key = (
            reference["asset_type"],
            reference.get("case_id"),
            reference.get("suite_id"),
            reference.get("profile_id"),
            reference["reference_type"],
        )
        if key not in reference_keys:
            reference_keys.add(key)
            references.append(reference)
    cases = (
        await db.execute(
            select(TestCase).where(
                TestCase.project_id == project_id, TestCase.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    for case in cases:
        if element_id in collect_element_ids(case.flow_nodes, case.steps):
            add_reference(
                {
                    "asset_type": "case",
                    "case_id": case.id,
                    "case_name": case.name,
                    "reference_type": "case_flow",
                }
            )

    suites = (
        await db.execute(
            select(TestSuite).where(
                TestSuite.project_id == project_id, TestSuite.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    for suite in suites:
        if element_id in collect_element_ids(suite.setup_steps, suite.teardown_steps):
            add_reference(
                {
                    "asset_type": "suite",
                    "suite_id": suite.id,
                    "suite_name": suite.name,
                    "reference_type": "suite_setup_or_teardown",
                }
            )

    profiles = (
        await db.execute(
            select(AppProfile).where(
                AppProfile.project_id == project_id, AppProfile.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    profile_ids = [profile.id for profile in profiles]
    profile_names = {profile.id: profile.name for profile in profiles}
    if not profile_ids:
        return references

    element_overrides = (
        await db.execute(
            select(AppProfileElementOverride).where(
                AppProfileElementOverride.profile_id.in_(profile_ids),
                AppProfileElementOverride.element_id == element_id,
                AppProfileElementOverride.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    for override in element_overrides:
        add_reference(
            {
                "asset_type": "profile",
                "profile_id": override.profile_id,
                "profile_name": profile_names[override.profile_id],
                "reference_type": "element_override",
            }
        )

    node_overrides = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.profile_id.in_(profile_ids),
                AppProfileNodeOverride.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    for override in node_overrides:
        if element_id not in collect_element_ids(override.patch):
            continue
        add_reference(
            {
                "asset_type": "profile",
                "profile_id": override.profile_id,
                "profile_name": profile_names[override.profile_id],
                "reference_type": "node_override",
            }
        )
    return references


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
