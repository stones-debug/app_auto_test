"""测试资产写事务的公共编排。"""

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.profile_revision import (
    touch_all_project_asset_revisions,
    touch_project_asset_revision,
)


async def commit_asset_change(db: AsyncSession, project_ids: Iterable[int]) -> None:
    """在同一事务中推进资产 revision 并提交；任一环节失败都回滚。"""
    try:
        for project_id in dict.fromkeys(project_ids):
            await touch_project_asset_revision(db, project_id)
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def commit_global_asset_change(db: AsyncSession) -> None:
    try:
        await touch_all_project_asset_revisions(db)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
