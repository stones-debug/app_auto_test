"""APP 档案与发布版本接口（方案 §4.2/§4.3）。

安全要求：所有按 profile_id 访问的接口反查 project_id 并调用项目权限依赖。
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    TestCase,
    TestSuite,
    TestSuiteCase,
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
from app.services.profile_audit import (
    find_idempotent_replay,
    find_project_idempotent_replay,
    write_audit,
)
from app.services.profile_revision import RevisionConflictError, bump_profile_revision
from app.ws.managers import profile_config_manager

router = APIRouter(tags=["APP 档案"])


def _audit_client(request: Request) -> dict[str, str | None]:
    return {
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


async def _broadcast_config(profile: AppProfile, user_id: int | None = None) -> None:
    """方案 §4.9：广播档案配置变更（提示刷新，非一致性来源）。"""
    await profile_config_manager.broadcast(
        profile.project_id,
        {
            "type": "profile_revision_changed",
            "project_id": profile.project_id,
            "profile_id": profile.id,
            "profile_revision": profile.revision,
            "changed_by": user_id,
            "changed_at": datetime.now(UTC).isoformat(),
        },
    )


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
    return (await _profiles_out([profile], db))[0]


async def _profiles_out(profiles: list[AppProfile], db: AsyncSession) -> list[dict]:
    """固定查询数批量聚合列表计数，避免每个档案执行五次统计查询。"""
    if not profiles:
        return []
    profile_ids = [profile.id for profile in profiles]
    release_counts = dict(
        (
            await db.execute(
                select(AppProfileRelease.profile_id, func.count())
                .where(
                    AppProfileRelease.profile_id.in_(profile_ids),
                    AppProfileRelease.deleted_at.is_(None),
                )
                .group_by(AppProfileRelease.profile_id)
            )
        ).all()
    )
    skip_rows = (
        await db.execute(
            select(AppProfileSkipRule.profile_id, AppProfileSkipRule.target_type, func.count())
            .where(
                AppProfileSkipRule.profile_id.in_(profile_ids),
                AppProfileSkipRule.deleted_at.is_(None),
            )
            .group_by(AppProfileSkipRule.profile_id, AppProfileSkipRule.target_type)
        )
    ).all()
    skip_by_profile: dict[int, dict[str, int]] = {
        profile_id: {"suite": 0, "case": 0, "step": 0, "assertion": 0}
        for profile_id in profile_ids
    }
    for profile_id, target_type, count in skip_rows:
        skip_by_profile[profile_id][target_type] = count

    async def grouped_count(model) -> dict[int, int]:
        return dict(
            (
                await db.execute(
                    select(model.profile_id, func.count())
                    .where(model.profile_id.in_(profile_ids), model.deleted_at.is_(None))
                    .group_by(model.profile_id)
                )
            ).all()
        )

    element_counts = await grouped_count(AppProfileElementOverride)
    variable_counts = await grouped_count(AppProfileVariableOverride)
    node_counts = await grouped_count(AppProfileNodeOverride)
    return [
        {
            "id": profile.id,
            "project_id": profile.project_id,
            "name": profile.name,
            "code": profile.code,
            "description": profile.description,
            "status": profile.status,
            "inherit_all": profile.inherit_all,
            "revision": profile.revision,
            "release_count": release_counts.get(profile.id, 0),
            "skip_counts": skip_by_profile[profile.id],
            "override_counts": {
                "element": element_counts.get(profile.id, 0),
                "variable": variable_counts.get(profile.id, 0),
                "node": node_counts.get(profile.id, 0),
            },
            "created_by": profile.created_by,
            "updated_by": profile.updated_by,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        }
        for profile in profiles
    ]


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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同名 APP 档案已存在")
    duplicate_code = await db.scalar(
        select(AppProfile.id).where(
            AppProfile.project_id == project_id,
            AppProfile.code == body.code,
            AppProfile.deleted_at.is_(None),
        )
    )
    if duplicate_code is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同编码 APP 档案已存在")
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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="APP 档案名称或编码已存在") from None
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": err.code, "current": err.current, "expected": err.expected},
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
        **_audit_client(request),
    )
    await db.commit()
    await _broadcast_config(profile, user.id)


# ---------- 发布版本 ----------


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


# ---------- 批量跳过/恢复（方案 §4.5） ----------


def _find_case_node(case: TestCase, node_type: str, node_key: str) -> tuple[str, dict] | None:
    try:
        normalized_key = str(UUID(str(node_key)))
    except ValueError:
        return None
    collection = case.steps if node_type == "step" else case.assertions
    for node in collection or []:
        if isinstance(node, dict) and str(node.get("key") or "") == normalized_key:
            return normalized_key, node
    return None


def _find_suite_step(suite: TestSuite, node_key: str) -> tuple[str, dict, str] | None:
    """在套件 setup/teardown 步骤中定位节点，返回 (normalized_key, node, phase)。

    phase 为执行语义的 suite_setup / suite_teardown（不取节点自身的 setup/main/teardown）。
    """
    try:
        normalized_key = str(UUID(str(node_key)))
    except ValueError:
        return None
    for phase, collection in (("suite_setup", suite.setup_steps or []), ("suite_teardown", suite.teardown_steps or [])):
        for node in collection or []:
            if isinstance(node, dict) and str(node.get("key") or "") == normalized_key:
                return normalized_key, node, phase
    return None


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
        if target.suite_id is None or target.case_id is None:
            return {}, "用例目标必须提供 suite_id 与 case_id"
        suite = await db.get(TestSuite, target.suite_id)
        if suite is None or suite.deleted_at is not None or suite.project_id != project_id:
            return {}, "套件不存在或跨项目"
        c = await db.get(TestCase, target.case_id)
        if c is None or c.deleted_at is not None or c.project_id != project_id:
            return {}, "用例不存在或跨项目"
        membership = await db.scalar(
            select(TestSuiteCase.id).where(
                TestSuiteCase.suite_id == target.suite_id,
                TestSuiteCase.case_id == target.case_id,
            )
        )
        if membership is None:
            return {}, "套件用例关系不存在"
        return {
            "target_type": "case",
            "suite_id": target.suite_id,
            "case_id": target.case_id,
        }, None
    if target.type == "suite_step":
        if target.suite_id is None or not target.node_key:
            return {}, "套件步骤目标必须提供 suite_id 与 node_key"
        if target.case_id is not None:
            return {}, "套件步骤目标不允许提供 case_id"
        suite = await db.get(TestSuite, target.suite_id)
        if suite is None or suite.deleted_at is not None or suite.project_id != project_id:
            return {}, "套件不存在或跨项目"
        found = _find_suite_step(suite, target.node_key)
        if found is None:
            return {}, "套件步骤节点不存在或 node_key 非法"
        normalized_key, _node, _phase = found
        return {
            "target_type": "suite_step",
            "suite_id": target.suite_id,
            "node_key": normalized_key,
        }, None
    # step / assertion
    if target.suite_id is None or target.case_id is None or not target.node_key:
        return {}, "节点目标必须提供 suite_id、case_id 与 node_key"
    suite = await db.get(TestSuite, target.suite_id)
    if suite is None or suite.deleted_at is not None or suite.project_id != project_id:
        return {}, "套件不存在或跨项目"
    c = await db.get(TestCase, target.case_id)
    if c is None or c.deleted_at is not None or c.project_id != project_id:
        return {}, "用例不存在或跨项目"
    membership = await db.scalar(
        select(TestSuiteCase.id).where(
            TestSuiteCase.suite_id == target.suite_id,
            TestSuiteCase.case_id == target.case_id,
        )
    )
    if membership is None:
        return {}, "套件用例关系不存在"
    found = _find_case_node(c, target.type, target.node_key)
    if found is None:
        return {}, f"{target.type} 节点不存在或 node_key 非法"
    normalized_key, _node = found
    return {
        "target_type": target.type,
        "suite_id": target.suite_id,
        "case_id": target.case_id,
        "node_key": normalized_key,
    }, None


@router.post("/app-profiles/{profile_id}/skip-rules/batch", response_model=dict)
async def skip_rules_batch(
    profile_id: int,
    body: SkipBatchRequest,
    request: Request,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    before = profile.revision
    if len(body.targets) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "PROFILE_RULE_INVALID", "message": "单次批量跳过目标上限 500"},
        )
    if body.request_id:
        replay = await find_idempotent_replay(db, profile_id, body.request_id)
        if replay is not None:
            return replay

    target_fields: list[dict] = []
    field_errors: list[dict] = []
    seen: set[tuple] = set()
    for idx, target in enumerate(body.targets):
        fields, err = await _validate_skip_target(db, profile.project_id, target, body.reason)
        if err:
            field_errors.append({"index": idx, "error": err})
            continue
        dedup_key = (
            fields["target_type"],
            fields.get("suite_id"),
            fields.get("case_id"),
            fields.get("node_key"),
        )
        if dedup_key in seen:
            field_errors.append({"index": idx, "error": "同一目标在批量中重复"})
            continue
        seen.add(dedup_key)
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
        for field, column in (
            ("suite_id", AppProfileSkipRule.suite_id),
            ("case_id", AppProfileSkipRule.case_id),
            ("node_key", AppProfileSkipRule.node_key),
        ):
            value = fields.get(field)
            existing_query = existing_query.where(
                column.is_(None) if value is None else column == value
            )
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
        **_audit_client(request),
    )
    await db.commit()
    await _broadcast_config(profile, user.id)
    return {
        "request_id": body.request_id,
        "revision_before": before,
        "revision_after": new_revision,
        "changed": changed,
        "unchanged": unchanged,
        "results": results,
    }


# ---------- 覆盖（方案 §4.6） ----------


@router.get("/app-profiles/{profile_id}/overrides", response_model=dict)
async def list_profile_overrides(
    profile_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前有效覆盖值，供工作台编辑器回显。"""
    profile = await _get_profile_or_404(profile_id, db)
    await get_project_permission(profile.project_id, user, db)
    element_rows = (
        await db.execute(
            select(AppProfileElementOverride).where(
                AppProfileElementOverride.profile_id == profile_id,
                AppProfileElementOverride.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    variable_rows = (
        await db.execute(
            select(AppProfileVariableOverride).where(
                AppProfileVariableOverride.profile_id == profile_id,
                AppProfileVariableOverride.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    node_rows = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    return {
        "revision": profile.revision,
        "elements": [
            {
                "element_id": row.element_id,
                "locator_type": row.locator_type,
                "locator_value": row.locator_value,
            }
            for row in element_rows
        ],
        "variables": [
            {"name": row.name, "value": row.value, "description": row.description}
            for row in variable_rows
        ],
        "nodes": [
            {
                "suite_id": row.suite_id,
                "case_id": row.case_id,
                "node_type": row.target_type,
                "node_key": str(row.node_key),
                "patch": row.patch,
            }
            for row in node_rows
        ],
    }


async def _bump_and_audit(
    db, profile, body, action, user, role, request, changes=None, response_data=None
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
    audit_response = {"revision": new_revision, **(response_data or {})}
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
        response_data=jsonable_encoder(audit_response),
        **_audit_client(request),
    )
    profile.updated_by = user.id
    return new_revision


# 元素覆盖
@router.put("/app-profiles/{profile_id}/element-overrides/{element_id}", response_model=dict)
async def upsert_element_override(
    profile_id: int,
    element_id: int,
    body: ElementOverrideUpsert,
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
    response_data = {
        "element_id": element_id,
        "locator_type": body.locator_type,
        "locator_value": body.locator_value,
    }
    new_revision = await _bump_and_audit(
        db, profile, body, "element_override_upsert", user, role, request, response_data=response_data
    )
    await db.commit()
    return {"revision": new_revision, **response_data}


@router.delete("/app-profiles/{profile_id}/element-overrides/{element_id}", status_code=status.HTTP_204_NO_CONTENT)
async def restore_element_override(
    profile_id: int,
    element_id: int,
    body: ElementOverrideDelete,
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
        return
    existing.deleted_at = datetime.now(UTC)
    existing.updated_by = user.id
    await _bump_and_audit(db, profile, body, "element_override_restore", user, role, request)
    await db.commit()


# 变量覆盖
@router.put("/app-profiles/{profile_id}/variable-overrides/{name}", response_model=dict)
async def upsert_variable_override(
    profile_id: int,
    name: str,
    body: VariableOverrideUpsert,
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
    response_data = {"name": name, "value": body.value, "description": body.description}
    new_revision = await _bump_and_audit(
        db, profile, body, "variable_override_upsert", user, role, request, response_data=response_data
    )
    await db.commit()
    return {"revision": new_revision, **response_data}


@router.delete("/app-profiles/{profile_id}/variable-overrides/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def restore_variable_override(
    profile_id: int,
    name: str,
    body: VariableOverrideDelete,
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
        return
    existing.deleted_at = datetime.now(UTC)
    existing.updated_by = user.id
    await _bump_and_audit(db, profile, body, "variable_override_restore", user, role, request)
    await db.commit()


# 节点参数覆盖
@router.put("/app-profiles/{profile_id}/node-overrides/{suite_id}/{case_id}/{node_type}/{node_key}", response_model=dict)
async def upsert_node_override(
    profile_id: int,
    suite_id: int,
    case_id: int,
    node_type: str,
    node_key: str,
    body: NodeOverridePatch,
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
    if node_type not in ("step", "assertion"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="node_type 只允许 step|assertion")
    from app.models import TestCase

    case = await db.get(TestCase, case_id)
    if case is None or case.deleted_at is not None or case.project_id != profile.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在或跨项目")
    membership = await db.scalar(
        select(TestSuiteCase.id).where(
            TestSuiteCase.suite_id == suite_id,
            TestSuiteCase.case_id == case_id,
        )
    )
    suite = await db.get(TestSuite, suite_id)
    if (
        suite is None
        or suite.deleted_at is not None
        or suite.project_id != profile.project_id
        or membership is None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件用例关系不存在")
    found = _find_case_node(case, node_type, node_key)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "PROFILE_TARGET_NOT_FOUND", "message": "节点不存在或 node_key 非法"},
        )
    normalized_key, source_node = found
    # 白名单合并校验（禁止改身份/顺序）
    from app.services.profile_resolver import (
        NODE_IDENTITY_FIELDS,
        NODE_PATCH_ALLOWED,
        ProfileRuleError,
        validate_node_patch,
    )

    for key in body.patch:
        if key in NODE_IDENTITY_FIELDS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"禁止修改节点字段: {key}")
        if key not in NODE_PATCH_ALLOWED:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"不允许覆盖字段: {key}")
    try:
        validate_node_patch(node_type, source_node, body.patch)
    except ProfileRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": exc.message},
        ) from None
    existing = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.suite_id == suite_id,
                AppProfileNodeOverride.target_type == node_type,
                AppProfileNodeOverride.case_id == case_id,
                AppProfileNodeOverride.node_key == normalized_key,
                AppProfileNodeOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = AppProfileNodeOverride(
            profile_id=profile_id,
            suite_id=suite_id,
            target_type=node_type,
            case_id=case_id,
            node_key=normalized_key,
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
    response_data = {
        "suite_id": suite_id,
        "case_id": case_id,
        "node_type": node_type,
        "node_key": normalized_key,
        "patch": body.patch,
    }
    new_revision = await _bump_and_audit(
        db, profile, body, "node_override_upsert", user, role, request, response_data=response_data
    )
    await db.commit()
    return {"revision": new_revision, **response_data}


@router.delete("/app-profiles/{profile_id}/node-overrides/{suite_id}/{case_id}/{node_type}/{node_key}", status_code=status.HTTP_204_NO_CONTENT)
async def restore_node_override(
    profile_id: int,
    suite_id: int,
    case_id: int,
    node_type: str,
    node_key: str,
    body: NodeOverrideDelete,
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
    try:
        normalized_key = str(UUID(node_key))
    except ValueError:
        return
    existing = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.suite_id == suite_id,
                AppProfileNodeOverride.target_type == node_type,
                AppProfileNodeOverride.case_id == case_id,
                AppProfileNodeOverride.node_key == normalized_key,
                AppProfileNodeOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return
    existing.deleted_at = datetime.now(UTC)
    existing.updated_by = user.id
    await _bump_and_audit(db, profile, body, "node_override_restore", user, role, request)
    await db.commit()


@router.put("/app-profiles/{profile_id}/suite-step-overrides/{suite_id}/{node_key}", response_model=dict)
async def upsert_suite_step_override(
    profile_id: int,
    suite_id: int,
    node_key: str,
    body: NodeOverridePatch,
    request: Request,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """套件前后置步骤节点覆盖（case_id 为空）。复用白名单 + Registry 校验。"""
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    if body.request_id:
        replay = await find_idempotent_replay(db, profile_id, body.request_id)
        if replay is not None:
            return replay
    suite = await db.get(TestSuite, suite_id)
    if suite is None or suite.deleted_at is not None or suite.project_id != profile.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在或跨项目")
    found = _find_suite_step(suite, node_key)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "PROFILE_TARGET_NOT_FOUND", "message": "套件步骤节点不存在或 node_key 非法"},
        )
    normalized_key, source_node, _phase = found
    # 白名单合并校验（禁止改身份/顺序）；套件步骤是 Action Step，验证逻辑与 step 一致
    from app.services.profile_resolver import (
        NODE_IDENTITY_FIELDS,
        NODE_PATCH_ALLOWED,
        ProfileRuleError,
        validate_node_patch,
    )

    for key in body.patch:
        if key in NODE_IDENTITY_FIELDS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"禁止修改节点字段: {key}")
        if key not in NODE_PATCH_ALLOWED:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"不允许覆盖字段: {key}")
    try:
        validate_node_patch("step", source_node, body.patch)
    except ProfileRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": exc.message},
        ) from None
    existing = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.target_type == "suite_step",
                AppProfileNodeOverride.suite_id == suite_id,
                AppProfileNodeOverride.node_key == normalized_key,
                AppProfileNodeOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = AppProfileNodeOverride(
            profile_id=profile_id,
            target_type="suite_step",
            suite_id=suite_id,
            case_id=None,
            node_key=normalized_key,
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
    response_data = {
        "suite_id": suite_id,
        "node_type": "suite_step",
        "node_key": normalized_key,
        "patch": body.patch,
    }
    new_revision = await _bump_and_audit(
        db, profile, body, "node_override_upsert", user, role, request, response_data=response_data
    )
    await db.commit()
    return {"revision": new_revision, **response_data}


@router.delete("/app-profiles/{profile_id}/suite-step-overrides/{suite_id}/{node_key}", status_code=status.HTTP_204_NO_CONTENT)
async def restore_suite_step_override(
    profile_id: int,
    suite_id: int,
    node_key: str,
    body: NodeOverrideDelete,
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
    try:
        normalized_key = str(UUID(node_key))
    except ValueError:
        return
    existing = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.target_type == "suite_step",
                AppProfileNodeOverride.suite_id == suite_id,
                AppProfileNodeOverride.node_key == normalized_key,
                AppProfileNodeOverride.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return
    existing.deleted_at = datetime.now(UTC)
    existing.updated_by = user.id
    await _bump_and_audit(db, profile, body, "node_override_restore", user, role, request)
    await db.commit()


# ---------- 配置工作台（方案 §4.4） ----------


@router.get("/app-profiles/{profile_id}/workspace")
async def workspace(
    profile_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    keyword: str = "",
    effective_status: str = "all",
    reason_code: str = "",
    sort_by: str = "name",
    sort_order: str = "asc",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    await get_project_permission(profile.project_id, user, db)
    project = await db.get(Project, profile.project_id)

    suites = (
        await db.execute(
            select(TestSuite).where(
                TestSuite.project_id == profile.project_id, TestSuite.deleted_at.is_(None)
            ).order_by(TestSuite.name)
        )
    ).scalars().all()
    skip = await _load_skip_index(db, profile_id)
    case_counts = await _case_counts(db, profile.project_id)
    diff_counts = await _diff_counts(db, profile.project_id, profile_id)
    override_counts = await _override_counts(db, profile.project_id, profile_id)

    items: list[dict] = []
    for s in suites:
        rule = skip["suite"].get(s.id)
        override_count = override_counts.get(s.id, 0)
        effective = "skipped" if rule else ("overridden" if override_count else "enabled")
        status_source = "direct" if rule else ("override" if override_count else "none")
        reason = None
        if rule:
            reason = {"code": rule.reason_code, "note": rule.reason_note or ""}
        if effective_status != "all" and effective != effective_status:
            continue
        if reason_code and (rule is None or rule.reason_code != reason_code):
            continue
        if keyword and keyword.lower() not in s.name.lower():
            continue
        items.append(
            {
                "node_type": "suite",
                "id": s.id,
                "name": s.name,
                "effective_status": effective,
                "status_source": status_source,
                "reason": reason,
                "override_count": override_count,
                "child_count": case_counts.get(s.id, 0),
                "difference_count": diff_counts.get(s.id, 0) + override_count,
                "has_children": (
                    case_counts.get(s.id, 0) > 0
                    or (s.setup_steps or []) != []
                    or (s.teardown_steps or []) != []
                ),
                "setup_step_count": len(s.setup_steps or []),
                "teardown_step_count": len(s.teardown_steps or []),
                "updated_at": s.updated_at,
            }
        )
    items = _sort_workspace(items, sort_by, sort_order)
    total = len(items)
    start = (page - 1) * page_size
    return {
        "profile_revision": profile.revision,
        "test_asset_revision": project.test_asset_revision,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items[start : start + page_size],
    }


@router.get("/app-profiles/{profile_id}/workspace/nodes")
async def workspace_nodes(
    profile_id: int,
    parent_type: str,
    parent_id: int,
    ancestor_suite_id: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    include: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    await get_project_permission(profile.project_id, user, db)
    skip = await _load_skip_index(db, profile_id)
    overrides = await _load_override_index(db, profile_id)
    if parent_type == "suite":
        suite = await db.get(TestSuite, parent_id)
        if suite is None or suite.deleted_at is not None or suite.project_id != profile.project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
        rows = await _suite_cases(db, parent_id)
        suite_rule = skip["suite"].get(parent_id)
        items = []
        for case in rows:
            direct_rule = skip["case"].get((parent_id, case.id))
            rule = suite_rule or direct_rule
            override_count = len(overrides["node"].get((parent_id, case.id), {}))
            effective = "skipped" if rule else ("overridden" if override_count else "enabled")
            source = "inherited" if suite_rule else ("direct" if direct_rule else ("override" if override_count else "none"))
            reason = None
            if rule:
                reason = {"code": rule.reason_code, "note": rule.reason_note or ""}
            items.append(
                {"node_type": "case", "id": case.id, "name": case.name, "effective_status": effective, "status_source": source, "reason": reason, "has_children": True, "override_count": override_count}
            )
        start = (page - 1) * page_size
        return {"total": len(items), "page": page, "page_size": page_size, "items": items[start : start + page_size]}
    if parent_type == "case":
        case = await db.get(TestCase, parent_id)
        if case is None or case.deleted_at is not None or case.project_id != profile.project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
        suite_rule = None
        if ancestor_suite_id is not None:
            suite = await db.get(TestSuite, ancestor_suite_id)
            membership = await db.scalar(
                select(TestSuiteCase.id).where(
                    TestSuiteCase.suite_id == ancestor_suite_id,
                    TestSuiteCase.case_id == case.id,
                )
            )
            if (
                suite is None
                or suite.deleted_at is not None
                or suite.project_id != profile.project_id
                or membership is None
            ):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件用例关系不存在")
            suite_rule = skip["suite"].get(ancestor_suite_id)
        case_rule = suite_rule or (
            skip["case"].get((ancestor_suite_id, case.id))
            if ancestor_suite_id is not None
            else None
        )
        items = []
        for node in (case.steps or []):
            node_key = str(node.get("key") or "")
            rule = skip["step"].get((ancestor_suite_id, case.id), {}).get(node_key)
            overridden = overrides["node"].get((ancestor_suite_id, case.id), {}).get(node_key) == "step"
            items.append(_node_item("step", case.id, node_key, node, rule, case_rule, overridden))
        for node in (case.assertions or []):
            node_key = str(node.get("key") or "")
            rule = skip["assertion"].get((ancestor_suite_id, case.id), {}).get(node_key)
            overridden = overrides["node"].get((ancestor_suite_id, case.id), {}).get(node_key) == "assertion"
            items.append(_node_item("assertion", case.id, node_key, node, rule, case_rule, overridden))
        start = (page - 1) * page_size
        return {"total": len(items), "page": page, "page_size": page_size, "items": items[start : start + page_size]}
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="parent_type 必须是 suite 或 case")


@router.get("/app-profiles/{profile_id}/suite-steps/{suite_id}")
async def hub_suite_steps(
    profile_id: int,
    suite_id: int,
    phase: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """套件前后置步骤节点列表（node_type='suite_step'），供工作台树状展开。

    用法 `?phase=suite_setup|suite_teardown` 单独取前置/后置；缺省返回全部。
    """
    if phase is not None and phase not in ("suite_setup", "suite_teardown"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="phase 只允许 suite_setup 或 suite_teardown",
        )
    profile = await _get_profile_or_404(profile_id, db)
    await get_project_permission(profile.project_id, user, db)
    suite = await db.get(TestSuite, suite_id)
    if suite is None or suite.deleted_at is not None or suite.project_id != profile.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
    skip = await _load_skip_index(db, profile_id)
    overrides = await _load_override_index(db, profile_id)

    items: list[dict] = []
    for phase_name, collection in (("suite_setup", suite.setup_steps or []), ("suite_teardown", suite.teardown_steps or [])):
        if phase and phase != phase_name:
            continue
        for node in collection:
            if not isinstance(node, dict):
                continue
            node_key = str(node.get("key") or "")
            rule = skip["suite_step"].get((suite_id, node_key))
            overridden = (suite_id, node_key) in overrides["suite_step"]
            items.append(_suite_step_item(suite_id, node_key, node, phase_name, rule, overridden))
    start = (page - 1) * page_size
    return {
        "profile_revision": profile.revision,
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "items": items[start : start + page_size],
    }


@router.get("/app-profiles/{profile_id}/differences")
async def differences(
    profile_id: int,
    type_: str = Query(default="all", alias="type"),
    target_type: str = "",
    reason_code: str = "",
    keyword: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    await get_project_permission(profile.project_id, user, db)
    skip = await _load_skip_index(db, profile_id)
    overrides = await _load_override_index(db, profile_id)
    rows: list[dict] = []

    # 收集出现的套件/用例 ID，一次性查名称（差异清单展示名称而非 ID）
    suite_ids: set[int] = set()
    case_ids: set[int] = set()
    step_suite_ids: set[int] = set()
    for sid in skip["suite"]:
        suite_ids.add(sid)
    for (sid, cid) in skip["case"]:
        suite_ids.add(sid)
        case_ids.add(cid)
    for (sid, cid) in skip["step"]:
        suite_ids.add(sid)
        case_ids.add(cid)
    for (sid, cid) in skip["assertion"]:
        suite_ids.add(sid)
        case_ids.add(cid)
    for sid, _nk in skip["suite_step"]:
        suite_ids.add(sid)
        step_suite_ids.add(sid)
    for sid, cid in overrides["node"]:
        suite_ids.add(sid)
        case_ids.add(cid)
    for sid, _nk in overrides["suite_step"]:
        suite_ids.add(sid)
        step_suite_ids.add(sid)
    suite_names: dict[int, str] = (
        {s.id: s.name for s in (await db.execute(select(TestSuite).where(TestSuite.id.in_(suite_ids)))).scalars()} if suite_ids else {}
    )
    case_names: dict[int, str] = (
        {c.id: c.name for c in (await db.execute(select(TestCase).where(TestCase.id.in_(case_ids)))).scalars()} if case_ids else {}
    )
    step_suites: dict[int, TestSuite] = (
        {s.id: s for s in (await db.execute(select(TestSuite).where(TestSuite.id.in_(step_suite_ids)))).scalars()} if step_suite_ids else {}
    )

    def _n(sid: int | None) -> str:
        return suite_names.get(sid, f"套件 {sid}") if sid is not None else "?"

    def _c(cid: int | None) -> str:
        return case_names.get(cid, f"用例 {cid}") if cid is not None else "?"

    def _suite_step_phase(sid: int, node_key: str) -> str:
        suite = step_suites.get(sid)
        if suite is not None:
            for node in suite.setup_steps or []:
                if isinstance(node, dict) and str(node.get("key") or "") == node_key:
                    return "suite_setup"
            for node in suite.teardown_steps or []:
                if isinstance(node, dict) and str(node.get("key") or "") == node_key:
                    return "suite_teardown"
        return "suite_setup"

    # 跳过项
    for sid, rule in skip["suite"].items():
        rows.append(_skip_row("suite", _n(sid), rule, "direct", suite_id=sid))
    for (sid, cid), rule in skip["case"].items():
        rows.append(_skip_row("case", f"{_n(sid)} / {_c(cid)}", rule, "direct", suite_id=sid, case_id=cid))
    for (sid, cid), rules in skip["step"].items():
        for rule in rules.values():
            rows.append(_skip_row("step", f"{_n(sid)} / {_c(cid)} / 步骤", rule, "direct", suite_id=sid, case_id=cid))
    for (sid, cid), rules in skip["assertion"].items():
        for rule in rules.values():
            rows.append(_skip_row("assertion", f"{_n(sid)} / {_c(cid)} / 断言", rule, "direct", suite_id=sid, case_id=cid))
    for (sid, nk), rule in skip["suite_step"].items():
        label = "前置" if _suite_step_phase(sid, nk) == "suite_setup" else "后置"
        rows.append(_skip_row("suite_step", f"{_n(sid)} / {label} / 步骤", rule, "direct", suite_id=sid))

    # 覆盖项
    if type_ in ("all", "overridden"):
        for (sid, cid), rules in overrides["node"].items():
            for _k in rules:
                rows.append(
                    {
                        "target_type": "node",
                        "path": f"{_n(sid)} / {_c(cid)} / 节点",
                        "override": True,
                        "suite_id": sid,
                        "case_id": cid,
                    }
                )
        for (sid, nk), _patch in overrides["suite_step"].items():
            label = "前置" if _suite_step_phase(sid, nk) == "suite_setup" else "后置"
            rows.append(
                {
                    "target_type": "suite_step",
                    "path": f"{_n(sid)} / {label} / 步骤",
                    "override": True,
                    "suite_id": sid,
                }
            )
        for el_id in overrides["element"]:
            rows.append({"target_type": "element", "path": f"元素 {el_id}", "override": True})
        for name in overrides["variable"]:
            rows.append({"target_type": "variable", "path": f"变量 {name}", "override": True})

    if type_ == "skipped":
        rows = [r for r in rows if not r.get("override")]
    elif type_ == "overridden":
        rows = [r for r in rows if r.get("override")]
    if target_type:
        rows = [r for r in rows if r["target_type"] == target_type]
    if reason_code:
        rows = [r for r in rows if r.get("reason_code") == reason_code]
    if keyword:
        rows = [r for r in rows if keyword.lower() in r["path"].lower()]
    total = len(rows)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size, "items": rows[start : start + page_size]}


# ---------- 工作台辅助 ----------


async def _load_skip_index(db: AsyncSession, profile_id: int) -> dict:
    rows = (
        await db.execute(
            select(AppProfileSkipRule).where(
                AppProfileSkipRule.profile_id == profile_id, AppProfileSkipRule.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    idx = {"suite": {}, "case": {}, "step": {}, "assertion": {}, "suite_step": {}}
    for rule in rows:
        if rule.target_type == "suite" and rule.suite_id is not None:
            idx["suite"][rule.suite_id] = rule
        elif rule.target_type == "case" and rule.suite_id is not None and rule.case_id is not None:
            idx["case"][(rule.suite_id, rule.case_id)] = rule
        elif (
            rule.target_type == "suite_step"
            and rule.suite_id is not None
            and rule.node_key is not None
        ):
            idx["suite_step"][(rule.suite_id, str(rule.node_key))] = rule
        elif (
            rule.target_type in ("step", "assertion")
            and rule.suite_id is not None
            and rule.case_id is not None
            and rule.node_key is not None
        ):
            idx[rule.target_type].setdefault(
                (rule.suite_id, rule.case_id), {}
            )[str(rule.node_key)] = rule
    return idx


async def _load_override_index(db: AsyncSession, profile_id: int) -> dict:
    el = (
        await db.execute(
            select(AppProfileElementOverride.element_id).where(
                AppProfileElementOverride.profile_id == profile_id, AppProfileElementOverride.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    var = (
        await db.execute(
            select(AppProfileVariableOverride.name).where(
                AppProfileVariableOverride.profile_id == profile_id, AppProfileVariableOverride.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    node = (
        await db.execute(
            select(AppProfileNodeOverride.suite_id, AppProfileNodeOverride.case_id, AppProfileNodeOverride.target_type, AppProfileNodeOverride.node_key, AppProfileNodeOverride.patch)
            .where(AppProfileNodeOverride.profile_id == profile_id, AppProfileNodeOverride.deleted_at.is_(None))
        )
    ).all()
    node_idx: dict[tuple[int, int], dict[str, str]] = {}
    suite_step_idx: dict[tuple[int, str], dict] = {}
    for sid, cid, ttype, nkey, patch in node:
        if ttype == "suite_step" and sid is not None:
            suite_step_idx[(sid, str(nkey))] = patch
            continue
        if sid is not None and cid is not None:
            node_idx.setdefault((sid, cid), {})[str(nkey)] = ttype
    return {"element": list(el), "variable": list(var), "node": node_idx, "suite_step": suite_step_idx}


async def _case_counts(db: AsyncSession, project_id: int) -> dict[int, int]:
    counts: dict[int, int] = {}
    rows = (
        await db.execute(
            select(TestSuiteCase.suite_id, func.count())
            .join(TestCase, TestCase.id == TestSuiteCase.case_id)
            .where(TestCase.project_id == project_id, TestCase.deleted_at.is_(None))
            .group_by(TestSuiteCase.suite_id)
        )
    ).all()
    for sid, cnt in rows:
        counts[sid] = cnt
    return counts


async def _diff_counts(db: AsyncSession, project_id: int, profile_id: int) -> dict[int, int]:
    """按套件聚合差异（跳过用例 + 节点 + 套件步骤 + 覆盖）数，简化：返回套件维度跳过用例数。"""
    skip = await _load_skip_index(db, profile_id)
    counts: dict[int, int] = {}
    for sid, _cid in skip["case"]:
        counts[sid] = counts.get(sid, 0) + 1
    for (sid, _cid), rules in skip["step"].items():
        counts[sid] = counts.get(sid, 0) + len(rules)
    for (sid, _cid), rules in skip["assertion"].items():
        counts[sid] = counts.get(sid, 0) + len(rules)
    for (sid, _nk), _rule in skip["suite_step"].items():
        counts[sid] = counts.get(sid, 0) + 1
    return counts


async def _override_counts(db: AsyncSession, project_id: int, profile_id: int) -> dict[int, int]:
    """按套件聚合节点覆盖数量（用例维度 + 套件步骤维度），供工作台“已覆盖”状态筛选。"""
    rows = (
        await db.execute(
            select(AppProfileNodeOverride.suite_id, func.count(AppProfileNodeOverride.id))
            .where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.deleted_at.is_(None),
                AppProfileNodeOverride.target_type.in_(("step", "assertion")),
                AppProfileNodeOverride.suite_id.is_not(None),
            )
            .group_by(AppProfileNodeOverride.suite_id)
        )
    ).all()
    counts = {suite_id: count for suite_id, count in rows}
    suite_step_rows = (
        await db.execute(
            select(AppProfileNodeOverride.suite_id, func.count(AppProfileNodeOverride.id))
            .where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.deleted_at.is_(None),
                AppProfileNodeOverride.target_type == "suite_step",
                AppProfileNodeOverride.suite_id.is_not(None),
            )
            .group_by(AppProfileNodeOverride.suite_id)
        )
    ).all()
    for suite_id, count in suite_step_rows:
        counts[suite_id] = counts.get(suite_id, 0) + count
    return counts


def _sort_workspace(items: list[dict], sort_by: str, sort_order: str) -> list[dict]:
    key_map = {"name": "name", "updated_at": "updated_at", "case_count": "child_count"}
    key = key_map.get(sort_by, "name")
    return sorted(items, key=lambda x: (x.get(key) is None, x.get(key)), reverse=(sort_order == "desc"))


async def _suite_cases(db: AsyncSession, suite_id: int) -> list[TestCase]:
    rows = (
        await db.execute(
            select(TestCase)
            .join(TestSuiteCase, TestSuiteCase.case_id == TestCase.id)
            .where(TestSuiteCase.suite_id == suite_id, TestCase.deleted_at.is_(None))
            .order_by(TestSuiteCase.sort_order)
        )
    ).scalars().all()
    return list(rows)


def _node_item(
    node_type: str,
    case_id: int,
    node_key: str,
    node: dict,
    rule,
    inherited_rule=None,
    overridden: bool = False,
) -> dict:
    effective_rule = inherited_rule or rule
    effective = "skipped" if effective_rule else ("overridden" if overridden else "enabled")
    source = "inherited" if inherited_rule else ("direct" if rule else ("override" if overridden else "none"))
    reason = None
    if effective_rule:
        reason = {"code": effective_rule.reason_code, "note": effective_rule.reason_note or ""}
    name = node.get("description") or node.get("action") or node.get("type") or ""
    return {
        "node_type": node_type,
        "id": None,
        "node_key": node_key,
        "name": name,
        "phase": node.get("phase"),
        "order": node.get("order"),
        "effective_status": effective,
        "status_source": source,
        "reason": reason,
        "override_count": 1 if overridden else 0,
        "has_children": False,
        "updated_at": None,
    }


def _suite_step_item(
    suite_id: int,
    node_key: str,
    node: dict,
    phase: str,
    rule,
    overridden: bool = False,
) -> dict:
    """套件前后置步骤工作台节点：node_type='suite_step'，id=suite_id，phase=suite_setup/suite_teardown。"""
    effective = "skipped" if rule else ("overridden" if overridden else "enabled")
    source = "direct" if rule else ("override" if overridden else "none")
    reason = None
    if rule:
        reason = {"code": rule.reason_code, "note": rule.reason_note or ""}
    name = node.get("description") or node.get("action") or node.get("type") or ""
    return {
        "node_type": "suite_step",
        "id": suite_id,
        "node_key": node_key,
        "name": name,
        "phase": phase,
        "order": node.get("order"),
        "effective_status": effective,
        "status_source": source,
        "reason": reason,
        "override_count": 1 if overridden else 0,
        "has_children": False,
        "updated_at": None,
    }


def _skip_row(target_type: str, path: str, rule, source_type: str, suite_id: int | None = None, case_id: int | None = None) -> dict:
    row = {
        "target_type": target_type,
        "path": path,
        "reason_code": rule.reason_code,
        "reason_note": rule.reason_note,
        "source_type": source_type,
        "override": False,
    }
    if suite_id is not None:
        row["suite_id"] = suite_id
    if case_id is not None:
        row["case_id"] = case_id
    return row
