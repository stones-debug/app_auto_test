"""APP 档案与发布版本接口（方案 §4.2/§4.3）。

安全要求：所有按 profile_id 访问的接口反查 project_id 并调用项目权限依赖。
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.models import (
    AppProfile,
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileRelease,
    AppProfileSkipRule,
    AppProfileVariableOverride,
    Execution,
    Project,
    User,
)
from app.schemas.app_profile import (
    AppProfileCreate,
    AppProfileDelete,
    AppProfileUpdate,
    ReleaseCreate,
    ReleaseDelete,
    ReleaseUpdate,
)
from app.services.profile_audit import find_idempotent_replay, write_audit
from app.services.profile_revision import RevisionConflictError, bump_profile_revision

router = APIRouter(tags=["APP 档案"])


def require_profile_manager():
    """按 project_id 路径的 Owner/Admin 校验（创建/列表类接口）。"""

    async def _checker(
        perm: tuple[Project, str | None] = Depends(get_project_permission),
    ) -> tuple[Project, str | None]:
        _project, role = perm
        if role not in ("owner", "admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要 Owner/Admin 权限")
        return perm

    return _checker


def require_profile_manager_by_profile():
    """按 profile_id 路径的 Owner/Admin 校验（方案 §4.10 反查 project_id）。"""

    async def _checker(
        profile_id: int,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> tuple[Project, str | None]:
        profile = await _get_profile_or_404(profile_id, db)
        perm = await get_project_permission(profile.project_id, user, db)
        _project, role = perm
        if role not in ("owner", "admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要 Owner/Admin 权限")
        return perm

    return _checker


def require_release_manager():
    """按 release_id 路径的 Owner/Admin 校验：release → profile → project 反查。"""

    async def _checker(
        release_id: int,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> tuple[Project, str | None]:
        release = await db.get(AppProfileRelease, release_id)
        if release is None or release.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="发布版本不存在")
        profile = await _get_profile_or_404(release.profile_id, db)
        perm = await get_project_permission(profile.project_id, user, db)
        _project, role = perm
        if role not in ("owner", "admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要 Owner/Admin 权限")
        return perm

    return _checker


async def _get_profile_or_404(profile_id: int, db: AsyncSession) -> AppProfile:
    profile = await db.get(AppProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="APP 档案不存在")
    return profile


async def _profile_out(profile: AppProfile, db: AsyncSession) -> dict:
    release_count = await db.scalar(
        select(func.count())
        .select_from(AppProfileRelease)
        .where(AppProfileRelease.profile_id == profile.id, AppProfileRelease.deleted_at.is_(None))
    )
    skip_rows = (
        await db.execute(
            select(AppProfileSkipRule.target_type, func.count())
            .where(AppProfileSkipRule.profile_id == profile.id, AppProfileSkipRule.deleted_at.is_(None))
            .group_by(AppProfileSkipRule.target_type)
        )
    ).all()
    skip_counts = {"suite": 0, "case": 0, "step": 0, "assertion": 0}
    for target_type, cnt in skip_rows:
        skip_counts[target_type] = cnt
    element_cnt = await db.scalar(
        select(func.count()).select_from(AppProfileElementOverride).where(
            AppProfileElementOverride.profile_id == profile.id, AppProfileElementOverride.deleted_at.is_(None)
        )
    )
    variable_cnt = await db.scalar(
        select(func.count()).select_from(AppProfileVariableOverride).where(
            AppProfileVariableOverride.profile_id == profile.id, AppProfileVariableOverride.deleted_at.is_(None)
        )
    )
    node_cnt = await db.scalar(
        select(func.count()).select_from(AppProfileNodeOverride).where(
            AppProfileNodeOverride.profile_id == profile.id, AppProfileNodeOverride.deleted_at.is_(None)
        )
    )
    return {
        "id": profile.id,
        "project_id": profile.project_id,
        "name": profile.name,
        "code": profile.code,
        "description": profile.description,
        "status": profile.status,
        "inherit_all": profile.inherit_all,
        "revision": profile.revision,
        "release_count": release_count or 0,
        "skip_counts": skip_counts,
        "override_counts": {
            "element": element_cnt or 0,
            "variable": variable_cnt or 0,
            "node": node_cnt or 0,
        },
        "created_by": profile.created_by,
        "updated_by": profile.updated_by,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _release_out(release: AppProfileRelease) -> dict:
    return {
        "id": release.id,
        "profile_id": release.profile_id,
        "version": release.version,
        "build_number": release.build_number,
        "description": release.description,
        "status": release.status,
        "created_by": release.created_by,
        "created_at": release.created_at,
        "updated_at": release.updated_at,
    }


# ---------- 档案 ----------


@router.get("/projects/{project_id}/app-profiles")
async def list_app_profiles(
    project_id: int,
    include_disabled: bool = False,
    keyword: str = "",
    perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    query = select(AppProfile).where(AppProfile.project_id == project_id)
    if not include_disabled:
        query = query.where(AppProfile.status == "active")
    query = query.where(AppProfile.deleted_at.is_(None))
    if keyword:
        lower = keyword.lower()
        query = query.where(
            func.lower(AppProfile.name).like(f"%{lower}%") | func.lower(AppProfile.code).like(f"%{lower}%")
        )
    rows = (await db.execute(query.order_by(AppProfile.updated_at.desc()))).scalars().all()
    return [await _profile_out(r, db) for r in rows]


@router.post("/projects/{project_id}/app-profiles", status_code=status.HTTP_201_CREATED)
async def create_app_profile(
    project_id: int,
    body: AppProfileCreate,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _project, role = modal_perm
    existing = (
        await db.execute(
            select(AppProfile).where(
                AppProfile.project_id == project_id,
                func.lower(AppProfile.name) == body.name.lower(),
                AppProfile.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同名 APP 档案已存在")
    profile = AppProfile(
        project_id=project_id,
        name=body.name,
        code=body.code,
        description=body.description,
        inherit_all=body.inherit_all,
        created_by=user.id,
        updated_by=user.id,
        revision=1,
    )
    db.add(profile)
    await db.flush()
    await write_audit(
        db,
        profile_id=profile.id,
        project_id=project_id,
        action="profile_create",
        actor_id=user.id,
        actor_role=role,
        revision_before=0,
        revision_after=1,
        request_id=body.request_id,
    )
    await db.commit()
    await db.refresh(profile)
    return {**await _profile_out(profile, db), "request_id": body.request_id}


@router.get("/app-profiles/{profile_id}")
async def get_app_profile(
    profile_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    await get_project_permission(profile.project_id, user, db)
    return await _profile_out(profile, db)


@router.patch("/app-profiles/{profile_id}")
async def update_app_profile(
    profile_id: int,
    body: AppProfileUpdate,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    if body.request_id:
        replay = await find_idempotent_replay(db, profile_id, body.request_id)
        if replay is not None:
            return replay
    before = profile.revision
    try:
        new_revision = await bump_profile_revision(db, profile_id, body.expected_revision, user.id)
    except RevisionConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": err.code, "current": err.current, "expected": err.expected},
        ) from None
    for field in ("name", "description", "status", "inherit_all"):
        if field in body.model_fields_set and getattr(body, field) is not None:
            setattr(profile, field, getattr(body, field))
    profile.updated_by = user.id
    await write_audit(
        db,
        profile_id=profile_id,
        project_id=profile.project_id,
        action="profile_update",
        actor_id=user.id,
        actor_role=role,
        revision_before=before,
        revision_after=new_revision,
        request_id=body.request_id,
    )
    await db.commit()
    await db.refresh(profile)
    return {**await _profile_out(profile, db), "request_id": body.request_id}


@router.delete("/app-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app_profile(
    profile_id: int,
    body: AppProfileDelete,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    profile_id_ = profile.id
    active = await db.scalar(
        select(func.count()).select_from(Execution).where(
            Execution.app_profile_id == profile_id_,
            Execution.status.in_(["queued", "running", "stopping"]),
        )
    )
    if active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="档案仍有非终态执行")
    before = profile.revision
    try:
        new_revision = await bump_profile_revision(db, profile_id_, body.expected_revision, user.id)
    except RevisionConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": err.code, "current": err.current, "expected": err.expected},
        ) from None
    profile.deleted_at = datetime.now(UTC)
    profile.status = "disabled"
    profile.updated_by = user.id
    await write_audit(
        db,
        profile_id=profile_id_,
        project_id=profile.project_id,
        action="profile_disable",
        actor_id=user.id,
        actor_role=role,
        revision_before=before,
        revision_after=new_revision,
        request_id=body.request_id,
    )
    await db.commit()


# ---------- 发布版本 ----------


@router.get("/app-profiles/{profile_id}/releases")
async def list_releases(
    profile_id: int,
    status_: str = "active",
    page: int = 1,
    page_size: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    await get_project_permission(profile.project_id, user, db)
    query = select(AppProfileRelease).where(AppProfileRelease.profile_id == profile_id)
    if status_ and status_ != "all":
        query = query.where(AppProfileRelease.status == status_)
    query = query.where(AppProfileRelease.deleted_at.is_(None))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    items = (
        await db.execute(
            query.order_by(AppProfileRelease.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "total": total or 0,
        "page": page,
        "page_size": page_size,
        "items": [_release_out(r) for r in items],
    }


@router.post("/app-profiles/{profile_id}/releases", status_code=status.HTTP_201_CREATED)
async def create_release(
    profile_id: int,
    body: ReleaseCreate,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    existing = (
        await db.execute(
            select(AppProfileRelease).where(
                AppProfileRelease.profile_id == profile_id,
                AppProfileRelease.version == body.version,
                AppProfileRelease.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同版本号发布版本已存在")
    release = AppProfileRelease(
        profile_id=profile_id,
        version=body.version,
        build_number=body.build_number,
        description=body.description,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(release)
    await db.flush()
    await write_audit(
        db,
        profile_id=profile_id,
        project_id=profile.project_id,
        action="release_create",
        actor_id=user.id,
        actor_role=role,
        revision_before=profile.revision,
        revision_after=profile.revision,
        request_id=body.request_id,
    )
    await db.commit()
    await db.refresh(release)
    return {**_release_out(release), "request_id": body.request_id}


@router.patch("/app-profile-releases/{release_id}")
async def update_release(
    release_id: int,
    body: ReleaseUpdate,
    modal_perm: tuple[Project, str | None] = Depends(require_release_manager()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    release = await db.get(AppProfileRelease, release_id)
    if release is None or release.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="发布版本不存在")
    profile = await _get_profile_or_404(release.profile_id, db)
    _project, role = modal_perm
    for field in ("version", "build_number", "description", "status"):
        if field in body.model_fields_set and getattr(body, field) is not None:
            setattr(release, field, getattr(body, field))
    release.updated_by = user.id
    await write_audit(
        db,
        profile_id=profile.id,
        project_id=profile.project_id,
        action="release_update",
        actor_id=user.id,
        actor_role=role,
        revision_before=profile.revision,
        revision_after=profile.revision,
        request_id=body.request_id,
    )
    await db.commit()
    await db.refresh(release)
    return {**_release_out(release), "request_id": body.request_id}


@router.delete("/app-profile-releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_release(
    release_id: int,
    body: ReleaseDelete,
    modal_perm: tuple[Project, str | None] = Depends(require_release_manager()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    release = await db.get(AppProfileRelease, release_id)
    if release is None or release.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="发布版本不存在")
    profile = await _get_profile_or_404(release.profile_id, db)
    _project, role = modal_perm
    release.deleted_at = datetime.now(UTC)
    release.status = "disabled"
    release.updated_by = user.id
    await write_audit(
        db,
        profile_id=profile.id,
        project_id=profile.project_id,
        action="release_disable",
        actor_id=user.id,
        actor_role=role,
        revision_before=profile.revision,
        revision_after=profile.revision,
        request_id=body.request_id,
    )
    await db.commit()
