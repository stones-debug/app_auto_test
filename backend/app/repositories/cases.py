"""测试用例及其列表扩展信息的数据访问。"""

from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Execution, TestCase, TestElement, TestModule, Variable
from app.repositories import elements as elements_repo


async def get_by_id(db: AsyncSession, case_id: int) -> TestCase | None:
    return await db.get(TestCase, case_id)


async def list_all(db: AsyncSession) -> list[TestCase]:
    return list((await db.execute(select(TestCase).order_by(TestCase.id))).scalars().all())


async def module_belongs_to_project(
    db: AsyncSession, project_id: int, module_id: int | None
) -> bool:
    """用例的模块必须来自用例模块树（scope='case'）。"""
    return await elements_repo.module_belongs_to_project(
        db, project_id=project_id, module_id=module_id, scope="case"
    )


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
    module_ids: set[int] | None,
    ungrouped: bool,
    keyword: str,
    status: str,
    offset: int,
    limit: int,
) -> tuple[int, list[TestCase], dict[int, str], dict[int, tuple[str, datetime]]]:
    """列表查询。

    `ungrouped=True` → 只返回 module_id IS NULL 的用例；否则 `module_ids` 非空时
    按该集合过滤（调用方已展开子孙模块），为空表示不按模块过滤。
    """
    conditions = [TestCase.project_id == project_id, TestCase.deleted_at.is_(None)]
    if ungrouped:
        conditions.append(TestCase.module_id.is_(None))
    elif module_ids:
        conditions.append(TestCase.module_id.in_(module_ids))
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
    flow_nodes: list[dict[str, Any]],
    steps: list[dict[str, Any]] | None = None,
    user_id: int,
) -> TestCase:
    case = TestCase(
        project_id=project_id,
        module_id=module_id,
        name=name,
        description=description,
        status=status,
        flow_nodes=flow_nodes,
        steps=steps or [],
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(case)
    return case


async def update_fields(
    case: TestCase, *, fields: set[str], values: dict[str, Any], user_id: int
) -> TestCase:
    for field in ("name", "module_id", "description", "status", "flow_nodes", "steps"):
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
    conditions: list[Any] = [TestCase.id.in_(ids), TestCase.deleted_at.is_(None)]
    if project_id is not None:
        conditions.append(TestCase.project_id == project_id)
    rows = await db.execute(select(TestCase).where(*conditions))
    by_id = {case.id: case for case in rows.scalars().all()}
    return [by_id[case_id] for case_id in ids if case_id in by_id]


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
    flow_nodes: list[dict[str, Any]],
    steps: list[dict[str, Any]] | None = None,
    user_id: int,
) -> TestCase:
    case = TestCase(
        project_id=source.project_id,
        module_id=source.module_id,
        name=f"{source.name} (副本)",
        description=source.description,
        status="draft",
        flow_nodes=flow_nodes,
        steps=steps or [],
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(case)
    return case


async def copy_variables(
    db: AsyncSession,
    *,
    source_case_id: int,
    target_case: TestCase,
    project_id: int,
    user_id: int,
) -> None:
    """为克隆用例复制正式 case-scope 变量，生成独立稳定 ID。"""
    await db.flush()
    source_variables = (
        await db.execute(
            select(Variable).where(
                Variable.scope == "case", Variable.case_id == source_case_id
            )
        )
    ).scalars().all()
    db.add_all([
        Variable(
            scope="case",
            project_id=project_id,
            case_id=target_case.id,
            name=variable.name,
            value=variable.value,
            kind=variable.kind,
            spec=deepcopy(variable.spec),
            description=variable.description,
            is_sensitive=variable.is_sensitive,
            created_by=user_id,
        )
        for variable in source_variables
    ])


async def refresh(db: AsyncSession, case: TestCase) -> TestCase:
    await db.refresh(case)
    return case
