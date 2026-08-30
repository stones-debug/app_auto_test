"""测试用例及其列表扩展信息的数据访问。"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Execution, TestCase, TestElement, TestModule


async def get_by_id(db: AsyncSession, case_id: int) -> TestCase | None:
    return await db.get(TestCase, case_id)


async def module_belongs_to_project(
    db: AsyncSession, project_id: int, module_id: int | None
) -> bool:
    if module_id is None:
        return True
    row = await db.execute(
        select(TestModule.id).where(
            TestModule.id == module_id,
            TestModule.project_id == project_id,
            TestModule.deleted_at.is_(None),
        )
    )
    return row.scalar_one_or_none() is not None


async def find_elements_by_ids(
    db: AsyncSession, element_ids: set[int]
) -> list[TestElement]:
    if not element_ids:
        return []
    rows = await db.execute(
        select(TestElement).where(
            TestElement.id.in_(element_ids), TestElement.deleted_at.is_(None)
        )
    )
    return list(rows.scalars().all())


async def list_page(
    db: AsyncSession,
    *,
    project_id: int,
    module_id: int | None,
    keyword: str,
    status: str,
    offset: int,
    limit: int,
) -> tuple[int, list[TestCase], dict[int, str], dict[int, tuple[str, datetime]]]:
    conditions = [TestCase.project_id == project_id, TestCase.deleted_at.is_(None)]
    if module_id is not None:
        conditions.append(TestCase.module_id == module_id)
    if keyword:
        conditions.append(TestCase.name.ilike(f"%{keyword}%"))
    if status:
        conditions.append(TestCase.status == status)
    total = await db.scalar(select(func.count()).select_from(TestCase).where(*conditions))
    result = await db.execute(
        select(TestCase)
        .where(*conditions)
        .order_by(TestCase.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    cases = list(result.scalars().all())

    module_ids = {case.module_id for case in cases if case.module_id is not None}
    modules: dict[int, str] = {}
    if module_ids:
        module_rows = await db.execute(
            select(TestModule).where(TestModule.id.in_(module_ids))
        )
        modules = {module.id: module.name for module in module_rows.scalars().all()}

    last_execution: dict[int, tuple[str, datetime]] = {}
    case_ids = [case.id for case in cases]
    if case_ids:
        execution_rows = await db.execute(
            select(Execution.case_id, Execution.status, Execution.created_at)
            .where(Execution.type == "case", Execution.case_id.in_(case_ids))
            .order_by(Execution.created_at.desc())
        )
        seen: set[int] = set()
        for case_id, execution_status, created_at in execution_rows.all():
            if case_id not in seen:
                last_execution[case_id] = (execution_status, created_at)
                seen.add(case_id)
    return total or 0, cases, modules, last_execution


async def create(
    db: AsyncSession,
    *,
    project_id: int,
    module_id: int | None,
    name: str,
    description: str | None,
    status: str,
    steps: list[dict[str, Any]],
    variables: dict[str, Any],
    user_id: int,
) -> TestCase:
    case = TestCase(
        project_id=project_id,
        module_id=module_id,
        name=name,
        description=description,
        status=status,
        steps=steps,
        variables=variables,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(case)
    return case


async def update_fields(
    case: TestCase, *, fields: set[str], values: dict[str, Any], user_id: int
) -> TestCase:
    for field in ("name", "module_id", "description", "status", "steps", "variables"):
        if field in fields:
            setattr(case, field, values[field])
    case.updated_by = user_id
    return case


async def load_deletable(
    db: AsyncSession,
    *,
    ids: list[int],
    project_id: int | None = None,
) -> list[TestCase]:
    conditions = [TestCase.id.in_(ids), TestCase.deleted_at.is_(None)]
    if project_id is not None:
        conditions.append(TestCase.project_id == project_id)
    rows = await db.execute(select(TestCase).where(*conditions))
    by_id = {case.id: case for case in rows.scalars().all()}
    return [by_id[case_id] for case_id in ids if case_id in by_id]


async def find_execution_references(
    db: AsyncSession, case_ids: list[int]
) -> set[int]:
    if not case_ids:
        return set()
    rows = await db.execute(
        select(Execution.case_id).where(Execution.case_id.in_(case_ids))
    )
    return {case_id for case_id in rows.scalars().all() if case_id is not None}


async def soft_delete_many(cases: list[TestCase], deleted_at: datetime) -> list[TestCase]:
    for case in cases:
        case.deleted_at = deleted_at
    return cases


async def soft_delete(case: TestCase, deleted_at: datetime) -> TestCase:
    case.deleted_at = deleted_at
    return case


async def clone(
    db: AsyncSession,
    source: TestCase,
    *,
    steps: list[dict[str, Any]],
    user_id: int,
) -> TestCase:
    case = TestCase(
        project_id=source.project_id,
        module_id=source.module_id,
        name=f"{source.name} (副本)",
        description=source.description,
        status="draft",
        steps=steps,
        variables=dict(source.variables or {}),
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(case)
    return case


async def refresh(db: AsyncSession, case: TestCase) -> TestCase:
    await db.refresh(case)
    return case
