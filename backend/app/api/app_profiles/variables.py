"""APP 档案用例编排项的变量详情与批量覆盖路由。

用例节点覆盖以 ``suite_case_id``（``test_suite_cases.id``）为身份，
同一用例在同一套件重复编排时各自独立。
"""

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission
from app.core.database import get_db
from app.core.errors import api_error
from app.models import AppProfile, Project, TestCase, TestSuiteCase, User
from app.repositories.app_profiles import overrides as overrides_repo
from app.repositories.app_profiles import resolution as resolution_repo
from app.schemas.app_profile import (
    ProfileSuiteCaseVariablesOut,
    ProfileVariableOverrideBatchRequest,
)
from app.services import case_variable_service
from app.services.profile_audit import find_idempotent_replay
from app.services.profile_resolver_nodes import ProfileRuleError

from . import router
from ._shared import _bump_and_audit, _get_profile_or_404, require_profile_manager_by_profile


async def _load_membership_context(
    db: AsyncSession, profile: AppProfile, suite_case_id: int
) -> tuple[TestSuiteCase, TestCase]:
    """校验编排项属于该档案所属项目，并返回其用例。"""
    membership = await resolution_repo.get_suite_case(db, suite_case_id)
    if membership is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "SUITE_CASE_NOT_FOUND", "套件用例编排项不存在")
    suite = await resolution_repo.get_suite(db, membership.suite_id)
    if suite is None or suite.deleted_at is not None or suite.project_id != profile.project_id:
        raise api_error(status.HTTP_404_NOT_FOUND, "SUITE_NOT_FOUND", "套件不存在或跨项目")
    case = await resolution_repo.get_case(db, membership.case_id)
    if case is None or case.deleted_at is not None or case.project_id != profile.project_id:
        raise api_error(status.HTTP_404_NOT_FOUND, "CASE_NOT_FOUND", "用例不存在或跨项目")
    return membership, case


async def _build_variables(
    db: AsyncSession, profile: AppProfile, membership: TestSuiteCase, case: TestCase
) -> dict:
    definitions = await case_variable_service.inherited_variable_definitions(
        db, project_id=profile.project_id, suite_id=membership.suite_id, case=case
    )
    # 节点覆盖之下依次是档案变量覆盖与编排项覆盖，保证“原值”就是恢复后的真实取值
    case_variable_service.apply_fixed_layer(
        definitions, "occurrence", membership.variable_overrides or {}
    )
    profile_variables = await overrides_repo.list_variable_overrides(db, profile.id)
    case_variable_service.apply_fixed_layer(
        definitions, "profile", {row.name: row.value for row in profile_variables}
    )
    rows = await overrides_repo.list_nodes_for_membership(db, profile.id, membership.id)
    node_overrides = {
        str(row.node_key): dict((row.patch or {}).get("variable_overrides") or {}) for row in rows
    }
    variables = case_variable_service.build_profile_variables(case, definitions, node_overrides)
    return {
        "profile_id": profile.id,
        "profile_revision": profile.revision,
        "suite_case_id": membership.id,
        "case_id": case.id,
        "case_name": case.name,
        "total": len(variables),
        "variables": variables,
    }


@router.get(
    "/app-profiles/{profile_id}/suite-cases/{suite_case_id}/variables",
    response_model=ProfileSuiteCaseVariablesOut,
)
async def get_suite_case_variables(
    profile_id: int,
    suite_case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    await get_project_permission(profile.project_id, user, db)
    membership, case = await _load_membership_context(db, profile, suite_case_id)
    return await _build_variables(db, profile, membership, case)


@router.patch(
    "/app-profiles/{profile_id}/suite-cases/{suite_case_id}/variable-overrides",
    response_model=ProfileSuiteCaseVariablesOut,
)
async def patch_suite_case_variables(
    profile_id: int,
    suite_case_id: int,
    body: ProfileVariableOverrideBatchRequest,
    request: Request,
    modal_perm: tuple[Project, str | None] = Depends(require_profile_manager_by_profile()),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量写入节点级变量覆盖：一个事务内校验、合并 patch、单次 revision 与单条审计。"""
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    if body.request_id:
        replay = await find_idempotent_replay(db, profile_id, body.request_id)
        if replay is not None:
            # 重放：审计里存的是提交前的 detail，用审计注入的 revision 补齐 profile_revision
            replay.setdefault("profile_revision", replay.get("revision"))
            return replay
    membership, case = await _load_membership_context(db, profile, suite_case_id)
    try:
        changes = await case_variable_service.apply_profile_variable_updates(
            db,
            profile_id=profile.id,
            membership=membership,
            case=case,
            updates=[item.model_dump() for item in body.updates],
            user_id=user.id,
        )
    except ProfileRuleError as exc:
        # 服务层已在失败时回滚（含无效更新时的整体回滚）
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message) from None
    detail = await _build_variables(db, profile, membership, case)
    # 提交后才产生新 revision，先摘掉陈旧值，由审计注入的 revision 补齐
    detail.pop("profile_revision", None)
    new_revision = await _bump_and_audit(
        db, profile, body, "node_override_batch", user, role, request,
        changes=changes, response_data=detail,
    )
    # 响应模型是 ProfileSuiteCaseVariablesOut（与 GET 同构），新版本号只在 profile_revision；
    # 多余键（如 revision）会被响应模型丢弃，客户端必须读 profile_revision。
    return {"profile_revision": new_revision, **detail}
