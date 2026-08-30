"""变量及变量作用域父对象的数据访问。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestCase, TestSuite, Variable


def scope_filter(
    scope: str, project_id: int | None, suite_id: int | None, case_id: int | None
):
    conditions = [Variable.scope == scope]
    if scope in ("project", "suite", "case") and project_id is not None:
        conditions.append(Variable.project_id == project_id)
    if scope == "suite" and suite_id is not None:
        conditions.append(Variable.suite_id == suite_id)
    if scope == "case" and case_id is not None:
        conditions.append(Variable.case_id == case_id)
    return conditions


async def get_suite(db: AsyncSession, suite_id: int) -> TestSuite | None:
    return await db.get(TestSuite, suite_id)


async def get_case(db: AsyncSession, case_id: int) -> TestCase | None:
    return await db.get(TestCase, case_id)


async def list_scoped(
    db: AsyncSession,
    *,
    scope: str,
    project_id: int | None,
    suite_id: int | None,
    case_id: int | None,
) -> list[Variable]:
    rows = await db.execute(
        select(Variable)
        .where(*scope_filter(scope, project_id, suite_id, case_id))
        .order_by(Variable.name)
    )
    return list(rows.scalars().all())


async def get_by_id(db: AsyncSession, variable_id: int) -> Variable | None:
    return await db.get(Variable, variable_id)


async def find_same_scope(
    db: AsyncSession,
    *,
    scope: str,
    name: str,
    project_id: int | None,
    suite_id: int | None,
    case_id: int | None,
) -> Variable | None:
    conditions = [
        Variable.scope == scope,
        Variable.name == name,
        Variable.project_id == project_id
        if project_id is not None
        else Variable.project_id.is_(None),
        Variable.suite_id == suite_id
        if suite_id is not None
        else Variable.suite_id.is_(None),
        Variable.case_id == case_id if case_id is not None else Variable.case_id.is_(None),
    ]
    rows = await db.execute(select(Variable).where(*conditions))
    return rows.scalar_one_or_none()


async def create(
    db: AsyncSession,
    *,
    scope: str,
    project_id: int | None,
    suite_id: int | None,
    case_id: int | None,
    name: str,
    value: str,
    description: str | None,
    user_id: int,
) -> Variable:
    variable = Variable(
        scope=scope,
        project_id=project_id,
        suite_id=suite_id,
        case_id=case_id,
        name=name,
        value=value,
        description=description,
        created_by=user_id,
    )
    db.add(variable)
    return variable


async def update_fields(
    variable: Variable, *, fields: set[str], value: str | None, description: str | None
) -> Variable:
    if "value" in fields and value is not None:
        variable.value = value
    if "description" in fields:
        variable.description = description
    return variable


async def delete(db: AsyncSession, variable: Variable) -> None:
    await db.delete(variable)


async def refresh(db: AsyncSession, variable: Variable) -> Variable:
    await db.refresh(variable)
    return variable
