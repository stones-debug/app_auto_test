"""APP 档案路由。"""

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.core.errors import api_error
from app.models import Project, User
from app.schemas.app_profile import AppProfileCreate, AppProfileDelete, AppProfileUpdate
from app.services import profile_service
from app.services.profile_revision import RevisionConflictError

from . import router
from ._shared import (
    _audit_client,
    _broadcast_config,
    require_profile_manager,
    require_profile_manager_by_profile,
)


def _revision_error(error: RevisionConflictError):
    return api_error(
        status.HTTP_409_CONFLICT, error.code, "APP 档案版本已变化，请重新加载后再保存",
        {"current": error.current, "expected": error.expected},
    )


@router.get("/projects/{project_id}/app-profiles")
async def list_app_profiles(project_id: int, include_disabled: bool = False, keyword: str = "", perm: tuple[Project, str | None] = Depends(get_project_permission), db: AsyncSession = Depends(get_db)):
    return await profile_service.list(db, project_id=project_id, include_disabled=include_disabled, keyword=keyword)


@router.post("/projects/{project_id}/app-profiles", status_code=status.HTTP_201_CREATED)
async def create_app_profile(project_id: int, body: AppProfileCreate, request: Request, modal_perm: tuple[Project, str | None] = Depends(require_profile_manager()), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    _project, role = modal_perm
    return await profile_service.create(db, project_id=project_id, body=body, user_id=user.id, role=role, audit=_audit_client(request))


@router.get("/app-profiles/{profile_id}")
async def get_app_profile(profile_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await profile_service.get(db, profile_id)
    await get_project_permission(profile.project_id, user, db)
    return await profile_service.get_with_counts(db, profile_id)


@router.patch("/app-profiles/{profile_id}")
async def update_app_profile(profile_id: int, body: AppProfileUpdate, request: Request, modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await profile_service.get(db, profile_id)
    _project, role = modal_perm
    try:
        response = await profile_service.update(db, profile=profile, body=body, user_id=user.id, role=role, audit=_audit_client(request))
    except RevisionConflictError as error:
        raise _revision_error(error) from None
    await _broadcast_config(profile, user.id)
    return response


@router.delete("/app-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app_profile(profile_id: int, body: AppProfileDelete, request: Request, modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await profile_service.get(db, profile_id)
    _project, role = modal_perm
    try:
        await profile_service.delete(db, profile=profile, body=body, user_id=user.id, role=role, audit=_audit_client(request))
    except RevisionConflictError as error:
        raise _revision_error(error) from None
    await _broadcast_config(profile, user.id)
