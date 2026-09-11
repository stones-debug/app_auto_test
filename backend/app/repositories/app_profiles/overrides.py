"""APP 档案覆盖项数据访问。"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.mutable import MutableDict

from app.models import (
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileVariableOverride,
)


async def list_all(
    db: AsyncSession, profile_id: int, *, include_nodes: bool = True
) -> tuple[list, list, list]:
    result = []
    models = [AppProfileElementOverride, AppProfileVariableOverride]
    if include_nodes:
        models.append(AppProfileNodeOverride)
    for model in models:
        rows = await db.execute(
            select(model).where(model.profile_id == profile_id, model.deleted_at.is_(None))
        )
        result.append(list(rows.scalars().all()))
    if not include_nodes:
        result.append([])
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
    target_type: str, node_key: str, suite_case_id: int | None = None,
):
    conditions = [
        AppProfileNodeOverride.profile_id == profile_id,
        AppProfileNodeOverride.target_type == target_type,
        AppProfileNodeOverride.node_key == node_key,
        AppProfileNodeOverride.deleted_at.is_(None),
    ]
    if suite_case_id is not None:
        # 编排项身份优先：同一用例重复编排时各自独立
        conditions.append(AppProfileNodeOverride.suite_case_id == suite_case_id)
    else:
        conditions.append(AppProfileNodeOverride.suite_id == suite_id)
        conditions.append(
            AppProfileNodeOverride.case_id.is_(None)
            if case_id is None else AppProfileNodeOverride.case_id == case_id
        )
    return (await db.execute(select(AppProfileNodeOverride).where(*conditions))).scalar_one_or_none()


async def list_nodes_for_membership(
    db: AsyncSession, profile_id: int, suite_case_id: int
) -> list[AppProfileNodeOverride]:
    rows = await db.execute(
        select(AppProfileNodeOverride).where(
            AppProfileNodeOverride.profile_id == profile_id,
            AppProfileNodeOverride.suite_case_id == suite_case_id,
            AppProfileNodeOverride.deleted_at.is_(None),
        )
    )
    return list(rows.scalars().all())


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
        row.locator_config = MutableDict.coerce("locator_config", locator_config)
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
    target_type: str, node_key: str, patch: dict[str, Any], user_id: int,
    suite_case_id: int | None = None,
):
    row = await get_node(
        db, profile_id, suite_id=suite_id, case_id=case_id,
        target_type=target_type, node_key=node_key, suite_case_id=suite_case_id,
    )
    if row is None:
        row = AppProfileNodeOverride(
            profile_id=profile_id, suite_id=suite_id, case_id=case_id, suite_case_id=suite_case_id,
            target_type=target_type, node_key=node_key, patch=patch,
            created_by=user_id, updated_by=user_id,
        )
        db.add(row)
        await db.flush()
    else:
        row.deleted_at = None
        row.suite_id = suite_id
        row.case_id = case_id
        row.suite_case_id = suite_case_id
        row.patch = patch
        row.updated_by = user_id
    return row


async def soft_delete(row, deleted_at: datetime, user_id: int):
    row.deleted_at = deleted_at
    row.updated_by = user_id
    return row


async def delete_for_membership(db: AsyncSession, suite_case_id: int) -> int:
    """套件用例编排项被移除时级联清理其节点覆盖。

    覆盖行以 ``suite_case_id`` 外键引用编排项，必须物理删除（含已软删行）才能让编排项删除通过外键校验。
    """
    rows = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.suite_case_id == suite_case_id
            )
        )
    ).scalars().all()
    for row in rows:
        await db.delete(row)
    if rows:
        await db.flush()
    return len(rows)
