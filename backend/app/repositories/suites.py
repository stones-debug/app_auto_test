"""测试套件及套件用例关系的数据访问。"""

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestCase, TestModule, TestSuite, TestSuiteCase


async def get_by_id(db: AsyncSession, suite_id: int) -> TestSuite | None:
    return await db.get(TestSuite, suite_id)


async def list_for_project(db: AsyncSession, project_id: int) -> list[TestSuite]:
    rows = await db.execute(
        select(TestSuite)
        .where(TestSuite.project_id == project_id, TestSuite.deleted_at.is_(None))
        .order_by(TestSuite.name, TestSuite.id)
    )
    return list(rows.scalars().all())


async def list_case_relations(db: AsyncSession, suite_id: int, case_id: int) -> list[TestSuiteCase]:
    rows = await db.execute(
        select(TestSuiteCase)
        .where(TestSuiteCase.suite_id == suite_id, TestSuiteCase.case_id == case_id)
        .order_by(TestSuiteCase.sort_order, TestSuiteCase.id)
    )
    return list(rows.scalars().all())


async def list_page(
    db: AsyncSession,
    *,
    project_id: int,
    module_ids: set[int] | None,
    ungrouped: bool,
    keyword: str,
    status: str,
    offset: int,
    limit: int,
) -> tuple[int, list[TestSuite], dict[int, int], dict[int, str]]:
    """列表查询。

    `ungrouped=True` → 只返回 module_id IS NULL 的套件；否则 `module_ids` 非空时
    按该集合过滤（调用方已展开子孙模块），为空表示不按模块过滤。
    """
    conditions = [TestSuite.project_id == project_id, TestSuite.deleted_at.is_(None)]
    if ungrouped:
        conditions.append(TestSuite.module_id.is_(None))
    elif module_ids:
        conditions.append(TestSuite.module_id.in_(module_ids))
    if keyword:
        # 与前端搜索框的提示一致：名称或描述任一命中即返回。
        # 侧栏不是分页列表，关键字必须在服务端过滤才能覆盖全部套件。
        pattern = f"%{keyword}%"
        conditions.append(
            or_(TestSuite.name.ilike(pattern), TestSuite.description.ilike(pattern))
        )
    if status:
        conditions.append(TestSuite.status == status)
    base_query = select(TestSuite).where(*conditions)
    total = await db.scalar(select(func.count()).select_from(base_query.subquery()))
    rows = await db.execute(
        base_query.order_by(TestSuite.updated_at.desc()).offset(offset).limit(limit)
    )
    suites = list(rows.scalars().all())
    counts: dict[int, int] = {}
    if suites:
        count_rows = await db.execute(
            select(TestSuiteCase.suite_id, func.count(TestSuiteCase.id))
            .where(TestSuiteCase.suite_id.in_([suite.id for suite in suites]))
            .group_by(TestSuiteCase.suite_id)
        )
        counts = dict(count_rows.tuples().all())
    module_ids_in_page = {suite.module_id for suite in suites if suite.module_id is not None}
    module_names: dict[int, str] = {}
    if module_ids_in_page:
        module_rows = await db.execute(
            select(TestModule.id, TestModule.name).where(TestModule.id.in_(module_ids_in_page))
        )
        module_names = dict(module_rows.tuples().all())
    return total or 0, suites, counts, module_names


async def create(
    db: AsyncSession,
    *,
    project_id: int,
    name: str,
    description: str | None,
    setup_steps: list[dict],
    teardown_steps: list[dict],
    user_id: int,
    module_id: int | None = None,
) -> TestSuite:
    suite = TestSuite(
        project_id=project_id,
        module_id=module_id,
        name=name,
        description=description,
        created_by=user_id,
        setup_steps=setup_steps,
        teardown_steps=teardown_steps,
    )
    db.add(suite)
    return suite


async def update_fields(
    suite: TestSuite, *, fields: dict[str, object]
) -> TestSuite:
    for field, value in fields.items():
        setattr(suite, field, value)
    return suite


async def soft_delete(suite: TestSuite, deleted_at: datetime) -> TestSuite:
    suite.deleted_at = deleted_at
    return suite


async def count_cases(db: AsyncSession, suite_id: int) -> int:
    return (
        await db.scalar(
            select(func.count(TestSuiteCase.id)).where(TestSuiteCase.suite_id == suite_id)
        )
        or 0
    )


async def list_cases(
    db: AsyncSession, suite_id: int
) -> list[tuple[TestSuiteCase, str, str | None]]:
    rows = await db.execute(
        select(TestSuiteCase, TestCase.name, TestModule.name)
        .join(TestCase, TestCase.id == TestSuiteCase.case_id)
        .outerjoin(TestModule, TestModule.id == TestCase.module_id)
        .where(TestSuiteCase.suite_id == suite_id, TestCase.deleted_at.is_(None))
        .order_by(TestSuiteCase.sort_order, TestSuiteCase.id)
    )
    return list(rows.tuples().all())


async def find_cases(
    db: AsyncSession, *, case_ids: list[int], project_id: int
) -> list[TestCase]:
    rows = await db.execute(
        select(TestCase).where(
            TestCase.id.in_(case_ids),
            TestCase.project_id == project_id,
            TestCase.deleted_at.is_(None),
        )
    )
    return list(rows.scalars().all())


async def max_sort_order(db: AsyncSession, suite_id: int) -> int:
    return (
        await db.scalar(
            select(func.max(TestSuiteCase.sort_order)).where(TestSuiteCase.suite_id == suite_id)
        )
        or 0
    )


async def add_cases(
    db: AsyncSession, *, suite_id: int, case_ids: list[int], start_order: int
) -> list[TestSuiteCase]:
    created = []
    for offset, case_id in enumerate(case_ids, start=1):
        relation = TestSuiteCase(
            suite_id=suite_id, case_id=case_id, sort_order=start_order + offset
        )
        db.add(relation)
        created.append(relation)
    return created


async def refresh_cases(db: AsyncSession, relations: list[TestSuiteCase]) -> None:
    for relation in relations:
        await db.refresh(relation)


async def list_relations(db: AsyncSession, suite_id: int) -> list[TestSuiteCase]:
    rows = await db.execute(
        select(TestSuiteCase)
        .join(TestCase, TestCase.id == TestSuiteCase.case_id)
        .where(TestSuiteCase.suite_id == suite_id, TestCase.deleted_at.is_(None))
        .order_by(TestSuiteCase.sort_order, TestSuiteCase.id)
        .with_for_update()
    )
    return list(rows.scalars().all())


async def reorder_cases(
    relations: list[TestSuiteCase], ordered_membership_ids: list[int]
) -> None:
    by_id = {relation.id: relation for relation in relations}
    for position, membership_id in enumerate(ordered_membership_ids, start=1):
        by_id[membership_id].sort_order = position


async def find_relation(
    db: AsyncSession, *, suite_id: int, membership_id: int
) -> TestSuiteCase | None:
    rows = await db.execute(
        select(TestSuiteCase).where(
            TestSuiteCase.suite_id == suite_id, TestSuiteCase.id == membership_id
        )
    )
    return rows.scalar_one_or_none()


async def update_membership_overrides(
    db: AsyncSession, relation: TestSuiteCase, overrides: dict[str, str]
) -> TestSuiteCase:
    relation.variable_overrides = overrides
    await db.flush()
    return relation


async def delete_relation(db: AsyncSession, relation: TestSuiteCase) -> None:
    await db.delete(relation)


async def refresh(db: AsyncSession, suite: TestSuite) -> TestSuite:
    await db.refresh(suite)
    return suite
