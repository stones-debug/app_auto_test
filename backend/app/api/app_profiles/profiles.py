"""APP 档案profiles路由。"""

from datetime import UTC, datetime

from fastapi import Depends, Request, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.core.errors import api_error
from app.models import AppProfile, Execution, Project, User
from app.schemas.app_profile import AppProfileCreate, AppProfileDelete, AppProfileUpdate
from app.services.profile_audit import (
    find_idempotent_replay,
    find_project_idempotent_replay,
    write_audit,
)
from app.services.profile_revision import RevisionConflictError, bump_profile_revision

from . import router
from ._shared import (
    _audit_client,
    _broadcast_config,
    _get_profile_or_404,
    _profile_out,
    _profiles_out,
    require_profile_manager,
    require_profile_manager_by_profile,
)


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
    return await _profiles_out(list(rows), db)

@router.post("/projects/{project_id}/app-profiles", status_code=status.HTTP_201_CREATED)
async def create_app_profile(
    project_id: int,
    body: AppProfileCreate,
    request: Request,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _project, role = modal_perm
    if body.request_id:
        replay = await find_project_idempotent_replay(
            db, project_id, body.request_id, action="profile_create"
        )
        if replay is not None:
            return replay
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
        raise api_error(status.HTTP_409_CONFLICT, "APP_PROFILE_NAME_EXISTS", "同名 APP 档案已存在")
    duplicate_code = await db.scalar(
        select(AppProfile.id).where(
            AppProfile.project_id == project_id,
            AppProfile.code == body.code,
            AppProfile.deleted_at.is_(None),
        )
    )
    if duplicate_code is not None:
        raise api_error(status.HTTP_409_CONFLICT, "APP_PROFILE_CODE_EXISTS", "同编码 APP 档案已存在")
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
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise api_error(status.HTTP_409_CONFLICT, "APP_PROFILE_EXISTS", "APP 档案名称或编码已存在") from None
    response = {**await _profile_out(profile, db), "request_id": body.request_id}
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
        response_data=jsonable_encoder(response),
        **_audit_client(request),
    )
    await db.commit()
    await db.refresh(profile)
    return response

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
    before = profile.revision
    try:
        new_revision = await bump_profile_revision(db, profile_id, body.expected_revision, user.id)
    except RevisionConflictError as err:
        raise api_error(
            status.HTTP_409_CONFLICT,
            err.code,
            "APP 档案版本已变化，请重新加载后再保存",
            {"current": err.current, "expected": err.expected},
        ) from None
    for field in ("name", "description", "status", "inherit_all"):
        if field in body.model_fields_set and getattr(body, field) is not None:
            setattr(profile, field, getattr(body, field))
    profile.updated_by = user.id
    await db.flush()
    await db.refresh(profile)
    response = {**await _profile_out(profile, db), "request_id": body.request_id}
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
        response_data=jsonable_encoder(response),
        **_audit_client(request),
    )
    await db.commit()
    await db.refresh(profile)
    await _broadcast_config(profile, user.id)
    return response

@router.delete("/app-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app_profile(
    profile_id: int,
    body: AppProfileDelete,
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
            return
    profile_id_ = profile.id
    active = await db.scalar(
        select(func.count()).select_from(Execution).where(
            Execution.app_profile_id == profile_id_,
            Execution.status.in_(["queued", "running", "stopping"]),
        )
    )
    if active:
        raise api_error(status.HTTP_409_CONFLICT, "APP_PROFILE_HAS_ACTIVE_EXECUTIONS", "档案仍有非终态执行")
    before = profile.revision
    try:
        new_revision = await bump_profile_revision(db, profile_id_, body.expected_revision, user.id)
    except RevisionConflictError as err:
        raise api_error(
            status.HTTP_409_CONFLICT,
            err.code,
            "APP 档案版本已变化，请重新加载后再保存",
            {"current": err.current, "expected": err.expected},
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
        **_audit_client(request),
    )
    await db.commit()
    await _broadcast_config(profile, user.id)
