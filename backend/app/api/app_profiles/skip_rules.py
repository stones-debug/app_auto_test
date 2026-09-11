"""APP 档案skip_rules路由。"""

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.errors import api_error
from app.models import Project, User
from app.repositories.app_profiles import skip_rules as skip_rules_repo
from app.schemas.app_profile import SkipBatchRequest
from app.services.profile_audit import find_idempotent_replay
from app.services.profile_revision import RevisionConflictError
from app.services.profile_skip_service import apply_batch

from . import router
from ._shared import (
    _audit_client,
    _broadcast_config,
    _find_case_node,
    _find_suite_step,
    _get_profile_or_404,
    require_profile_manager_by_profile,
)


async def _validate_skip_target(db: AsyncSession, project_id: int, target, reason) -> tuple[dict, str | None]:
    """校验单个跳过目标；返回 (字段dict, 错误信息)。"""

    if target.type == "suite":
        if target.suite_id is None:
            return {}, "套件目标必须提供 suite_id"
        s, _c, _membership = await skip_rules_repo.load_target(db, project_id=project_id, suite_id=target.suite_id, case_id=None)
        if s is None or s.deleted_at is not None or s.project_id != project_id:
            return {}, "套件不存在或跨项目"
        return {"target_type": "suite", "suite_id": target.suite_id}, None
    if target.type == "case":
        if target.suite_id is None or target.case_id is None:
            return {}, "用例目标必须提供 suite_id 与 case_id"
        suite, c, membership = await skip_rules_repo.load_target(db, project_id=project_id, suite_id=target.suite_id, case_id=target.case_id)
        if suite is None or suite.deleted_at is not None or suite.project_id != project_id:
            return {}, "套件不存在或跨项目"
        if c is None or c.deleted_at is not None or c.project_id != project_id:
            return {}, "用例不存在或跨项目"
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
        suite, _c, _membership = await skip_rules_repo.load_target(db, project_id=project_id, suite_id=target.suite_id, case_id=None)
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
    suite, c, membership = await skip_rules_repo.load_target(db, project_id=project_id, suite_id=target.suite_id, case_id=target.case_id)
    if suite is None or suite.deleted_at is not None or suite.project_id != project_id:
        return {}, "套件不存在或跨项目"
    if c is None or c.deleted_at is not None or c.project_id != project_id:
        return {}, "用例不存在或跨项目"
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
    if len(body.targets) > 500:
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "PROFILE_RULE_INVALID", "单次批量跳过目标上限 500")
    if body.request_id:
        replay = await find_idempotent_replay(db, profile_id, body.request_id, actor_id=user.id)
        if replay is not None:
            return replay

    target_fields: list[tuple[int, dict]] = []
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
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "PROFILE_RULE_INVALID",
            "批量跳过目标存在校验错误",
            {"field_errors": field_errors},
        )
    if body.operation == "skip" and body.reason is None:
        raise api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "PROFILE_RULE_INVALID", "skip 操作必须提供 reason")


    try:
        result = await apply_batch(
            db, profile=profile, body=body, target_fields=target_fields,
            user_id=user.id, role=role, audit=_audit_client(request),
        )
    except RevisionConflictError as err:
        raise api_error(
            status.HTTP_409_CONFLICT,
            err.code,
            "APP 档案版本已变化，请重新加载后再保存",
            {"current": err.current, "expected": err.expected},
        ) from None

    await _broadcast_config(profile, user.id)
    return result
