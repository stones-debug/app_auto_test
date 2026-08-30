"""APP 档案业务用例、事务边界与 DTO 组装。"""

from __future__ import annotations

import builtins
from datetime import UTC, datetime
from typing import Any

from fastapi import status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import api_error
from app.models import AppProfile
from app.repositories.app_profiles import profiles as profiles_repo
from app.services.profile_audit import (
    find_idempotent_replay,
    find_project_idempotent_replay,
    write_audit,
)
from app.services.profile_revision import RevisionConflictError, bump_profile_revision


def to_dict(profile: AppProfile, counts: dict[str, Any] | None = None) -> dict:
    counts = counts or {"release_count": 0, "skip_counts": {"suite": 0, "case": 0, "step": 0, "assertion": 0}, "override_counts": {"element": 0, "variable": 0, "node": 0}}
    return {
        "id": profile.id, "project_id": profile.project_id, "name": profile.name,
        "code": profile.code, "description": profile.description, "status": profile.status,
        "inherit_all": profile.inherit_all, "revision": profile.revision,
        "release_count": counts["release_count"], "skip_counts": counts["skip_counts"],
        "override_counts": counts["override_counts"], "created_by": profile.created_by,
        "updated_by": profile.updated_by, "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


async def list(db: AsyncSession, *, project_id: int, include_disabled: bool, keyword: str) -> builtins.list[dict]:
    rows = await profiles_repo.list_for_project(db, project_id=project_id, include_disabled=include_disabled, keyword=keyword)
    counts = await profiles_repo.profile_counts(db, [row.id for row in rows])
    return [to_dict(row, counts[row.id]) for row in rows]


async def get(db: AsyncSession, profile_id: int) -> AppProfile:
    profile = await profiles_repo.get_by_id(db, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise api_error(status.HTTP_404_NOT_FOUND, "APP_PROFILE_NOT_FOUND", "APP 档案不存在")
    return profile


async def get_with_counts(db: AsyncSession, profile_id: int) -> dict:
    profile = await get(db, profile_id)
    counts = await profiles_repo.profile_counts(db, [profile.id])
    return to_dict(profile, counts[profile.id])


async def create(db: AsyncSession, *, project_id: int, body, user_id: int, role: str | None, audit: dict) -> dict:
    if body.request_id:
        replay = await find_project_idempotent_replay(db, project_id, body.request_id, action="profile_create")
        if replay is not None:
            return replay
    name_conflict, code_conflict = await profiles_repo.find_name_or_code_conflict(
        db, project_id=project_id, name=body.name, code=body.code
    )
    if name_conflict is not None:
        raise api_error(status.HTTP_409_CONFLICT, "APP_PROFILE_NAME_EXISTS", "同名 APP 档案已存在")
    if code_conflict is not None:
        raise api_error(status.HTTP_409_CONFLICT, "APP_PROFILE_CODE_EXISTS", "同编码 APP 档案已存在")
    try:
        profile = await profiles_repo.create(
            db, project_id=project_id, name=body.name, code=body.code,
            description=body.description, inherit_all=body.inherit_all, user_id=user_id,
        )
        response = {**to_dict(profile), "request_id": body.request_id}
        await write_audit(
            db, profile_id=profile.id, project_id=project_id, action="profile_create",
            actor_id=user_id, actor_role=role, revision_before=0, revision_after=1,
            request_id=body.request_id, response_data=jsonable_encoder(response), **audit,
        )
        await db.commit()
        await profiles_repo.refresh(db, profile)
        return response
    except Exception:
        await db.rollback()
        raise


async def update(db: AsyncSession, *, profile: AppProfile, body, user_id: int, role: str | None, audit: dict) -> dict:
    if body.request_id:
        replay = await find_idempotent_replay(db, profile.id, body.request_id)
        if replay is not None:
            return replay
    before = profile.revision
    try:
        new_revision = await bump_profile_revision(db, profile.id, body.expected_revision, user_id)
    except RevisionConflictError:
        raise
    profile.revision = new_revision
    await profiles_repo.update_fields(
        profile, fields=body.model_fields_set,
        values={field: getattr(body, field) for field in body.model_fields_set}, user_id=user_id,
    )
    await profiles_repo.refresh(db, profile)
    response = {**to_dict(profile), "request_id": body.request_id}
    await write_audit(
        db, profile_id=profile.id, project_id=profile.project_id, action="profile_update",
        actor_id=user_id, actor_role=role, revision_before=before, revision_after=new_revision,
        request_id=body.request_id, response_data=jsonable_encoder(response), **audit,
    )
    await db.commit()
    return response


async def delete(db: AsyncSession, *, profile: AppProfile, body, user_id: int, role: str | None, audit: dict) -> None:
    if body.request_id:
        replay = await find_idempotent_replay(db, profile.id, body.request_id)
        if replay is not None:
            return
    if await profiles_repo.has_active_execution(db, profile.id):
        raise api_error(status.HTTP_409_CONFLICT, "APP_PROFILE_HAS_ACTIVE_EXECUTIONS", "档案仍有非终态执行")
    before = profile.revision
    try:
        new_revision = await bump_profile_revision(db, profile.id, body.expected_revision, user_id)
        await profiles_repo.soft_delete(profile, datetime.now(UTC), user_id)
        await write_audit(
            db, profile_id=profile.id, project_id=profile.project_id, action="profile_disable",
            actor_id=user_id, actor_role=role, revision_before=before, revision_after=new_revision,
            request_id=body.request_id, **audit,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def bump_and_audit(
    db: AsyncSession, *, profile: AppProfile, body, action: str, user_id: int,
    role: str | None, audit: dict, changes: builtins.list | None = None, response_data: dict | None = None
) -> int:
    before = profile.revision
    try:
        new_revision = await bump_profile_revision(db, profile.id, body.expected_revision, user_id)
    except RevisionConflictError:
        raise
    profile.revision = new_revision
    profile.updated_by = user_id
    await write_audit(
        db, profile_id=profile.id, project_id=profile.project_id, action=action,
        actor_id=user_id, actor_role=role, revision_before=before, revision_after=new_revision,
        request_id=body.request_id, changes=changes or [],
        response_data=jsonable_encoder({"revision": new_revision, **(response_data or {})}), **audit,
    )
    return new_revision


async def commit_bump_and_audit(
    db: AsyncSession, *, profile: AppProfile, body, action: str, user_id: int,
    role: str | None, audit: dict, changes: builtins.list | None = None, response_data: dict | None = None
) -> int:
    """完成一次档案修改的 revision、审计和提交；失败统一回滚。"""
    try:
        revision = await bump_and_audit(
            db, profile=profile, body=body, action=action, user_id=user_id,
            role=role, audit=audit, changes=changes, response_data=response_data,
        )
        await db.commit()
        return revision
    except Exception:
        await db.rollback()
        raise
