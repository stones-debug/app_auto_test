from datetime import datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_project_permission,
    require_device_access,
    require_project_write,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import ErrorCode, api_error
from app.core.ratelimit import rate_limit
from app.models import (
    Device,
    Execution,
    TestCase,
    TestSuite,
    User,
)
from app.repositories import agents as agents_repo
from app.repositories import cases as cases_repo
from app.repositories import devices as devices_repo
from app.repositories import executions as executions_repo
from app.repositories import projects as projects_repo
from app.repositories import suites as suites_repo
from app.repositories.app_profiles import profiles as profiles_repo
from app.schemas.execution import (
    BatchExecutionCreate,
    ExecutionCaseOut,
    ExecutionCreate,
    ExecutionDetail,
    ExecutionListItem,
    ExecutionLogOut,
    ExecutionLogPage,
    ExecutionOut,
    ExecutionPage,
    ExecutionPreviewRequest,
    ExecutionPreviewResponse,
    ExecutionRetryRequest,
    ExecutionStepOut,
    ExecutionSuiteOut,
)
from app.services import execution_service
from app.services.access_scope import visible_project_ids
from app.services.execution_detail_service import load_suite_tree
from app.services.profile_resolver import (
    ProfileEmpty,
    ProfileRevisionConflict,
    ProfileRuleError,
    ResolutionRequest,
    get_resolver,
)
from app.services.screenshot_store import resolve_screenshot_path
from app.utils.pagination import get_pagination

router = APIRouter(tags=["执行管理"])


def _apply_artifact(step_out: ExecutionStepOut, src: dict) -> ExecutionStepOut:
    step_out.artifact_id = (
        str(src.get("artifact_id") or f"step:{src['id']}")
        if src.get("screenshot_path")
        else None
    )
    return step_out


def suite_step_out(src: dict) -> ExecutionStepOut:
    return _apply_artifact(ExecutionStepOut.model_validate(src), src)


def case_step_out(src: dict) -> ExecutionStepOut:
    return _apply_artifact(ExecutionStepOut.model_validate(src), src)


def _execution_summary(suite_outs: list[ExecutionSuiteOut]) -> dict[str, int]:
    """过渡兼容：执行概要计数（套件/用例/步骤），供前端逐步迁移到 suites 嵌套结构。"""
    suites = len(suite_outs)
    cases = sum(len(s.cases) for s in suite_outs)
    setup_steps = sum(len(s.setup_steps) for s in suite_outs)
    teardown_steps = sum(len(s.teardown_steps) for s in suite_outs)
    case_steps = sum(len(c.steps) for s in suite_outs for c in s.cases)
    roles = {"passed", "failed", "error", "stopped", "skipped"}
    suite_by_status = {role: sum(1 for s in suite_outs if s.status == role) for role in roles}
    case_by_status = {role: sum(1 for s in suite_outs for c in s.cases if c.status == role) for role in roles}
    return {
        "suite_total": suites,
        "case_total": cases,
        "step_total": setup_steps + teardown_steps + case_steps,
        "suite_passed": suite_by_status.get("passed", 0),
        "suite_failed": suite_by_status.get("failed", 0),
        "suite_error_count": suite_by_status.get("error", 0),
        "suite_skipped": suite_by_status.get("skipped", 0),
        "case_passed": case_by_status.get("passed", 0),
        "case_failed": case_by_status.get("failed", 0),
        "case_error_count": case_by_status.get("error", 0),
        "case_skipped": case_by_status.get("skipped", 0),
    }


@router.post("/executions/preview", response_model=ExecutionPreviewResponse)
async def preview_execution(
    body: ExecutionPreviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """执行预检（方案 §4.7）：只读解析，不创建执行、不抢设备。"""
    project, role = await get_project_permission(body.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.PROJECT_FORBIDDEN, "无权执行")
    # 方案 §10.3：预检按 user+project 限流
    from app.core.ratelimit import rate_limit_check

    rate_limit_check("preview", f"u{user.id}:p{body.project_id}", settings.rate_limit_preview_per_minute)
    # 读取当前档案/项目 revision 作为 expected（预检返回给前端，供提交时二次校验）
    profile = await profiles_repo.get_by_id(db, body.app_profile_id)
    if profile is None or profile.deleted_at is not None or profile.project_id != body.project_id:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.APP_PROFILE_NOT_FOUND, "APP 档案不存在")
    expected_profile_rev = profile.revision
    expected_asset_rev = project.test_asset_revision
    await _validate_context_suite(
        db,
        project_id=body.project_id,
        target_type=cast(Literal["case", "suite", "batch"], body.target.type),
        target_ids=body.target.ids,
        context_suite_id=body.context_suite_id,
    )
    request = ResolutionRequest(
        project_id=body.project_id,
        profile_id=body.app_profile_id,
        release_id=body.app_release_id,
        target_type=cast(Literal["case", "suite", "batch"], body.target.type),
        target_ids=body.target.ids,
        expected_profile_revision=expected_profile_rev,
        expected_test_asset_revision=expected_asset_rev,
        run_options=body.parameters,
        execution_variables=(body.parameters or {}).get("variables") or {},
        context_suite_id=body.context_suite_id,
    )
    try:
        result = await get_resolver().preview(request, db)
    except ProfileRevisionConflict as err:
        raise api_error(
            status.HTTP_409_CONFLICT,
            err.code,
            "档案或测试资产版本已变化，请重新预检",
            {"current": err.current, "expected": err.expected},
        ) from None
    except ProfileEmpty as err:
        raise api_error(
            status.HTTP_400_BAD_REQUEST,
            ErrorCode.PROFILE_EMPTY,
            "解析后没有可执行用例",
            {"exclusions": [{"target_type": e.target_type, "name": e.display_snapshot.get("name")} for e in err.exclusions]},
        ) from None
    except ProfileRuleError as err:
        raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, err.code, err.message) from None

    return ExecutionPreviewResponse(
        profile_revision=result.profile_revision,
        test_asset_revision=result.test_asset_revision,
        profile={"id": body.app_profile_id, "name": result.profile_name},
        release={"id": body.app_release_id, "version": result.release_version},
        counts={**result.summary},
        exclusion_preview=[
            {
                "target_type": e.target_type,
                "path": e.display_snapshot.get("name") or "",
                "reason_code": e.reason_code,
                "reason_note": e.reason_note,
            }
            for e in result.exclusions
        ],
        warnings=result.warnings,
    )


async def _get_execution_or_404(execution_id: int, db: AsyncSession) -> Execution:
    execution = await executions_repo.get_by_id(db, execution_id)
    if execution is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "EXECUTION_NOT_FOUND", "执行记录不存在")
    return execution


async def _require_execution_access(execution: Execution, user: User, db: AsyncSession) -> None:
    """读权限：项目可访问即可（viewer 只读）。"""
    await get_project_permission(execution.project_id, user, db)


async def _require_execution_write(execution: Execution, user: User, db: AsyncSession) -> None:
    """写权限（CR-04）：创建/停止/重试要求 owner/admin/member。"""
    await require_project_write(execution.project_id, user, db)


async def _get_case_or_404(case_id: int, db: AsyncSession) -> TestCase:
    case = await cases_repo.get_by_id(db, case_id)
    if case is None or case.deleted_at is not None:
        raise api_error(status.HTTP_404_NOT_FOUND, "CASE_NOT_FOUND", "用例不存在")
    return case


async def _get_suite_or_404(suite_id: int, db: AsyncSession) -> TestSuite:
    suite = await suites_repo.get_by_id(db, suite_id)
    if suite is None or suite.deleted_at is not None:
        raise api_error(status.HTTP_404_NOT_FOUND, "SUITE_NOT_FOUND", "套件不存在")
    return suite


async def _validate_context_suite(
    db: AsyncSession, *, project_id: int, target_type: str, target_ids: list[int], context_suite_id: int | None
) -> None:
    """方案 §2：单用例套件上下文校验——仅 case 类型；用例必须属于该套件；套件/用例/项目同项目。"""
    if context_suite_id is None:
        return
    if target_type != "case":
        raise api_error(status.HTTP_400_BAD_REQUEST, "EXECUTION_CONTEXT_INVALID", "context_suite_id 仅支持执行单个用例")
    if len(target_ids) != 1:
        raise api_error(status.HTTP_400_BAD_REQUEST, "EXECUTION_CONTEXT_INVALID", "context_suite_id 仅支持单个用例")
    suite = await _get_suite_or_404(context_suite_id, db)
    case = await _get_case_or_404(target_ids[0], db)
    if suite.project_id != project_id or case.project_id != project_id:
        raise api_error(status.HTTP_400_BAD_REQUEST, "EXECUTION_CONTEXT_INVALID", "套件/用例/项目必须属于同一项目")
    member = await suites_repo.get_case_relation(db, suite.id, case.id)
    if member is None:
        raise api_error(status.HTTP_400_BAD_REQUEST, "EXECUTION_CONTEXT_INVALID", "用例不属于该套件")


async def _validate_device_for_execution(
    device_id: int | None, user: User, db: AsyncSession
) -> Device:
    """Windows 方案 §3.3：执行设备校验。

    - 缺失 → 400 DEVICE_REQUIRED（不再从全平台设备池随机选择）；
    - 用户无权（未绑定 Agent / 非管理员）→ 403；
    - 设备忙/被锁/Agent 离线 → 409（并发安全最终仍由 Worker 原子锁保证）。
    """
    if device_id is None:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="DEVICE_REQUIRED",
            message="必须指定执行设备 device_id",
        )
    device = await require_device_access(device_id, user, db)
    if device.status != "idle" or device.locked_by_execution is not None:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="DEVICE_BUSY",
            message="设备忙或已被其他执行占用",
        )
    agent = await agents_repo.get_by_id(db, device.agent_id)
    if agent is None or agent.status != "online":
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="AGENT_OFFLINE",
            message="Agent 离线，设备不可用",
        )
    return device


def _reject_current_screen_for_non_case(parameters: dict) -> None:
    if parameters.get("attach_to_current_app"):
        raise api_error(status.HTTP_400_BAD_REQUEST, "EXECUTION_TARGET_INVALID", "复用当前设备界面仅支持执行单个用例")


@router.post(
    "/executions/cases/{case_id}",
    response_model=ExecutionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_execution(
    case_id: int,
    body: ExecutionCreate,
    user: User = Depends(get_current_user),
    _rl: None = Depends(rate_limit("execution")),  # CR-21：执行创建限流
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case_or_404(case_id, db)
    await require_project_write(case.project_id, user, db)
    await _validate_context_suite(
        db,
        project_id=case.project_id,
        target_type="case",
        target_ids=[case.id],
        context_suite_id=body.context_suite_id,
    )
    await _validate_device_for_execution(body.device_id, user, db)
    profile_body = await execution_service.apply_app_profile_feature_mode(db, case.project_id, body)
    return await execution_service.create_case_execution(
        db, case, user, body.device_id, body.parameters, body.timeout_seconds,
        body=profile_body,
        context_suite_id=body.context_suite_id,
    )


@router.post(
    "/executions/suites/batch",
    response_model=ExecutionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch_execution(
    body: BatchExecutionCreate,
    user: User = Depends(get_current_user),
    _rl: None = Depends(rate_limit("execution")),  # CR-21：执行创建限流
    db: AsyncSession = Depends(get_db),
):
    _reject_current_screen_for_non_case(body.parameters)
    suites: list[TestSuite] = []
    project_id: int | None = None
    for sid in body.suite_ids:
        suite = await _get_suite_or_404(sid, db)
        if project_id is None:
            project_id = suite.project_id
        elif suite.project_id != project_id:
            raise api_error(status.HTTP_400_BAD_REQUEST, "EXECUTION_TARGET_INVALID", "批量执行的套件必须属于同一项目")
        suites.append(suite)
    if project_id is None:
        raise api_error(status.HTTP_400_BAD_REQUEST, "EXECUTION_TARGET_INVALID", "至少选择一个套件")
    await require_project_write(project_id, user, db)
    await _validate_device_for_execution(body.device_id, user, db)
    profile_body = await execution_service.apply_app_profile_feature_mode(db, project_id, body)
    return await execution_service.create_batch_execution(
        db, suites, user, body.device_id, body.parameters, body.timeout_seconds,
        body=profile_body,
    )


@router.post(
    "/executions/suites/{suite_id}",
    response_model=ExecutionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_suite_execution(
    suite_id: int,
    body: ExecutionCreate,
    user: User = Depends(get_current_user),
    _rl: None = Depends(rate_limit("execution")),  # CR-21：执行创建限流
    db: AsyncSession = Depends(get_db),
):
    _reject_current_screen_for_non_case(body.parameters)
    suite = await _get_suite_or_404(suite_id, db)
    await require_project_write(suite.project_id, user, db)
    await _validate_device_for_execution(body.device_id, user, db)
    profile_body = await execution_service.apply_app_profile_feature_mode(db, suite.project_id, body)
    return await execution_service.create_suite_execution(
        db, suite, user, body.device_id, body.parameters, body.timeout_seconds,
        body=profile_body,
    )


@router.get("/executions", response_model=ExecutionPage)
async def list_executions(
    project_id: int | None = None,
    status: str = "",
    type: str = "",
    keyword: str = "",
    device_id: int | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    pagination=Depends(get_pagination),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if project_id is not None:
        await get_project_permission(project_id, user, db)
    else:
        # Step 9：owner/member/public viewer 使用统一可见范围（公开项目执行可发现）
        project_id = None

    total, rows, names = await executions_repo.list_page(
        db, project_ids=list(await visible_project_ids(db, user.id)), project_id=project_id,
        status=status, type_=type, keyword=keyword, device_id=device_id,
        created_from=created_from, created_to=created_to,
        offset=pagination.offset, limit=pagination.limit,
    )

    items = []
    for row in rows:
        item = ExecutionListItem.model_validate(row)
        item.case_name = names["case"].get(row.case_id) if row.case_id else None
        item.suite_name = names["suite"].get(row.suite_id) if row.suite_id else None
        item.project_name = names["project"].get(row.project_id)
        item.device_name = names["device"].get(row.device_id) if row.device_id else None
        item.created_by_name = names["creator"].get(row.created_by) if row.created_by else None
        items.append(item)
    return {"total": total or 0, "page": pagination.page, "page_size": pagination.page_size, "items": items}


@router.get("/executions/{execution_id}", response_model=ExecutionDetail)
async def get_execution(
    execution_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_access(execution, user, db)
    # S1-7 §2：套件级嵌套聚合（含套件前后置 + 用例步骤/断言）
    tree = await load_suite_tree(db, execution_id)
    suite_outs: list[ExecutionSuiteOut] = []
    for suite in tree:
        suite_out = ExecutionSuiteOut.model_validate(suite)
        suite_out.setup_steps = [suite_step_out(s) for s in suite.get("setup_steps") or []]
        suite_out.teardown_steps = [suite_step_out(s) for s in suite.get("teardown_steps") or []]
        case_outs: list[ExecutionCaseOut] = []
        for case in suite.get("cases") or []:
            case_out = ExecutionCaseOut.model_validate(case)
            case_out.steps = [case_step_out(s) for s in case.get("steps") or []]
            case_outs.append(case_out)
        suite_out.cases = case_outs
        suite_outs.append(suite_out)

    detail = ExecutionDetail.model_validate(execution)
    detail.suites = suite_outs
    detail.summary = _execution_summary(suite_outs)
    project = await projects_repo.get_by_id(db, execution.project_id)
    detail.project_name = project.name if project else None
    if execution.device_id:
        device = await devices_repo.get_by_id(db, execution.device_id)
        detail.device_name = device.name if device else None
    if execution.created_by:
        creator = await projects_repo.get_user(db, execution.created_by)
        detail.created_by_name = creator.username if creator else None
    return detail


@router.get("/executions/{execution_id}/logs", response_model=ExecutionLogPage)
async def get_execution_logs(
    execution_id: int,
    after_timestamp: datetime | None = None,
    pagination=Depends(get_pagination),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_access(execution, user, db)
    rows, total = await execution_service.get_execution_logs(
        db, execution_id, after_timestamp, pagination.offset, pagination.limit
    )
    items = [ExecutionLogOut.model_validate(r) for r in rows]
    return {"total": total, "page": pagination.page, "page_size": pagination.page_size, "items": items}


@router.get("/executions/{execution_id}/artifacts/{artifact_id}")
async def get_execution_artifact(
    execution_id: int,
    artifact_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_access(execution, user, db)
    artifact = await executions_repo.get_artifact(db, execution_id, artifact_id)
    if artifact is None or not artifact.screenshot_path:
        raise api_error(status.HTTP_404_NOT_FOUND, "EXECUTION_ARTIFACT_NOT_FOUND", "执行附件不存在")
    target = resolve_screenshot_path(execution_id, artifact.screenshot_path)
    if target is None or not target.is_file():
        raise api_error(status.HTTP_404_NOT_FOUND, "EXECUTION_ARTIFACT_NOT_FOUND", "执行附件不存在")
    return FileResponse(target)


@router.post("/executions/{execution_id}/stop")
async def stop_execution(
    execution_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_write(execution, user, db)
    new_status = await execution_service.stop_execution(db, execution)
    return {"execution_id": execution.id, "status": new_status}


@router.post("/executions/{execution_id}/retry", response_model=ExecutionOut, status_code=status.HTTP_201_CREATED)
async def retry_execution(
    execution_id: int,
    body: ExecutionRetryRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_write(execution, user, db)
    if body is None:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="DEVICE_REQUIRED",
            message="重试必须指定执行设备 device_id",
        )
    await _validate_device_for_execution(body.device_id, user, db)
    return await execution_service.retry_execution(
        db, execution, user, body.device_id, body.timeout_seconds
    )
