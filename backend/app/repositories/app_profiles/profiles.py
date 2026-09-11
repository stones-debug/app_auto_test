"""APP 档案、统计、revision 与审计的数据访问。"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppProfile,
    AppProfileAuditLog,
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileRelease,
    AppProfileSkipRule,
    AppProfileVariableOverride,
    Execution,
    Project,
)


async def list_for_project(
    db: AsyncSession, *, project_id: int, include_disabled: bool, keyword: str
) -> list[AppProfile]:
    query = select(AppProfile).where(
        AppProfile.project_id == project_id, AppProfile.deleted_at.is_(None)
    )
    if not include_disabled:
        query = query.where(AppProfile.status == "active")
    if keyword:
        lower = keyword.lower()
        query = query.where(
            func.lower(AppProfile.name).like(f"%{lower}%")
            | func.lower(AppProfile.code).like(f"%{lower}%")
        )
    rows = await db.execute(query.order_by(AppProfile.updated_at.desc()))
    return list(rows.scalars().all())


async def get_by_id(db: AsyncSession, profile_id: int) -> AppProfile | None:
    return await db.get(AppProfile, profile_id)


async def find_default(db: AsyncSession, project_id: int) -> AppProfile | None:
    return (await db.execute(select(AppProfile).where(AppProfile.project_id == project_id, AppProfile.name == "通用配置（待调整）", AppProfile.status == "active", AppProfile.deleted_at.is_(None)))).scalar_one_or_none()


async def find_named(db: AsyncSession, project_id: int, name: str) -> AppProfile | None:
    return (await db.execute(select(AppProfile).where(AppProfile.project_id == project_id, AppProfile.name == name, AppProfile.deleted_at.is_(None)))).scalar_one_or_none()


async def lock_revision(db: AsyncSession, profile_id: int) -> int | None:
    return await db.scalar(select(AppProfile.revision).where(AppProfile.id == profile_id, AppProfile.deleted_at.is_(None)).with_for_update())


async def get_project_revision(db: AsyncSession, project_id: int) -> int | None:
    return await db.scalar(select(Project.test_asset_revision).where(Project.id == project_id, Project.deleted_at.is_(None)).with_for_update())


async def find_name_or_code_conflict(
    db: AsyncSession,
    *,
    project_id: int,
    name: str,
    code: str | None = None,
    exclude_id: int | None = None,
) -> tuple[AppProfile | None, AppProfile | None]:
    conditions = [AppProfile.project_id == project_id, AppProfile.deleted_at.is_(None)]
    if exclude_id is not None:
        conditions.append(AppProfile.id != exclude_id)
    name_row = await db.execute(
        select(AppProfile).where(*conditions, func.lower(AppProfile.name) == name.lower())
    )
    code_row = None
    if code is not None:
        code_result = await db.execute(select(AppProfile).where(*conditions, AppProfile.code == code))
        code_row = code_result.scalar_one_or_none()
    return name_row.scalar_one_or_none(), code_row


async def create(
    db: AsyncSession,
    *,
    project_id: int,
    name: str,
    code: str,
    description: str | None,
    inherit_all: bool,
    user_id: int,
) -> AppProfile:
    profile = AppProfile(
        project_id=project_id,
        name=name,
        code=code,
        description=description,
        inherit_all=inherit_all,
        created_by=user_id,
        updated_by=user_id,
        revision=1,
    )
    db.add(profile)
    await db.flush()
    return profile


async def update_fields(
    profile: AppProfile, *, fields: set[str], values: dict[str, Any], user_id: int
) -> AppProfile:
    for field in ("name", "description", "status", "inherit_all"):
        if field in fields and values.get(field) is not None:
            setattr(profile, field, values[field])
    profile.updated_by = user_id
    return profile


async def soft_delete(profile: AppProfile, deleted_at: datetime, user_id: int) -> AppProfile:
    profile.deleted_at = deleted_at
    profile.status = "disabled"
    profile.updated_by = user_id
    return profile


async def refresh(db: AsyncSession, profile: AppProfile) -> AppProfile:
    await db.flush()
    await db.refresh(profile)
    return profile


async def has_active_execution(db: AsyncSession, profile_id: int) -> bool:
    count = await db.scalar(
        select(func.count()).select_from(Execution).where(
            Execution.app_profile_id == profile_id,
            Execution.status.in_(["queued", "running", "stopping"]),
        )
    )
    return bool(count)


async def compare_and_bump_revision(
    db: AsyncSession, profile_id: int, expected_revision: int, actor_id: int | None
) -> int | None:
    result = await db.execute(
        update(AppProfile)
        .where(
            AppProfile.id == profile_id,
            AppProfile.revision == expected_revision,
            AppProfile.deleted_at.is_(None),
        )
        .values(
            revision=AppProfile.revision + 1,
            updated_by=actor_id,
            updated_at=func.now(),
        )
        .returning(AppProfile.revision)
    )
    return result.scalar_one_or_none()


async def profile_counts(db: AsyncSession, profile_ids: list[int]) -> dict[int, dict[str, Any]]:
    result = {
        profile_id: {
            "release_count": 0,
            "skip_counts": {"suite": 0, "case": 0, "step": 0, "assertion": 0},
            "override_counts": {"element": 0, "variable": 0, "node": 0},
        }
        for profile_id in profile_ids
    }
    if not profile_ids:
        return result
    rows = await db.execute(
        select(AppProfileRelease.profile_id, func.count())
        .where(AppProfileRelease.profile_id.in_(profile_ids), AppProfileRelease.deleted_at.is_(None))
        .group_by(AppProfileRelease.profile_id)
    )
    for profile_id, count in rows.all():
        result[profile_id]["release_count"] = count
    rows = await db.execute(
        select(AppProfileSkipRule.profile_id, AppProfileSkipRule.target_type, func.count())
        .where(AppProfileSkipRule.profile_id.in_(profile_ids), AppProfileSkipRule.deleted_at.is_(None))
        .group_by(AppProfileSkipRule.profile_id, AppProfileSkipRule.target_type)
    )
    for profile_id, target_type, count in rows.all():
        if target_type in result[profile_id]["skip_counts"]:
            result[profile_id]["skip_counts"][target_type] = count
    for model, key in (
        (AppProfileElementOverride, "element"),
        (AppProfileVariableOverride, "variable"),
        (AppProfileNodeOverride, "node"),
    ):
        rows = await db.execute(
            select(model.profile_id, func.count())
            .where(model.profile_id.in_(profile_ids), model.deleted_at.is_(None))
            .group_by(model.profile_id)
        )
        for profile_id, count in rows.all():
            result[profile_id]["override_counts"][key] = count
    return result


async def find_audit_replay(
    db: AsyncSession,
    *,
    profile_id: int | None = None,
    project_id: int | None = None,
    request_id: str,
    action: str | None = None,
) -> dict | None:
    if not request_id:
        return None
    conditions = [AppProfileAuditLog.request_id == request_id]
    if profile_id is not None:
        conditions.append(AppProfileAuditLog.profile_id == profile_id)
    if project_id is not None:
        conditions.append(AppProfileAuditLog.project_id == project_id)
    if action is not None:
        conditions.append(AppProfileAuditLog.action == action)
    row = (await db.execute(select(AppProfileAuditLog).where(*conditions))).scalar_one_or_none()
    return row.response_data if row is not None else None


AUDIT_ACTIONS = frozenset(
    {
        "profile_create", "profile_update", "profile_disable",
        "release_create", "release_update", "release_disable",
        "skip_batch", "restore_batch", "element_override_upsert", "element_override_restore",
        "variable_override_upsert", "variable_override_restore", "node_override_upsert", "node_override_restore",
        "node_override_batch",
    }
)


async def add_audit(
    db: AsyncSession,
    *,
    profile_id: int,
    project_id: int,
    action: str,
    actor_id: int | None,
    actor_role: str | None,
    revision_before: int,
    revision_after: int,
    changes: list | None = None,
    response_data: dict | None = None,
    request_id: str | None = None,
    batch_id: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> AppProfileAuditLog:
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"未知审计动作: {action}")
    try:
        normalized_request_id = str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
    except ValueError:
        raise ValueError(f"request_id 必须为 UUID: {request_id}") from None
    log = AppProfileAuditLog(
        profile_id=profile_id,
        project_id=project_id,
        request_id=normalized_request_id,
        batch_id=batch_id,
        action=action,
        actor_id=actor_id,
        actor_role=actor_role,
        revision_before=revision_before,
        revision_after=revision_after,
        changes=changes or [],
        response_data=response_data or {},
        client_ip=client_ip,
        user_agent=user_agent,
    )
    db.add(log)
    await db.flush()
    return log
