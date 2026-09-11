"""APP 档案跳过规则数据访问。"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppProfileSkipRule, TestCase, TestSuite, TestSuiteCase


async def load_target(
    db: AsyncSession, *, project_id: int, suite_id: int | None, suite_case_id: int | None
):
    suite = await db.get(TestSuite, suite_id) if suite_id is not None else None
    membership = await db.get(TestSuiteCase, suite_case_id) if suite_case_id is not None else None
    if suite is None and membership is not None:
        suite = await db.get(TestSuite, membership.suite_id)
    case = await db.get(TestCase, membership.case_id) if membership is not None else None
    if suite is not None and suite.project_id != project_id:
        suite = None
    if case is not None and case.project_id != project_id:
        case = None
    if membership is not None and suite is not None and membership.suite_id != suite.id:
        membership = None
        case = None
    return suite, case, membership


async def load_existing(db: AsyncSession, profile_id: int, fields: dict[str, Any]):
    conditions = [
        AppProfileSkipRule.profile_id == profile_id,
        AppProfileSkipRule.target_type == fields["target_type"],
        AppProfileSkipRule.deleted_at.is_(None),
    ]
    for field in ("suite_id", "suite_case_id", "node_key"):
        value = fields.get(field)
        column = getattr(AppProfileSkipRule, field)
        conditions.append(column.is_(None) if value is None else column == value)
    return (await db.execute(select(AppProfileSkipRule).where(*conditions))).scalar_one_or_none()


async def upsert_many(
    db: AsyncSession, *, profile_id: int, targets: list[tuple[int, dict[str, Any]]], reason,
    user_id: int
) -> tuple[list[dict], int, int]:
    results: list[dict] = []
    changed = unchanged = 0
    for index, fields in targets:
        existing = await load_existing(db, profile_id, fields)
        if existing is not None:
            unchanged += 1
            results.append({"index": index, "status": "unchanged", "rule_id": existing.id})
            continue
        row = AppProfileSkipRule(
            profile_id=profile_id, target_type=fields["target_type"],
            suite_id=fields.get("suite_id"), suite_case_id=fields.get("suite_case_id"),
            node_key=fields.get("node_key"), reason_code=reason.code,
            reason_note=reason.note, created_by=user_id, updated_by=user_id,
        )
        db.add(row)
        await db.flush()
        changed += 1
        results.append({"index": index, "status": "changed", "rule_id": row.id})
    return results, changed, unchanged


async def restore_many(
    db: AsyncSession, *, profile_id: int, targets: list[tuple[int, dict[str, Any]]], deleted_at: datetime,
    user_id: int
) -> tuple[list[dict], int, int]:
    results: list[dict] = []
    changed = unchanged = 0
    for index, fields in targets:
        existing = await load_existing(db, profile_id, fields)
        if existing is None:
            unchanged += 1
            results.append({"index": index, "status": "unchanged", "rule_id": None})
            continue
        existing.deleted_at = deleted_at
        existing.updated_by = user_id
        changed += 1
        results.append({"index": index, "status": "changed", "rule_id": existing.id})
    return results, changed, unchanged
