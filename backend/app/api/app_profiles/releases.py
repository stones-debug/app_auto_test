"""APP 档案发布版本路由。"""

from fastapi import Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.models import Project, User
from app.schemas.app_profile import ReleaseCreate, ReleaseDelete, ReleaseUpdate
from app.services import app_profile_release_service as release_service
from app.services import profile_service

from . import router
from ._shared import _audit_client, require_profile_manager_by_profile, require_release_manager


@router.get("/app-profiles/{profile_id}/releases")
async def list_releases(profile_id: int, status_: str = Query(default="active", alias="status"), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await profile_service.get(db, profile_id)
    await get_project_permission(profile.project_id, user, db)
    result = await release_service.list(db, profile_id=profile_id, status_=status_, offset=(page - 1) * page_size, limit=page_size)
    return {**result, "page": page, "page_size": page_size}


@router.post("/app-profiles/{profile_id}/releases", status_code=status.HTTP_201_CREATED)
async def create_release(profile_id: int, body: ReleaseCreate, request: Request, modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await profile_service.get(db, profile_id)
    _project, role = modal_perm
    return await release_service.create(db, profile=profile, body=body, user_id=user.id, role=role, audit=_audit_client(request))


@router.patch("/app-profile-releases/{release_id}")
async def update_release(release_id: int, body: ReleaseUpdate, request: Request, modal_perm: tuple[Project, str | None] = Depends(require_release_manager()), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    release = await release_service.get(db, release_id)
    profile = await profile_service.get(db, release.profile_id)
    _project, role = modal_perm
    return await release_service.update(db, release=release, profile=profile, body=body, user_id=user.id, role=role, audit=_audit_client(request))


@router.delete("/app-profile-releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_release(release_id: int, body: ReleaseDelete, request: Request, modal_perm: tuple[Project, str | None] = Depends(require_release_manager()), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    release = await release_service.get(db, release_id)
    profile = await profile_service.get(db, release.profile_id)
    _project, role = modal_perm
    await release_service.delete(db, release=release, profile=profile, body=body, user_id=user.id, role=role, audit=_audit_client(request))
