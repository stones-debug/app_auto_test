"""APP 档案发布版本业务用例与事务边界。"""

from datetime import UTC, datetime

from fastapi import status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, api_error
from app.repositories.app_profiles import releases as releases_repo
from app.services.profile_audit import find_idempotent_replay, write_audit


def to_dict(release) -> dict:
    return {
        "id": release.id, "profile_id": release.profile_id, "version": release.version,
        "build_number": release.build_number, "description": release.description,
        "status": release.status, "created_by": release.created_by,
        "created_at": release.created_at, "updated_at": release.updated_at,
    }


async def get(db: AsyncSession, release_id: int):
    release = await releases_repo.get_by_id(db, release_id)
    if release is None or release.deleted_at is not None:
        raise api_error(404, "APP_RELEASE_NOT_FOUND", "发布版本不存在")
    return release


async def list(db: AsyncSession, *, profile_id: int, status_: str, offset: int, limit: int) -> dict:
    total, items = await releases_repo.list_for_profile(db, profile_id=profile_id, status=status_, offset=offset, limit=limit)
    return {"total": total, "items": [to_dict(item) for item in items]}


async def create(db: AsyncSession, *, profile, body, user_id: int, role: str | None, audit: dict) -> dict:
    if body.request_id:
        replay = await find_idempotent_replay(db, profile.id, body.request_id, actor_id=user_id)
        if replay is not None:
            return replay
    if await releases_repo.find_version_conflict(db, profile_id=profile.id, version=body.version, build_number=body.build_number):
        raise api_error(status.HTTP_409_CONFLICT, ErrorCode.APP_RELEASE_EXISTS, "同版本号发布版本已存在")
    try:
        release = await releases_repo.create(db, profile_id=profile.id, version=body.version, build_number=body.build_number, description=body.description, user_id=user_id)
        response = {**to_dict(release), "request_id": body.request_id}
        await write_audit(db, profile_id=profile.id, project_id=profile.project_id, action="release_create", actor_id=user_id, actor_role=role, revision_before=profile.revision, revision_after=profile.revision, request_id=body.request_id, response_data=jsonable_encoder(response), **audit)
        await db.commit()
        await releases_repo.refresh(db, release)
        return response
    except Exception:
        await db.rollback()
        raise


async def update(db: AsyncSession, *, release, profile, body, user_id: int, role: str | None, audit: dict) -> dict:
    if body.request_id:
        replay = await find_idempotent_replay(db, profile.id, body.request_id, actor_id=user_id)
        if replay is not None:
            return replay
    try:
        await releases_repo.update_fields(release, fields=body.model_fields_set, values={field: getattr(body, field) for field in body.model_fields_set}, user_id=user_id)
        await releases_repo.refresh(db, release)
        response = {**to_dict(release), "request_id": body.request_id}
        await write_audit(db, profile_id=profile.id, project_id=profile.project_id, action="release_update", actor_id=user_id, actor_role=role, revision_before=profile.revision, revision_after=profile.revision, request_id=body.request_id, response_data=jsonable_encoder(response), **audit)
        await db.commit()
        return response
    except Exception as exc:
        await db.rollback()
        if exc.__class__.__name__ == "IntegrityError":
            raise api_error(status.HTTP_409_CONFLICT, ErrorCode.APP_RELEASE_EXISTS, "同版本号发布版本已存在") from None
        raise


async def delete(db: AsyncSession, *, release, profile, body, user_id: int, role: str | None, audit: dict) -> None:
    if body.request_id:
        replay = await find_idempotent_replay(db, profile.id, body.request_id, actor_id=user_id)
        if replay is not None:
            return
    try:
        await releases_repo.soft_delete(release, datetime.now(UTC), user_id)
        await write_audit(db, profile_id=profile.id, project_id=profile.project_id, action="release_disable", actor_id=user_id, actor_role=role, revision_before=profile.revision, revision_after=profile.revision, request_id=body.request_id, **audit)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
