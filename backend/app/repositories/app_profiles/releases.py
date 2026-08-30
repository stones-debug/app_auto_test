"""APP 档案发布版本数据访问。"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppProfileRelease


async def get_by_id(db: AsyncSession, release_id: int) -> AppProfileRelease | None:
    return await db.get(AppProfileRelease, release_id)


async def find_active_for_profile(db: AsyncSession, profile_id: int) -> AppProfileRelease | None:
    return (await db.execute(select(AppProfileRelease).where(AppProfileRelease.profile_id == profile_id, AppProfileRelease.status == "active", AppProfileRelease.deleted_at.is_(None)).order_by((AppProfileRelease.version == "未标注历史版本").desc(), AppProfileRelease.id))).scalars().first()


async def find_version(db: AsyncSession, profile_id: int, version: str) -> AppProfileRelease | None:
    return (await db.execute(select(AppProfileRelease).where(AppProfileRelease.profile_id == profile_id, AppProfileRelease.version == version, AppProfileRelease.deleted_at.is_(None)))).scalar_one_or_none()


async def lock_for_resolution(db: AsyncSession, *, release_id: int, profile_id: int):
    return (await db.execute(select(AppProfileRelease.status, AppProfileRelease.version).where(AppProfileRelease.id == release_id, AppProfileRelease.profile_id == profile_id, AppProfileRelease.deleted_at.is_(None)).with_for_update())).one_or_none()


async def list_for_profile(
    db: AsyncSession, *, profile_id: int, status: str, offset: int, limit: int
) -> tuple[int, list[AppProfileRelease]]:
    query = select(AppProfileRelease).where(
        AppProfileRelease.profile_id == profile_id,
        AppProfileRelease.deleted_at.is_(None),
    )
    if status and status != "all":
        query = query.where(AppProfileRelease.status == status)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = await db.execute(
        query.order_by(AppProfileRelease.created_at.desc()).offset(offset).limit(limit)
    )
    return total or 0, list(rows.scalars().all())


async def find_version_conflict(
    db: AsyncSession, *, profile_id: int, version: str, build_number: str | None, exclude_id: int | None = None
) -> AppProfileRelease | None:
    conditions = [
        AppProfileRelease.profile_id == profile_id,
        AppProfileRelease.version == version,
        AppProfileRelease.deleted_at.is_(None),
        func.coalesce(AppProfileRelease.build_number, "") == (build_number or ""),
    ]
    if exclude_id is not None:
        conditions.append(AppProfileRelease.id != exclude_id)
    return (await db.execute(select(AppProfileRelease).where(*conditions))).scalar_one_or_none()


async def create(
    db: AsyncSession, *, profile_id: int, version: str, build_number: str | None,
    description: str | None, user_id: int
) -> AppProfileRelease:
    release = AppProfileRelease(
        profile_id=profile_id, version=version, build_number=build_number,
        description=description, created_by=user_id, updated_by=user_id,
    )
    db.add(release)
    await db.flush()
    return release


async def update_fields(
    release: AppProfileRelease, *, fields: set[str], values: dict[str, Any], user_id: int
) -> AppProfileRelease:
    for field in ("version", "build_number", "description", "status"):
        if field in fields and values.get(field) is not None:
            setattr(release, field, values[field])
    release.updated_by = user_id
    return release


async def soft_delete(release: AppProfileRelease, deleted_at: datetime, user_id: int) -> AppProfileRelease:
    release.deleted_at = deleted_at
    release.status = "disabled"
    release.updated_by = user_id
    return release


async def refresh(db: AsyncSession, release: AppProfileRelease) -> AppProfileRelease:
    await db.flush()
    await db.refresh(release)
    return release
