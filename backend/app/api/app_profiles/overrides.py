"""APP 档案overrides路由。"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.models import (
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileVariableOverride,
    Project,
    TestCase,
    TestSuite,
    TestSuiteCase,
    User,
)
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
            locator_config=body.locator_config.model_dump() if body.locator_config else None,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(existing)
        await db.flush()
    else:
        existing.deleted_at = None
        existing.locator_type = body.locator_type
        existing.locator_value = body.locator_value
        existing.locator_config = body.locator_config.model_dump() if body.locator_config else None
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
