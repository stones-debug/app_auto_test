"""APP 档案overrides路由。"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.core.errors import api_error
from app.models import Project, User
from app.repositories import elements as elements_repo
from app.repositories.app_profiles import overrides as overrides_repo
from app.repositories.app_profiles import resolution as resolution_repo
from app.repositories.app_profiles import skip_rules as skip_rules_repo
from app.schemas.app_profile import (
    ElementOverrideDelete,
    ElementOverrideUpsert,
    NodeOverrideDelete,
    NodeOverridePatch,
    VariableOverrideDelete,
    VariableOverrideUpsert,
)
from app.services.profile_audit import find_idempotent_replay

from . import router
from ._shared import (
    _bump_and_audit,
    _find_case_node,
    _find_suite_step,
    _get_profile_or_404,
    require_profile_manager_by_profile,
)


@router.get("/app-profiles/{profile_id}/overrides", response_model=dict)
async def list_profile_overrides(
    profile_id: int,
    include_nodes: bool = True,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前有效覆盖值，供工作台编辑器回显。"""
    profile = await _get_profile_or_404(profile_id, db)
    await get_project_permission(profile.project_id, user, db)
    element_rows, variable_rows, node_rows = await overrides_repo.list_all(
        db, profile_id, include_nodes=include_nodes
    )
    return {
        "revision": profile.revision,
        "elements": [
            {
                "element_id": row.element_id,
                "locator_type": row.locator_type,
                "locator_value": row.locator_value,
                "locator_config": row.locator_config,
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
                "suite_case_id": row.suite_case_id,
                "case_id": row.case_id,
                "node_type": row.target_type,
                "node_key": str(row.node_key),
                "patch": row.patch,
            }
            for row in node_rows
        ],
    }

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
    el = await elements_repo.get_by_id(db, element_id)
    if el is None or el.deleted_at is not None or el.project_id != profile.project_id:
        raise api_error(status.HTTP_404_NOT_FOUND, "ELEMENT_NOT_FOUND", "元素不存在或跨项目")
    await overrides_repo.upsert_element(
        db, profile_id=profile_id, element_id=element_id, locator_type=body.locator_type,
        locator_value=body.locator_value,
        locator_config=body.locator_config.model_dump() if body.locator_config else None,
        user_id=user.id,
    )
    response_data = {
        "element_id": element_id,
        "locator_type": body.locator_type,
        "locator_value": body.locator_value,
    }
    new_revision = await _bump_and_audit(
        db, profile, body, "element_override_upsert", user, role, request, response_data=response_data
    )
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
    existing = await overrides_repo.get_element(db, profile_id, element_id)
    if existing is None:
        return
    await overrides_repo.soft_delete(existing, datetime.now(UTC), user.id)
    await _bump_and_audit(db, profile, body, "element_override_restore", user, role, request)

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
    await overrides_repo.upsert_variable(
        db, profile_id=profile_id, name=name, value=body.value,
        description=body.description, user_id=user.id,
    )
    response_data = {"name": name, "value": body.value, "description": body.description}
    new_revision = await _bump_and_audit(
        db, profile, body, "variable_override_upsert", user, role, request, response_data=response_data
    )
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
    existing = await overrides_repo.get_variable(db, profile_id, name)
    if existing is None:
        return
    await overrides_repo.soft_delete(existing, datetime.now(UTC), user.id)
    await _bump_and_audit(db, profile, body, "variable_override_restore", user, role, request)

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
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "PROFILE_TARGET_INVALID", "node_type 只允许 step|assertion")

    case = await resolution_repo.get_case(db, case_id)
    if case is None or case.deleted_at is not None or case.project_id != profile.project_id:
        raise api_error(status.HTTP_404_NOT_FOUND, "CASE_NOT_FOUND", "用例不存在或跨项目")
    suite, _case, membership = await skip_rules_repo.load_target(
        db, project_id=profile.project_id, suite_id=suite_id, case_id=case_id
    )
    if (
        suite is None
        or suite.deleted_at is not None
        or suite.project_id != profile.project_id
        or membership is None
    ):
        raise api_error(status.HTTP_404_NOT_FOUND, "SUITE_CASE_NOT_FOUND", "套件用例关系不存在")
    found = _find_case_node(case, node_type, node_key)
    if found is None:
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "PROFILE_TARGET_NOT_FOUND", "节点不存在或 node_key 非法")
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
            raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "PROFILE_OVERRIDE_INVALID", f"禁止修改节点字段: {key}")
        if key not in NODE_PATCH_ALLOWED:
            raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "PROFILE_OVERRIDE_INVALID", f"不允许覆盖字段: {key}")
    try:
        validate_node_patch(node_type, source_node, body.patch)
    except ProfileRuleError as exc:
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message) from None
    await overrides_repo.upsert_node(
        db, profile_id=profile_id, suite_id=suite_id, case_id=case_id,
        suite_case_id=membership,
        target_type=node_type, node_key=normalized_key, patch=body.patch, user_id=user.id,
    )
    response_data = {
        "suite_id": suite_id,
        "case_id": case_id,
        "suite_case_id": membership,
        "node_type": node_type,
        "node_key": normalized_key,
        "patch": body.patch,
    }
    new_revision = await _bump_and_audit(
        db, profile, body, "node_override_upsert", user, role, request, response_data=response_data
    )
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
    membership = await resolution_repo.find_membership(db, suite_id, case_id)
    existing = await overrides_repo.get_node(
        db, profile_id, suite_id=suite_id, case_id=case_id,
        target_type=node_type, node_key=normalized_key,
        suite_case_id=membership.id if membership is not None else None,
    )
    if existing is None:
        return
    await overrides_repo.soft_delete(existing, datetime.now(UTC), user.id)
    await _bump_and_audit(db, profile, body, "node_override_restore", user, role, request)

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
    suite = await resolution_repo.get_suite(db, suite_id)
    if suite is None or suite.deleted_at is not None or suite.project_id != profile.project_id:
        raise api_error(status.HTTP_404_NOT_FOUND, "SUITE_NOT_FOUND", "套件不存在或跨项目")
    found = _find_suite_step(suite, node_key)
    if found is None:
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "PROFILE_TARGET_NOT_FOUND", "套件步骤节点不存在或 node_key 非法")
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
            raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "PROFILE_OVERRIDE_INVALID", f"禁止修改节点字段: {key}")
        if key not in NODE_PATCH_ALLOWED:
            raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "PROFILE_OVERRIDE_INVALID", f"不允许覆盖字段: {key}")
    try:
        validate_node_patch("step", source_node, body.patch)
    except ProfileRuleError as exc:
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message) from None
    await overrides_repo.upsert_node(
        db, profile_id=profile_id, suite_id=suite_id, case_id=None,
        target_type="suite_step", node_key=normalized_key, patch=body.patch, user_id=user.id,
    )
    response_data = {
        "suite_id": suite_id,
        "node_type": "suite_step",
        "node_key": normalized_key,
        "patch": body.patch,
    }
    new_revision = await _bump_and_audit(
        db, profile, body, "node_override_upsert", user, role, request, response_data=response_data
    )
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
    existing = await overrides_repo.get_node(
        db, profile_id, suite_id=suite_id, case_id=None,
        target_type="suite_step", node_key=normalized_key,
    )
    if existing is None:
        return
    await overrides_repo.soft_delete(existing, datetime.now(UTC), user.id)
    await _bump_and_audit(db, profile, body, "node_override_restore", user, role, request)
