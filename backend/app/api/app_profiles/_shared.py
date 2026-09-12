"""APP 档案路由共享的纯组装、权限和 Repository 适配函数。"""

from datetime import UTC, datetime

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.core.errors import api_error
from app.models import AppProfile, AppProfileRelease, Project, TestCase, TestSuite, User
from app.repositories.app_profiles import releases as releases_repo
from app.services import profile_service
from app.services.profile_revision import RevisionConflictError
from app.ws.managers import profile_config_manager


def _find_case_node(case: TestCase, node_type: str, node_key: str):
    from uuid import UUID
    try:
        normalized_key = str(UUID(str(node_key)))
    except ValueError:
        return None
    collection = [
        node for node in (case.flow_nodes or case.steps or [])
        if node_type == "step" and node.get("kind", "action") == "action"
        or node_type == "assertion" and node.get("kind") == "assertion"
    ]
    for node in collection or []:
        if isinstance(node, dict) and str(node.get("key") or "") == normalized_key:
            return normalized_key, node
    return None


def _find_suite_step(suite: TestSuite, node_key: str):
    from uuid import UUID
    try:
        normalized_key = str(UUID(str(node_key)))
    except ValueError:
        return None
    for phase, collection in (("suite_setup", suite.setup_steps or []), ("suite_teardown", suite.teardown_steps or [])):
        for node in collection:
            if isinstance(node, dict) and str(node.get("key") or "") == normalized_key:
                return normalized_key, node, phase
    return None


def _skip_row(
    target_type: str,
    path: str,
    rule,
    source_type: str,
    suite_id: int | None = None,
    suite_case_id: int | None = None,
    case_id: int | None = None,
) -> dict:
    row = {"target_type": target_type, "path": path, "reason_code": rule.reason_code, "reason_note": rule.reason_note, "source_type": source_type, "override": False}
    if suite_id is not None:
        row["suite_id"] = suite_id
    if suite_case_id is not None:
        row["suite_case_id"] = suite_case_id
    if case_id is not None:
        row["case_id"] = case_id
    return row


def require_profile_manager():
    async def _checker(perm: tuple[Project, str | None] = Depends(get_project_permission)):
        _project, role = perm
        if role not in ("owner", "admin"):
            raise api_error(status.HTTP_403_FORBIDDEN, "PROFILE_MANAGER_REQUIRED", "需要 Owner/Admin 权限")
        return perm
    return _checker


def _element_info(node: dict, element_names: dict[int, str] | None = None) -> tuple[int | None, str | None]:
    raw_element_id = node.get("element_id")
    try:
        element_id = int(raw_element_id) if raw_element_id is not None else None
    except (TypeError, ValueError):
        element_id = None
    return element_id, (element_names or {}).get(element_id) if element_id is not None else None


def _node_item(
    node_type: str, case_id: int, node_key: str, node: dict, rule, inherited_rule=None,
    overridden: bool = False, element_names: dict[int, str] | None = None,
    suite_case_id: int | None = None,
) -> dict:
    effective_rule = inherited_rule or rule
    effective = "skipped" if effective_rule else "enabled"
    source = "inherited" if inherited_rule else ("direct" if rule else "none")
    reason = {"code": effective_rule.reason_code, "note": effective_rule.reason_note or ""} if effective_rule else None
    element_id, element_name = _element_info(node, element_names)
    return {"node_type": node_type, "id": None, "suite_case_id": suite_case_id, "node_key": node_key, "element_id": element_id, "element_name": element_name, "name": node.get("description") or node.get("action") or node.get("type") or "", "registry_key": node.get("action") if node_type == "step" else node.get("type") or node.get("assertion_type"), "phase": node.get("phase"), "order": node.get("order"), "effective_status": effective, "status_source": source, "reason": reason, "override_count": 0, "has_children": False, "updated_at": None}


async def _broadcast_config(profile: AppProfile, user_id: int | None = None) -> None:
    await profile_config_manager.broadcast(profile.project_id, {"type": "profile_revision_changed", "project_id": profile.project_id, "profile_id": profile.id, "profile_revision": profile.revision, "changed_by": user_id, "changed_at": datetime.now(UTC).isoformat()})


def _suite_step_item(
    suite_id: int, node_key: str, node: dict, phase: str, rule, overridden: bool = False,
    element_names: dict[int, str] | None = None,
) -> dict:
    effective = "skipped" if rule else "enabled"
    source = "direct" if rule else "none"
    reason = {"code": rule.reason_code, "note": rule.reason_note or ""} if rule else None
    element_id, element_name = _element_info(node, element_names)
    return {"node_type": "suite_step", "id": suite_id, "node_key": node_key, "element_id": element_id, "element_name": element_name, "name": node.get("description") or node.get("action") or node.get("type") or "", "registry_key": node.get("action"), "phase": phase, "order": node.get("order"), "effective_status": effective, "status_source": source, "reason": reason, "override_count": 0, "has_children": False, "updated_at": None}


def _sort_workspace(items: list[dict], sort_by: str, sort_order: str) -> list[dict]:
    key = {"name": "name", "updated_at": "updated_at", "case_count": "child_count"}.get(sort_by, "name")
    return sorted(items, key=lambda item: (item.get(key) is None, item.get(key)), reverse=sort_order == "desc")


def require_profile_manager_by_profile():
    async def _checker(profile_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
        profile = await profile_service.get(db, profile_id)
        perm = await get_project_permission(profile.project_id, user, db)
        _project, role = perm
        if role not in ("owner", "admin"):
            raise api_error(status.HTTP_403_FORBIDDEN, "PROFILE_MANAGER_REQUIRED", "需要 Owner/Admin 权限")
        return perm
    return _checker


def _release_out(release: AppProfileRelease) -> dict:
    from app.services.app_profile_release_service import to_dict
    return to_dict(release)


def _profile_out(profile: AppProfile, counts: dict | None = None) -> dict:
    return profile_service.to_dict(profile, counts)


async def _bump_and_audit(db, profile, body, action, user, role, request, changes=None, response_data=None) -> int:
    try:
        return await profile_service.commit_bump_and_audit(
            db, profile=profile, body=body, action=action, user_id=user.id, role=role,
            audit=_audit_client(request), changes=changes, response_data=response_data,
        )
    except RevisionConflictError as err:
        raise api_error(status.HTTP_409_CONFLICT, err.code, "APP 档案版本已变化，请重新加载后再保存", {"current": err.current, "expected": err.expected}) from None


async def _get_profile_or_404(profile_id: int, db: AsyncSession) -> AppProfile:
    return await profile_service.get(db, profile_id)


def _audit_client(request: Request) -> dict[str, str | None]:
    return {"client_ip": request.client.host if request.client else None, "user_agent": request.headers.get("user-agent")}


def require_release_manager():
    async def _checker(release_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
        release = await releases_repo.get_by_id(db, release_id)
        if release is None or release.deleted_at is not None:
            raise api_error(status.HTTP_404_NOT_FOUND, "APP_RELEASE_NOT_FOUND", "发布版本不存在")
        profile = await profile_service.get(db, release.profile_id)
        perm = await get_project_permission(profile.project_id, user, db)
        _project, role = perm
        if role not in ("owner", "admin"):
            raise api_error(status.HTTP_403_FORBIDDEN, "PROFILE_MANAGER_REQUIRED", "需要 Owner/Admin 权限")
        return perm
    return _checker
