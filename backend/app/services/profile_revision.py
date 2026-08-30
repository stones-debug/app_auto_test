"""Profile 与项目资产乐观锁（方案 §2.9）。

所有配置/资产写操作必须经此处锁定行并校验 expected revision，最后递增一次，
禁止通过数据库 trigger 对每条批量记录分别递增。
"""

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppProfile, Project
from app.repositories import projects as projects_repo

REVISION_CONFLICT = "PROFILE_REVISION_CONFLICT"
ASSET_REVISION_CONFLICT = "TEST_ASSET_REVISION_CONFLICT"


class RevisionConflictError(Exception):
    """expected revision 与当前不符，返回 409。"""

    def __init__(self, code: str, current: int, expected: int) -> None:
        self.code = code
        self.current = current
        self.expected = expected
        super().__init__(f"{code}: 期望 {expected}，实际 {current}")


async def bump_profile_revision(
    db: AsyncSession, profile_id: int, expected_revision: int, actor_id: int | None
) -> int:
    """锁定档案行并递增 revision；冲突抛 RevisionConflictError。"""
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
    new_revision = result.scalar_one_or_none()
    if new_revision is None:
        existing = await db.get(AppProfile, profile_id)
        if existing is None or existing.deleted_at is not None:
            raise RevisionConflictError("APP_PROFILE_NOT_FOUND", 0, expected_revision)
        raise RevisionConflictError(REVISION_CONFLICT, existing.revision, expected_revision)
    return new_revision


async def bump_project_asset_revision(
    db: AsyncSession, project_id: int, expected_revision: int, actor_id: int | None
) -> int:
    """锁定项目行并递增 test_asset_revision；冲突抛 RevisionConflictError。"""
    result = await db.execute(
        update(Project)
        .where(Project.id == project_id, Project.test_asset_revision == expected_revision)
        .values(
            test_asset_revision=Project.test_asset_revision + 1,
            updated_at=func.now(),
        )
        .returning(Project.test_asset_revision)
    )
    new_revision = result.scalar_one_or_none()
    if new_revision is None:
        existing = await db.get(Project, project_id)
        if existing is None or existing.deleted_at is not None:
            raise RevisionConflictError("PROJECT_NOT_FOUND", 0, expected_revision)
        raise RevisionConflictError(ASSET_REVISION_CONFLICT, existing.test_asset_revision, expected_revision)
    return new_revision


async def touch_project_asset_revision(db: AsyncSession, project_id: int) -> int:
    """公共测试资产写入时原子递增项目资产修订号。

    资产管理接口本身没有 ``expected_revision`` 参数，因此不能复用档案配置
    工作台的乐观锁语义；但每个成功的资产写事务都必须至少推进一次修订号，
    使预检/执行提交能够发现两次操作之间发生的公共资产变化。
    """
    try:
        return await projects_repo.touch_asset_revision(db, project_id)
    except ValueError as exc:
        raise RevisionConflictError("PROJECT_NOT_FOUND", 0, 0) from exc


async def touch_all_project_asset_revisions(db: AsyncSession) -> None:
    """全局变量变化时使所有有效项目的资产快照失效。"""
    await projects_repo.touch_all_asset_revisions(db)
