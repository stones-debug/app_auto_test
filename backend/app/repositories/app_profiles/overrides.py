"""APP 档案 occurrence 变量覆盖数据访问。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppProfileSuiteCaseVariableOverride


async def list_suite_case_variable_overrides(db: AsyncSession, profile_id: int, suite_case_id: int) -> list[AppProfileSuiteCaseVariableOverride]:
    rows = await db.execute(select(AppProfileSuiteCaseVariableOverride).where(
        AppProfileSuiteCaseVariableOverride.profile_id == profile_id,
        AppProfileSuiteCaseVariableOverride.suite_case_id == suite_case_id,
        AppProfileSuiteCaseVariableOverride.deleted_at.is_(None),
    ))
    return list(rows.scalars().all())


async def list_suite_case_variable_overrides_batch(db: AsyncSession, profile_id: int, suite_case_ids: set[int]) -> dict[int, list[AppProfileSuiteCaseVariableOverride]]:
    if not suite_case_ids:
        return {}
    rows = await db.execute(select(AppProfileSuiteCaseVariableOverride).where(
        AppProfileSuiteCaseVariableOverride.profile_id == profile_id,
        AppProfileSuiteCaseVariableOverride.suite_case_id.in_(suite_case_ids),
        AppProfileSuiteCaseVariableOverride.deleted_at.is_(None),
    ))
    result: dict[int, list[AppProfileSuiteCaseVariableOverride]] = {}
    for row in rows.scalars().all():
        result.setdefault(row.suite_case_id, []).append(row)
    return result


async def get_suite_case_variable(db: AsyncSession, profile_id: int, suite_case_id: int, name: str) -> AppProfileSuiteCaseVariableOverride | None:
    return (await db.execute(select(AppProfileSuiteCaseVariableOverride).where(
        AppProfileSuiteCaseVariableOverride.profile_id == profile_id,
        AppProfileSuiteCaseVariableOverride.suite_case_id == suite_case_id,
        AppProfileSuiteCaseVariableOverride.name == name,
        AppProfileSuiteCaseVariableOverride.deleted_at.is_(None),
    ))).scalar_one_or_none()


async def upsert_suite_case_variable(db: AsyncSession, *, profile_id: int, suite_case_id: int, name: str, value: str, user_id: int) -> AppProfileSuiteCaseVariableOverride:
    row = await get_suite_case_variable(db, profile_id, suite_case_id, name)
    if row is None:
        row = AppProfileSuiteCaseVariableOverride(profile_id=profile_id, suite_case_id=suite_case_id, name=name, value=value, created_by=user_id, updated_by=user_id)
        db.add(row)
        await db.flush()
    else:
        row.deleted_at = None
        row.value = value
        row.updated_by = user_id
    return row
