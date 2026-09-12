"""APP 档案用例编排项的变量详情与批量覆盖路由。

APP 档案 occurrence 变量覆盖以 ``suite_case_id``（``test_suite_cases.id``）为身份，
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
    MyVariableBatchRequest,
    MyVariablesPage,
    ProfileSuiteCaseVariablesOut,
    ProfileVariableOverrideBatchRequest,
)
from app.services import case_variable_service, user_variable_service
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
    case_variable_service.apply_fixed_layer(
        definitions, "occurrence", membership.variable_overrides or {}
    )
    occurrence_rows = await overrides_repo.list_suite_case_variable_overrides(db, profile.id, membership.id)
    occurrence_overrides = {row.name: row.value for row in occurrence_rows}
    variables = case_variable_service.build_profile_variables(case, definitions, occurrence_overrides)
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
    """批量写入当前套件编排项的统一变量覆盖。"""
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = modal_perm
    if body.request_id:
        replay = await find_idempotent_replay(db, profile_id, body.request_id, actor_id=user.id)
        if replay is not None:
            # 重放：审计里存的是提交前的 detail，用审计注入的 revision 补齐 profile_revision
            replay.setdefault("profile_revision", replay.get("revision"))
            return replay
    membership, case = await _load_membership_context(db, profile, suite_case_id)
    try:
        changes = await case_variable_service.apply_profile_occurrence_variable_updates(
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
        db, profile, body, "occurrence_variable_override_batch", user, role, request,
        changes=changes, response_data=detail,
    )
    # 响应模型是 ProfileSuiteCaseVariablesOut（与 GET 同构），新版本号只在 profile_revision；
    # 多余键（如 revision）会被响应模型丢弃，客户端必须读 profile_revision。
    return {"profile_revision": new_revision, **detail}


@router.get("/app-profiles/{profile_id}/my-variables", response_model=MyVariablesPage)
async def get_my_variables(
    profile_id: int,
    keyword: str = "",
    scope: str | None = None,
    overridden_only: bool = False,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = await get_project_permission(profile.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise api_error(status.HTTP_403_FORBIDDEN, "PROJECT_FORBIDDEN", "无权查看当前用户变量")
    if scope not in (None, "project", "suite", "case"):
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "VARIABLE_VALUE_INVALID", "变量作用域不合法")
    if page < 1 or page_size < 1 or page_size > 200:
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "PAGINATION_INVALID", "分页参数不合法")
    return await user_variable_service.list_my_variables(
        db, profile=profile, user=user, keyword=keyword, scope=scope,
        overridden_only=overridden_only, page=page, page_size=page_size,
    )


@router.patch("/app-profiles/{profile_id}/my-variables", response_model=MyVariablesPage)
async def patch_my_variables(
    profile_id: int,
    body: MyVariableBatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_profile_or_404(profile_id, db)
    _project, role = await get_project_permission(profile.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise api_error(status.HTTP_403_FORBIDDEN, "PROJECT_FORBIDDEN", "无权修改当前用户变量")
    return await user_variable_service.update_my_variables(
        db, profile=profile, user=user, request_id=body.request_id,
        updates=[item.model_dump() for item in body.updates], role=role,
        audit={"client_ip": request.client.host if request.client else None, "user_agent": request.headers.get("user-agent")},
    )
