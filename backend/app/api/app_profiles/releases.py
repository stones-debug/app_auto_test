"""APP 档案releases路由。"""

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.models import AppProfileRelease, Project, User
from app.schemas.app_profile import ReleaseCreate, ReleaseDelete, ReleaseUpdate
from app.services.profile_audit import find_idempotent_replay, write_audit

from . import router
from ._shared import (
    _audit_client,
    _get_profile_or_404,
    _release_out,
    require_profile_manager_by_profile,
    require_release_manager,
)


@router.get("/app-profiles/{profile_id}/releases")
async def list_releases(
    profile_id: int,
    status_: str = Query(default="active", alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
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
    request: Request,
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
    response = {**_release_out(release), "request_id": body.request_id}
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
        response_data=jsonable_encoder(response),
        **_audit_client(request),
    )
    await db.commit()
    await db.refresh(release)
    return response

@router.patch("/app-profile-releases/{release_id}")
async def update_release(
    release_id: int,
    body: ReleaseUpdate,
    request: Request,
    modal_perm: tuple[Project, str | None] = Depends(require_release_manager()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    release = await db.get(AppProfileRelease, release_id)
    if release is None or release.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="发布版本不存在")
    profile = await _get_profile_or_404(release.profile_id, db)
    _project, role = modal_perm
    if body.request_id:
        replay = await find_idempotent_replay(db, profile.id, body.request_id)
        if replay is not None:
            return replay
    for field in ("version", "build_number", "description", "status"):
        if field in body.model_fields_set and getattr(body, field) is not None:
            setattr(release, field, getattr(body, field))
    release.updated_by = user.id
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同版本号发布版本已存在") from None
    await db.refresh(release)
    response = {**_release_out(release), "request_id": body.request_id}
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
        response_data=jsonable_encoder(response),
        **_audit_client(request),
    )
    await db.commit()
    await db.refresh(release)
    return response

@router.delete("/app-profile-releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_release(
    release_id: int,
    body: ReleaseDelete,
    request: Request,
    modal_perm: tuple[Project, str | None] = Depends(require_release_manager()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    release = await db.get(AppProfileRelease, release_id)
    if release is None or release.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="发布版本不存在")
    profile = await _get_profile_or_404(release.profile_id, db)
    _project, role = modal_perm
    if body.request_id:
        replay = await find_idempotent_replay(db, profile.id, body.request_id)
        if replay is not None:
            return
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
        **_audit_client(request),
    )
    await db.commit()
