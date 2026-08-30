"""APP 档案覆盖项数据访问。"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileVariableOverride,
)


async def list_all(db: AsyncSession, profile_id: int) -> tuple[list, list, list]:
    result = []
    for model in (AppProfileElementOverride, AppProfileVariableOverride, AppProfileNodeOverride):
        rows = await db.execute(
            select(model).where(model.profile_id == profile_id, model.deleted_at.is_(None))
        )
        result.append(list(rows.scalars().all()))
    return tuple(result)  # type: ignore[return-value]


async def get_element(db: AsyncSession, profile_id: int, element_id: int):
    return (
        await db.execute(
            select(AppProfileElementOverride).where(
                AppProfileElementOverride.profile_id == profile_id,
                AppProfileElementOverride.element_id == element_id,
                AppProfileElementOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def get_variable(db: AsyncSession, profile_id: int, name: str):
    return (
        await db.execute(
            select(AppProfileVariableOverride).where(
                AppProfileVariableOverride.profile_id == profile_id,
                AppProfileVariableOverride.name == name,
                AppProfileVariableOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def get_node(
    db: AsyncSession, profile_id: int, *, suite_id: int, case_id: int | None,
    target_type: str, node_key: str
):
    conditions = [
        AppProfileNodeOverride.profile_id == profile_id,
        AppProfileNodeOverride.suite_id == suite_id,
        AppProfileNodeOverride.target_type == target_type,
        AppProfileNodeOverride.node_key == node_key,
        AppProfileNodeOverride.deleted_at.is_(None),
    ]
    conditions.append(
        AppProfileNodeOverride.case_id.is_(None)
        if case_id is None else AppProfileNodeOverride.case_id == case_id
    )
    return (await db.execute(select(AppProfileNodeOverride).where(*conditions))).scalar_one_or_none()


async def upsert_element(
    db: AsyncSession, *, profile_id: int, element_id: int, locator_type: str,
    locator_value: str | None, locator_config: dict[str, Any] | None, user_id: int
):
    row = await get_element(db, profile_id, element_id)
    if row is None:
        row = AppProfileElementOverride(
            profile_id=profile_id, element_id=element_id, locator_type=locator_type,
            locator_value=locator_value, locator_config=locator_config,
            created_by=user_id, updated_by=user_id,
        )
        db.add(row)
        await db.flush()
    else:
        row.deleted_at = None
        row.locator_type = locator_type
        row.locator_value = locator_value
        row.locator_config = locator_config
        row.updated_by = user_id
    return row


async def upsert_variable(
    db: AsyncSession, *, profile_id: int, name: str, value: str, description: str | None, user_id: int
):
    row = await get_variable(db, profile_id, name)
    if row is None:
        row = AppProfileVariableOverride(
            profile_id=profile_id, name=name, created_by=user_id, updated_by=user_id
        )
        db.add(row)
        await db.flush()
    row.deleted_at = None
    row.value = value
    row.description = description
    row.updated_by = user_id
    return row


async def upsert_node(
    db: AsyncSession, *, profile_id: int, suite_id: int, case_id: int | None,
    target_type: str, node_key: str, patch: dict[str, Any], user_id: int
):
    row = await get_node(
        db, profile_id, suite_id=suite_id, case_id=case_id,
        target_type=target_type, node_key=node_key,
    )
    if row is None:
        row = AppProfileNodeOverride(
            profile_id=profile_id, suite_id=suite_id, case_id=case_id,
            target_type=target_type, node_key=node_key, patch=patch,
            created_by=user_id, updated_by=user_id,
        )
        db.add(row)
        await db.flush()
    else:
        row.deleted_at = None
        row.patch = patch
        row.updated_by = user_id
    return row


async def soft_delete(row, deleted_at: datetime, user_id: int):
    row.deleted_at = deleted_at
    row.updated_by = user_id
    return row
