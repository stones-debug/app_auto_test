import logging
import time
from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import app_profile_required_for_project, settings
from app.core.errors import ErrorCode, api_error
from app.models import (
    Execution,
    ExecutionLog,
    TestCase,
    TestSuite,
    User,
)
from app.repositories import executions as executions_repo
from app.repositories.app_profiles import profiles as profiles_repo
from app.repositories.app_profiles import releases as releases_repo
from app.services import execution_prepare
from app.services.profile_resolver import (
    ProfileEmpty,
    ProfileRevisionConflict,
    ProfileRuleError,
    ResolutionRequest,
    get_resolver,
)


class _ProfileRequired(Exception):
    pass


RETRYABLE_EXECUTION_STATES = frozenset({"passed", "failed", "error", "stopped", "cancelled"})
logger = logging.getLogger("app.execution")


def ensure_retryable_status(execution: Execution) -> None:
    if execution.status not in RETRYABLE_EXECUTION_STATES:
        raise api_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.EXECUTION_RETRY_NOT_ALLOWED,
            f"执行状态为 {execution.status}，仅终态执行允许重试",
            {"status": execution.status},
        )


async def apply_app_profile_feature_mode(db: AsyncSession, project_id: int, body):
    """按灰度模式处理旧执行请求。

    compat 灰度项目自动使用迁移创建的通用档案；required 模式要求调用方显式选择，
    off/非灰度项目保持旧执行协议。显式提交的档案上下文始终按正常规则校验。
    """
    if body.app_profile_id is not None or not app_profile_required_for_project(project_id):
        return body if body.app_profile_id is not None else None
    if settings.app_profile_feature_mode == "required":
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="APP_PROFILE_REQUIRED",
            message="必须选择 APP 档案和发布版本",
        )

    profile = await profiles_repo.find_default(db, project_id)
    if profile is None:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="DEFAULT_APP_PROFILE_MISSING",
            message="灰度项目缺少通用配置档案，请先运行回填脚本",
        )
    release = await releases_repo.find_active_for_profile(db, profile.id)
    project_revision = await profiles_repo.get_project_revision(db, project_id)
    if release is None or project_revision is None:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="DEFAULT_APP_RELEASE_MISSING",
            message="通用配置档案缺少可用发布版本",
        )
    body.app_profile_id = profile.id
    body.app_release_id = release.id
    body.expected_profile_revision = profile.revision
    body.expected_test_asset_revision = project_revision
    return body


async def _lock_and_verify_resolution_revisions(
    db: AsyncSession,
    request: ResolutionRequest,
    *,
    resolved_profile_revision: int,
    resolved_asset_revision: int,
    resolved_release_version: str,
) -> None:
    """提交执行前锁定双 revision，封闭预检解析与快照落库之间的竞态。"""
    asset_revision = await profiles_repo.get_project_revision(db, request.project_id)
    if asset_revision != resolved_asset_revision:
        raise api_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.TEST_ASSET_REVISION_CONFLICT,
            "测试资产版本已变化，请重新预检",
            {"current": asset_revision or 0, "expected": resolved_asset_revision},
        )
    profile_revision = await profiles_repo.lock_revision(db, request.profile_id)
    if profile_revision != resolved_profile_revision:
        raise api_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.PROFILE_REVISION_CONFLICT,
            "APP 档案版本已变化，请重新预检",
            {"current": profile_revision or 0, "expected": resolved_profile_revision},
        )
    if request.release_id is None:
        raise api_error(status.HTTP_400_BAD_REQUEST, ErrorCode.APP_RELEASE_NOT_FOUND, "缺少 APP 发布版本")
    release_row = await releases_repo.lock_for_resolution(
        db, release_id=request.release_id, profile_id=request.profile_id
    )
    if (
        release_row is None
        or release_row.status != "active"
        or release_row.version != resolved_release_version
    ):
        raise api_error(status.HTTP_409_CONFLICT, ErrorCode.APP_RELEASE_CHANGED, "发布版本已变化，请重新预检")


async def _build_resolution_request(
    db: AsyncSession,
    *,
    project_id: int,
    type_: str,
    suite_id: int | None,
    case_id: int | None,
    body,
    context_suite_id: int | None = None,
) -> ResolutionRequest:
    """从创建请求构造解析请求；缺档案/版本时抛 APP_PROFILE_REQUIRED。"""
    if body.app_profile_id is None:
        raise _ProfileRequired()
    target_type = "case" if type_ == "case" else ("suite" if type_ == "suite" else "batch")
    target_ids = [case_id] if case_id is not None else ([suite_id] if suite_id is not None else [])
    if type_ == "batch":
        target_ids = list((body.parameters or {}).get("suite_ids") or [])
    target_scope = cast(Literal["explicit", "profile_all"], getattr(body, "target_scope", "explicit"))
    return ResolutionRequest(
        project_id=project_id,
        profile_id=body.app_profile_id,
        release_id=body.app_release_id,
        target_type=target_type,
        target_ids=target_ids,
        expected_profile_revision=body.expected_profile_revision or 1,
        expected_test_asset_revision=body.expected_test_asset_revision or 1,
        run_options=body.parameters or {},
        execution_variables=(body.parameters or {}).get("variables") or {},
        context_suite_id=context_suite_id if type_ == "case" else None,
        target_scope=target_scope,
    )


async def _create_execution_with_profile(
    db: AsyncSession,
    *,
    project_id: int,
    type_: str,
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
    body,
    suite_id: int | None = None,
    case_id: int | None = None,
    retry_of: int | None = None,
    context_suite_id: int | None = None,
) -> Execution:
    """方案 §6.1：创建执行并在同一事务固化快照/排除项/队列。

    revision 二次检查：resolver 解析前要求 expected 与档案/项目当前 revision 一致，
    任一变化抛 ProfileRevisionConflict → 409，不创建任何执行。
    """
    create_started = time.monotonic()
    prepared = None
    if getattr(body, "prepare_token", None):
        prepared = await execution_prepare.lock_for_create(db, body.prepare_token)
        now = datetime.now(UTC)
        if prepared is None or prepared.user_id != user.id or prepared.project_id != project_id:
            raise api_error(status.HTTP_409_CONFLICT, ErrorCode.EXECUTION_PREPARE_INVALID, "预检令牌无效")
        if prepared.consumed_at is not None or prepared.expires_at <= now:
            raise api_error(status.HTTP_409_CONFLICT, ErrorCode.EXECUTION_PREPARE_INVALID, "预检已失效，请重新预检")
        if prepared.device_id != device_id:
            raise api_error(status.HTTP_409_CONFLICT, ErrorCode.EXECUTION_PREPARE_INVALID, "预检设备已变化，请重新预检")
        for field in ("app_profile_id", "app_release_id", "expected_profile_revision", "expected_test_asset_revision"):
            value = getattr(body, field, None)
            expected = {
                "app_profile_id": prepared.app_profile_id,
                "app_release_id": prepared.app_release_id,
                "expected_profile_revision": prepared.profile_revision,
                "expected_test_asset_revision": prepared.test_asset_revision,
            }[field]
            if value is not None and value != expected:
                raise api_error(status.HTTP_409_CONFLICT, ErrorCode.EXECUTION_PREPARE_INVALID, "预检上下文已变化，请重新预检")
        target_type = "case" if type_ == "case" else ("suite" if type_ == "suite" else "batch")
        target_ids = [case_id] if type_ == "case" and case_id is not None else (
            [suite_id] if type_ == "suite" and suite_id is not None else list(
                getattr(body, "suite_ids", None) or (body.parameters or {}).get("suite_ids") or []
            )
        )
        target_scope = cast(Literal["explicit", "profile_all"], getattr(body, "target_scope", "explicit"))
        prepared_ids = list((prepared.target or {}).get("ids") or [])
        if target_scope == "profile_all" and not target_ids:
            target_ids = prepared_ids
        target = execution_prepare.canonical_target(
            target_type=target_type,
            target_ids=target_ids,
            target_scope=target_scope,
            context_suite_id=context_suite_id if type_ == "case" else None,
            resolved_ids=prepared_ids if target_scope == "profile_all" else None,
        )
        if target != prepared.target or execution_prepare.request_hash(
            target=prepared.target, parameters=body.parameters or {}
        ) != prepared.request_hash:
            raise api_error(status.HTTP_409_CONFLICT, ErrorCode.EXECUTION_PREPARE_INVALID, "预检目标或参数已变化，请重新预检")
        request = ResolutionRequest(
            project_id=prepared.project_id,
            profile_id=prepared.app_profile_id,
            release_id=prepared.app_release_id,
            target_type=prepared.target["type"],
            target_ids=prepared_ids,
            expected_profile_revision=prepared.profile_revision,
            expected_test_asset_revision=prepared.test_asset_revision,
            run_options=deepcopy(prepared.parameters or {}),
            execution_variables=(prepared.parameters or {}).get("variables") or {},
            context_suite_id=prepared.target.get("context_suite_id"),
            target_scope=prepared.target.get("target_scope", "explicit"),
        )
        try:
            result = execution_prepare.deserialize_result(prepared.resolution_payload)
        except (KeyError, TypeError, ValueError):
            raise api_error(status.HTTP_409_CONFLICT, ErrorCode.EXECUTION_PREPARE_INVALID, "预检快照不可用") from None
    else:
        request = await _build_resolution_request(
            db, project_id=project_id, type_=type_, suite_id=suite_id, case_id=case_id, body=body,
            context_suite_id=context_suite_id,
        )
        try:
            result = await get_resolver().preview(request, db)
        except ProfileRevisionConflict as err:
            from app.services.metrics import inc_conflict

            type_ = "profile" if "PROFILE_REVISION" in err.code else "asset"
            inc_conflict(type_)
            raise api_error(
                status.HTTP_409_CONFLICT,
                err.code,
                "档案或测试资产版本已变化，请重新预检",
                {"current": err.current, "expected": err.expected},
            ) from None
        except ProfileEmpty:
            from app.services.metrics import inc_resolve

            inc_resolve("empty")
            raise api_error(status.HTTP_400_BAD_REQUEST, ErrorCode.PROFILE_EMPTY, "解析后没有可执行用例") from None
        except ProfileRuleError as err:
            from app.services.metrics import inc_resolve

            inc_resolve("invalid")
            raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, err.code, err.message) from None

    from app.services.metrics import inc_resolve, observe_snapshot

    resolution_mode = "prepare_hit" if prepared is not None else "fallback"
    if prepared is None:
        inc_resolve("success")

    await _lock_and_verify_resolution_revisions(
        db,
        request,
        resolved_profile_revision=result.profile_revision,
        resolved_asset_revision=result.test_asset_revision,
        resolved_release_version=result.release_version,
    )

    # 方案 §3.6/§6.2：快照上限（SNAPSHOT_TOO_LARGE）
    import json as _json

    snapshot_bytes = len(_json.dumps([c.__dict__ for c in result.cases], default=str).encode("utf-8"))
    if snapshot_bytes > settings.max_execution_snapshot_bytes:
        raise api_error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            ErrorCode.SNAPSHOT_TOO_LARGE,
            f"快照超过上限 {settings.max_execution_snapshot_bytes // (1024 * 1024)} MB",
            {"size": snapshot_bytes},
        )

    import time as _time
    _t0 = _time.monotonic()
    execution_parameters = dict(parameters or {})
    execution_suite_id = suite_id
    execution_case_id = case_id
    if prepared is not None:
        prepared_ids = list((prepared.target or {}).get("ids") or [])
        if type_ == "batch":
            execution_parameters["suite_ids"] = prepared_ids
            if prepared.target.get("target_scope") == "profile_all":
                execution_parameters["target_scope"] = "profile_all"
        elif type_ == "case" and execution_case_id is None and prepared_ids:
            execution_case_id = prepared_ids[0]
        elif type_ == "suite" and execution_suite_id is None and prepared_ids:
            execution_suite_id = prepared_ids[0]
    execution = await executions_repo.create_profiled(
        db,
        fields={
            "project_id": project_id, "type": type_, "suite_id": execution_suite_id,
            "case_id": execution_case_id, "device_id": device_id, "status": "queued",
            "parameters": execution_parameters, "timeout_seconds": timeout_seconds or settings.default_execution_timeout,
            "created_by": user.id, "retry_of": retry_of,
            "app_profile_id": prepared.app_profile_id if prepared else body.app_profile_id,
            "app_profile_name_snapshot": result.profile_name,
            "app_release_id": prepared.app_release_id if prepared else body.app_release_id,
            "app_release_version_snapshot": result.release_version or "", "profile_revision": result.profile_revision,
            "test_asset_revision": result.test_asset_revision, "profile_resolution_summary": {**result.summary},
        },
        result=result, app_profile_id=prepared.app_profile_id if prepared else body.app_profile_id,
    )
    if prepared is not None:
        prepared.consumed_at = datetime.now(UTC)
    observe_snapshot(snapshot_bytes, (_time.monotonic() - _t0) * 1000.0)
    await db.commit()
    await executions_repo.refresh(db, execution)
    logger.info(
        "execution_create stage=create prepare=%s project_id=%s profile_id=%s target_scope=%s "
        "suite_count=%s case_count=%s node_count=%s snapshot_bytes=%s elapsed_ms=%.1f",
        resolution_mode,
        project_id,
        prepared.app_profile_id if prepared else body.app_profile_id,
        request.target_scope,
        result.summary.get("executable_suites", 0),
        result.summary.get("executable_cases", 0),
        result.summary.get("executable_steps", 0),
        snapshot_bytes,
        (time.monotonic() - create_started) * 1000,
    )
    return execution


async def _create_and_enqueue(
    db: AsyncSession,
    project_id: int,
    type_: str,
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
    *,
    suite_id: int | None = None,
    case_id: int | None = None,
    retry_of: int | None = None,
) -> Execution:
    execution = await executions_repo.create(db, fields={
        "project_id": project_id, "type": type_, "suite_id": suite_id, "case_id": case_id,
        "device_id": device_id, "status": "queued", "parameters": parameters or {},
        "timeout_seconds": timeout_seconds or settings.default_execution_timeout,
        "created_by": user.id, "retry_of": retry_of,
    })
    await executions_repo.enqueue(db, execution.id)
    await db.commit()
    await executions_repo.refresh(db, execution)
    return execution


async def create_case_execution(
    db: AsyncSession,
    case: TestCase | None,
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
    body=None,
    context_suite_id: int | None = None,
    project_id: int | None = None,
    asset_case_id: int | None = None,
) -> Execution:
    if body is None:
        if case is None:
            raise ValueError("case required without profile context")
        return await _create_and_enqueue(
            db, project_id=case.project_id, type_="case", user=user, device_id=device_id,
            parameters=parameters, timeout_seconds=timeout_seconds, case_id=case.id,
        )
    return await _create_execution_with_profile(
        db, project_id=project_id if project_id is not None else case.project_id if case is not None else 0, type_="case", user=user, device_id=device_id,
        parameters=parameters, timeout_seconds=timeout_seconds, body=body,
        case_id=asset_case_id if asset_case_id is not None else case.id if case is not None else None,
        context_suite_id=context_suite_id,
    )


async def create_suite_execution(
    db: AsyncSession,
    suite: TestSuite | None,
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
    body=None,
    project_id: int | None = None,
    asset_suite_id: int | None = None,
) -> Execution:
    if body is None:
        if suite is None:
            raise ValueError("suite required without profile context")
        return await _create_and_enqueue(
            db, project_id=suite.project_id, type_="suite", user=user, device_id=device_id,
            parameters=parameters, timeout_seconds=timeout_seconds, suite_id=suite.id,
        )
    return await _create_execution_with_profile(
        db, project_id=project_id if project_id is not None else suite.project_id if suite is not None else 0, type_="suite", user=user, device_id=device_id,
        parameters=parameters, timeout_seconds=timeout_seconds, body=body,
        suite_id=asset_suite_id if asset_suite_id is not None else suite.id if suite is not None else None,
    )


async def create_batch_execution(
    db: AsyncSession,
    suites: list[TestSuite],
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
    body=None,
) -> Execution:
    if suites:
        project_id = suites[0].project_id
    elif body is not None and getattr(body, "prepare_token", None):
        prepared = await execution_prepare.peek(db, body.prepare_token)
        if prepared is None:
            raise api_error(status.HTTP_409_CONFLICT, ErrorCode.EXECUTION_PREPARE_INVALID, "预检令牌无效")
        project_id = prepared.project_id
    else:
        raise ValueError("suite required for batch execution")
    parameters = dict(parameters or {})
    if body is not None and getattr(body, "prepare_token", None):
        return await _create_execution_with_profile(
            db, project_id=project_id, type_="batch", user=user, device_id=device_id,
            parameters=parameters, timeout_seconds=timeout_seconds, body=body,
        )
    suite_ids = parameters.get("suite_ids") or []
    suite_ids = list(dict.fromkeys([*suite_ids, *[s.id for s in suites]]))
    parameters["suite_ids"] = suite_ids
    if getattr(body, "target_scope", "explicit") == "profile_all":
        parameters["target_scope"] = "profile_all"
    if body is None:
        return await _create_and_enqueue(
            db, project_id=project_id, type_="batch", user=user, device_id=device_id,
            parameters=parameters, timeout_seconds=timeout_seconds,
        )
    # 批量解析器从 body.parameters 读取 suite_ids；确保顶层请求字段在预检与正式快照中一致。
    body.parameters = parameters
    return await _create_execution_with_profile(
        db, project_id=project_id, type_="batch", user=user, device_id=device_id,
        parameters=parameters, timeout_seconds=timeout_seconds, body=body,
    )


async def stop_execution(db: AsyncSession, execution: Execution) -> str:
    """queued → cancelled；running → stopping；其他状态拒绝。

    CR-12：queued 取消使用条件更新，与 Worker 原子认领竞争时只有一个赢家。
    """
    if execution.status == "queued":
        now = datetime.now(UTC)
        rowcount = await executions_repo.stop_queued(db, execution.id, now)
        await db.commit()
        if rowcount != 1:
            raise api_error(status.HTTP_409_CONFLICT, ErrorCode.EXECUTION_NOT_STOPPABLE, "执行已非排队态，无法取消")
        return "cancelled"
    if execution.status == "running":
        rowcount = await executions_repo.stop_running(db, execution.id, datetime.now(UTC))
        await db.commit()
        if rowcount != 1:
            raise api_error(status.HTTP_409_CONFLICT, ErrorCode.EXECUTION_NOT_STOPPABLE, "执行已非运行态，无需停止")
        return "stopping"
    if execution.status == "stopping":
        return "stopping"
    raise api_error(
        status.HTTP_409_CONFLICT,
        ErrorCode.EXECUTION_NOT_STOPPABLE,
        f"执行状态为 {execution.status}，无法停止",
        {"status": execution.status},
    )


async def retry_execution(
    db: AsyncSession,
    execution: Execution,
    user: User,
    device_id: int,
    timeout_seconds: int | None = None,
) -> Execution:
    """用原执行的完整物化树创建重试，不读取当前资产、档案或发布版本。"""
    ensure_retryable_status(execution)
    source_execution_id = execution.id
    try:
        retry = await executions_repo.clone_snapshot(
            db,
            execution,
            user_id=user.id,
            device_id=device_id,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else execution.timeout_seconds,
        )
        await db.commit()
        await executions_repo.refresh(db, retry)
        return retry
    except executions_repo.SnapshotNotReadyError as exc:
        await db.rollback()
        raise api_error(
            status.HTTP_409_CONFLICT,
            ErrorCode.EXECUTION_SNAPSHOT_NOT_READY,
            str(exc),
            {"execution_id": source_execution_id},
        ) from None
    except Exception:
        await db.rollback()
        raise


async def get_execution_logs(
    db: AsyncSession,
    execution_id: int,
    after_timestamp: datetime | None,
    offset: int,
    limit: int,
) -> tuple[list[ExecutionLog], int]:
    """日志分页。

    Step 8：total 用 select(func.count())，不读取全部 ID；
    排序使用 (created_at, id)，避免同毫秒日志漏读（after 游标仅支持 timestamp，
    同毫秒边界以 id 次序稳定分页，见文档注明）。
    """
    return await executions_repo.list_logs(db, execution_id, after_timestamp, offset, limit)
