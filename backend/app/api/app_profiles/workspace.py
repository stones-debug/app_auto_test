"""APP 档案workspace路由。"""

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.models import User
from app.repositories import projects as projects_repo
from app.repositories.app_profiles import resolution as resolution_repo
from app.repositories.app_profiles import skip_rules as skip_rules_repo

from . import router
from ._shared import (
    _get_profile_or_404,
    _node_item,
    _skip_row,
    _sort_workspace,
    _suite_step_item,
)


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
    project = await projects_repo.get_by_id(db, profile.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    suites = await resolution_repo.list_suites(db, profile.project_id)
    skip = await resolution_repo.load_skip_index(db, profile_id)
    case_counts = await resolution_repo.case_counts(db, profile.project_id)
    diff_counts = await resolution_repo.diff_counts(db, profile_id)
    override_counts = await resolution_repo.override_counts(db, profile_id)

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
    skip = await resolution_repo.load_skip_index(db, profile_id)
    overrides = await resolution_repo.load_override_index(db, profile_id)
    if parent_type == "suite":
        suite = await resolution_repo.get_suite(db, parent_id)
        if suite is None or suite.deleted_at is not None or suite.project_id != profile.project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
        rows = await resolution_repo.suite_cases(db, parent_id)
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
        case = await resolution_repo.get_case(db, parent_id)
        if case is None or case.deleted_at is not None or case.project_id != profile.project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
        suite_rule = None
        if ancestor_suite_id is not None:
            suite, _case, membership = await skip_rules_repo.load_target(
                db, project_id=profile.project_id, suite_id=ancestor_suite_id, case_id=case.id
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
        for node in (case.flow_nodes or case.steps or []):
            if node.get("kind", "action") != "action":
                continue
            node_key = str(node.get("key") or "")
            rule = skip["step"].get((ancestor_suite_id, case.id), {}).get(node_key)
            overridden = overrides["node"].get((ancestor_suite_id, case.id), {}).get(node_key) == "step"
            items.append(_node_item("step", case.id, node_key, node, rule, case_rule, overridden))
        for node in (case.flow_nodes or case.steps or []):
            if node.get("kind") != "assertion":
                continue
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
    suite = await resolution_repo.get_suite(db, suite_id)
    if suite is None or suite.deleted_at is not None or suite.project_id != profile.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
    skip = await resolution_repo.load_skip_index(db, profile_id)
    overrides = await resolution_repo.load_override_index(db, profile_id)

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
    skip = await resolution_repo.load_skip_index(db, profile_id)
    overrides = await resolution_repo.load_override_index(db, profile_id)
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
    suite_names, case_names, step_suites = await resolution_repo.difference_names(
        db, suite_ids=suite_ids, case_ids=case_ids
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
