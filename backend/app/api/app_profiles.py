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
    ElementOverrideDelete,
    ElementOverrideUpsert,
    NodeOverrideDelete,
    NodeOverridePatch,
    ReleaseCreate,
    ReleaseDelete,
    ReleaseUpdate,
    SkipBatchRequest,
    VariableOverrideDelete,
    VariableOverrideUpsert,
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


# ---------- 批量跳过/恢复（方案 §4.5） ----------


async def _validate_skip_target(db: AsyncSession, project_id: int, target, reason) -> tuple[dict, str | None]:
    """校验单个跳过目标；返回 (字段dict, 错误信息)。"""
    from app.models import TestCase, TestSuite

    if target.type == "suite":
        if target.suite_id is None:
            return {}, "套件目标必须提供 suite_id"
        s = await db.get(TestSuite, target.suite_id)
        if s is None or s.deleted_at is not None or s.project_id != project_id:
            return {}, "套件不存在或跨项目"
        return {"target_type": "suite", "suite_id": target.suite_id}, None
    if target.type == "case":
        if target.case_id is None:
            return {}, "用例目标必须提供 case_id"
        c = await db.get(TestCase, target.case_id)
        if c is None or c.deleted_at is not None or c.project_id != project_id:
            return {}, "用例不存在或跨项目"
        return {"target_type": "case", "case_id": target.case_id}, None
    # step / assertion
    if target.case_id is None or not target.node_key:
        return {}, "节点目标必须提供 case_id 与 node_key"
    c = await db.get(TestCase, target.case_id)
    if c is None or c.deleted_at is not None or c.project_id != project_id:
        return {}, "用例不存在或跨项目"
    return {"target_type": target.type, "case_id": target.case_id, "node_key": target.node_key}, None


@router.post("/app-profiles/{profile_id}/skip-rules/batch", response_model=dict)
async def skip_rules_batch(
    profile_id: int,
    body: SkipBatchRequest,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    before = profile.revision
    if body.request_id:
        replay = await find_idempotent_replay(db, profile_id, body.request_id)
        if replay is not None:
            return replay

    target_fields: list[dict] = []
    field_errors: list[dict] = []
    for idx, target in enumerate(body.targets):
        fields, err = await _validate_skip_target(db, profile.project_id, target, body.reason)
        if err:
            field_errors.append({"index": idx, "error": err})
            continue
        target_fields.append((idx, fields))
    if field_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PROFILE_RULE_INVALID", "field_errors": field_errors},
        )
    if body.operation == "skip" and body.reason is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PROFILE_RULE_INVALID", "message": "skip 操作必须提供 reason"},
        )

    from app.models import AppProfileSkipRule

    results: list[dict] = []
    changed = 0
    unchanged = 0
    new_rules: list[AppProfileSkipRule] = []
    try:
        new_revision = await bump_profile_revision(db, profile_id, body.expected_revision, user.id)
    except RevisionConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": err.code, "current": err.current, "expected": err.expected},
        ) from None

    for idx, fields in target_fields:
        existing_query = select(AppProfileSkipRule).where(
            AppProfileSkipRule.profile_id == profile_id,
            AppProfileSkipRule.deleted_at.is_(None),
        )
        existing_query = existing_query.where(
            AppProfileSkipRule.target_type == fields["target_type"]
        )
        if fields.get("suite_id") is not None:
            existing_query = existing_query.where(AppProfileSkipRule.suite_id == fields["suite_id"])
        if fields.get("case_id") is not None:
            existing_query = existing_query.where(AppProfileSkipRule.case_id == fields["case_id"])
        if fields.get("node_key") is not None:
            existing_query = existing_query.where(AppProfileSkipRule.node_key == fields["node_key"])
        existing = (await db.execute(existing_query)).scalar_one_or_none()
        if body.operation == "skip":
            if existing is not None:
                unchanged += 1
                results.append({"index": idx, "status": "unchanged", "rule_id": existing.id})
                continue
            rule = AppProfileSkipRule(
                profile_id=profile_id,
                target_type=fields["target_type"],
                suite_id=fields.get("suite_id"),
                case_id=fields.get("case_id"),
                node_key=fields.get("node_key"),
                reason_code=body.reason.code,
                reason_note=body.reason.note,
                created_by=user.id,
                updated_by=user.id,
            )
            db.add(rule)
            await db.flush()
            changed += 1
            results.append({"index": idx, "status": "changed", "rule_id": rule.id})
            new_rules.append(rule)
        else:  # restore
            if existing is None:
                unchanged += 1
                results.append({"index": idx, "status": "unchanged", "rule_id": None})
                continue
            existing.deleted_at = datetime.now(UTC)
            changed += 1
            results.append({"index": idx, "status": "changed", "rule_id": existing.id})

    action = "skip_batch" if body.operation == "skip" else "restore_batch"
    await write_audit(
        db,
        profile_id=profile_id,
        project_id=profile.project_id,
        action=action,
        actor_id=user.id,
        actor_role=role,
        revision_before=before,
        revision_after=new_revision,
        request_id=body.request_id,
        changes=results,
        response_data={"changed": changed, "unchanged": unchanged, "revision": new_revision},
    )
    await db.commit()
    return {
        "request_id": body.request_id,
        "revision_before": before,
        "revision_after": new_revision,
        "changed": changed,
        "unchanged": unchanged,
        "results": results,
    }


# ---------- 覆盖（方案 §4.6） ----------


async def _bump_and_audit(
    db, profile, body, action, user, role, changes=None, response_data=None
) -> dict:
    """递增 revision + 写审计，返回 (revision_after, 新值)。公共提取。"""
    before = profile.revision
    try:
        new_revision = await bump_profile_revision(db, profile.id, body.expected_revision, user.id)
    except RevisionConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": err.code, "current": err.current, "expected": err.expected},
        ) from None
    await write_audit(
        db,
        profile_id=profile.id,
        project_id=profile.project_id,
        action=action,
        actor_id=user.id,
        actor_role=role,
        revision_before=before,
        revision_after=new_revision,
        request_id=body.request_id,
        changes=changes or [],
        response_data=response_data or {},
    )
    profile.updated_by = user.id
    return new_revision


# 元素覆盖
@router.put("/app-profiles/{profile_id}/element-overrides/{element_id}", response_model=dict)
async def upsert_element_override(
    profile_id: int,
    element_id: int,
    body: ElementOverrideUpsert,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    from app.models import TestElement

    el = await db.get(TestElement, element_id)
    if el is None or el.deleted_at is not None or el.project_id != profile.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="元素不存在或跨项目")
    existing = (
        await db.execute(
            select(AppProfileElementOverride).where(
                AppProfileElementOverride.profile_id == profile_id,
                AppProfileElementOverride.element_id == element_id,
                AppProfileElementOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = AppProfileElementOverride(
            profile_id=profile_id,
            element_id=element_id,
            locator_type=body.locator_type,
            locator_value=body.locator_value,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(existing)
        await db.flush()
    else:
        existing.deleted_at = None
        existing.locator_type = body.locator_type
        existing.locator_value = body.locator_value
        existing.updated_by = user.id
    new_revision = await _bump_and_audit(db, profile, body, "element_override_upsert", user, role)
    await db.commit()
    return {"revision": new_revision, "element_id": element_id, "locator_type": body.locator_type, "locator_value": body.locator_value}


@router.delete("/app-profiles/{profile_id}/element-overrides/{element_id}", status_code=status.HTTP_204_NO_CONTENT)
async def restore_element_override(
    profile_id: int,
    element_id: int,
    body: ElementOverrideDelete,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    existing = (
        await db.execute(
            select(AppProfileElementOverride).where(
                AppProfileElementOverride.profile_id == profile_id,
                AppProfileElementOverride.element_id == element_id,
                AppProfileElementOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.deleted_at = datetime.now(UTC)
        existing.updated_by = user.id
    await _bump_and_audit(db, profile, body, "element_override_restore", user, role)
    await db.commit()


# 变量覆盖
@router.put("/app-profiles/{profile_id}/variable-overrides/{name}", response_model=dict)
async def upsert_variable_override(
    profile_id: int,
    name: str,
    body: VariableOverrideUpsert,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    existing = (
        await db.execute(
            select(AppProfileVariableOverride).where(
                AppProfileVariableOverride.profile_id == profile_id,
                AppProfileVariableOverride.name == name,
                AppProfileVariableOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = AppProfileVariableOverride(
            profile_id=profile_id, name=name, created_by=user.id, updated_by=user.id
        )
        db.add(existing)
        await db.flush()
    else:
        existing.deleted_at = None
    existing.value = body.value
    existing.description = body.description
    existing.updated_by = user.id
    new_revision = await _bump_and_audit(db, profile, body, "variable_override_upsert", user, role)
    await db.commit()
    return {"revision": new_revision, "name": name, "value": body.value}


@router.delete("/app-profiles/{profile_id}/variable-overrides/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def restore_variable_override(
    profile_id: int,
    name: str,
    body: VariableOverrideDelete,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    existing = (
        await db.execute(
            select(AppProfileVariableOverride).where(
                AppProfileVariableOverride.profile_id == profile_id,
                AppProfileVariableOverride.name == name,
                AppProfileVariableOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.deleted_at = datetime.now(UTC)
        existing.updated_by = user.id
    await _bump_and_audit(db, profile, body, "variable_override_restore", user, role)
    await db.commit()


# 节点参数覆盖
@router.put("/app-profiles/{profile_id}/node-overrides/{case_id}/{node_type}/{node_key}", response_model=dict)
async def upsert_node_override(
    profile_id: int,
    case_id: int,
    node_type: str,
    node_key: str,
    body: NodeOverridePatch,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    if node_type not in ("step", "assertion"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="node_type 只允许 step|assertion")
    from app.models import TestCase

    case = await db.get(TestCase, case_id)
    if case is None or case.deleted_at is not None or case.project_id != profile.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在或跨项目")
    # 白名单合并校验（禁止改身份/顺序）
    from app.services.profile_resolver import NODE_IDENTITY_FIELDS, NODE_PATCH_ALLOWED

    for key in body.patch:
        if key in NODE_IDENTITY_FIELDS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"禁止修改节点字段: {key}")
        if key not in NODE_PATCH_ALLOWED:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"不允许覆盖字段: {key}")
    existing = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.target_type == node_type,
                AppProfileNodeOverride.case_id == case_id,
                AppProfileNodeOverride.node_key == node_key,
                AppProfileNodeOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = AppProfileNodeOverride(
            profile_id=profile_id,
            target_type=node_type,
            case_id=case_id,
            node_key=node_key,
            patch=body.patch,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(existing)
        await db.flush()
    else:
        existing.deleted_at = None
        existing.patch = body.patch
        existing.updated_by = user.id
    new_revision = await _bump_and_audit(db, profile, body, "node_override_upsert", user, role)
    await db.commit()
    return {"revision": new_revision, "case_id": case_id, "node_key": node_key, "patch": body.patch}


@router.delete("/app-profiles/{profile_id}/node-overrides/{case_id}/{node_type}/{node_key}", status_code=status.HTTP_204_NO_CONTENT)
async def restore_node_override(
    profile_id: int,
    case_id: int,
    node_type: str,
    node_key: str,
    body: NodeOverrideDelete,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    existing = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.target_type == node_type,
                AppProfileNodeOverride.case_id == case_id,
                AppProfileNodeOverride.node_key == node_key,
                AppProfileNodeOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.deleted_at = datetime.now(UTC)
        existing.updated_by = user.id
    await _bump_and_audit(db, profile, body, "node_override_restore", user, role)
    await db.commit()
