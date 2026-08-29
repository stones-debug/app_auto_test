"""APP 档案路由的跨资源依赖、权限和序列化辅助函数。"""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
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
    Project,
    TestCase,
    TestSuite,
    TestSuiteCase,
    User,
)
from app.services.profile_audit import write_audit
from app.services.profile_revision import RevisionConflictError, bump_profile_revision
from app.ws.managers import profile_config_manager


def _find_case_node(case: TestCase, node_type: str, node_key: str) -> tuple[str, dict] | None:
    try:
        normalized_key = str(UUID(str(node_key)))
    except ValueError:
        return None
    collection = case.steps if node_type == "step" else [
        assertion
        for step in (case.steps or [])
        for assertion in (step.get("assertions") or [])
    ]
    for node in collection or []:
        if isinstance(node, dict) and str(node.get("key") or "") == normalized_key:
            return normalized_key, node
    return None


def _find_suite_step(suite: TestSuite, node_key: str) -> tuple[str, dict, str] | None:
    """在套件 setup/teardown 步骤中定位节点，返回 (normalized_key, node, phase)。"""
    try:
        normalized_key = str(UUID(str(node_key)))
    except ValueError:
        return None
    for phase, collection in (
        ("suite_setup", suite.setup_steps or []),
        ("suite_teardown", suite.teardown_steps or []),
    ):
        for node in collection or []:
            if isinstance(node, dict) and str(node.get("key") or "") == normalized_key:
                return normalized_key, node, phase
    return None


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
        "registry_key": node.get("action") if node_type == "step" else node.get("type") or node.get("assertion_type"),
        "phase": node.get("phase"),
        "order": node.get("order"),
        "effective_status": effective,
        "status_source": source,
        "reason": reason,
        "override_count": 1 if overridden else 0,
        "override_template": _node_override_template(node),
        "has_children": False,
        "updated_at": None,
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
        "registry_key": node.get("action"),
        "phase": phase,
        "order": node.get("order"),
        "effective_status": effective,
        "status_source": source,
        "reason": reason,
        "override_count": 1 if overridden else 0,
        "override_template": _node_override_template(node),
        "has_children": False,
        "updated_at": None,
    }

def _sort_workspace(items: list[dict], sort_by: str, sort_order: str) -> list[dict]:
    key_map = {"name": "name", "updated_at": "updated_at", "case_count": "child_count"}
    key = key_map.get(sort_by, "name")
    return sorted(items, key=lambda x: (x.get(key) is None, x.get(key)), reverse=(sort_order == "desc"))

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

def _node_override_template(node: dict) -> dict:
    """返回公共节点中允许用户编辑的字段，避免暴露并误改节点身份字段。"""
    from app.services.profile_resolver import NODE_PATCH_ALLOWED

    return {
        key: deepcopy(value)
        for key, value in node.items()
        if key in NODE_PATCH_ALLOWED and value is not None
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

async def _profile_out(profile: AppProfile, db: AsyncSession) -> dict:
    return (await _profiles_out([profile], db))[0]

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

async def _get_profile_or_404(profile_id: int, db: AsyncSession) -> AppProfile:
    profile = await db.get(AppProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="APP 档案不存在")
    return profile

def _audit_client(request: Request) -> dict[str, str | None]:
    return {
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }

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
