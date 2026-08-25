from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    AppProfile,
    AppProfileRelease,
    Execution,
    ExecutionExclusion,
    ExecutionLog,
    ExecutionQueue,
    Project,
    TestCase,
    TestSuite,
    User,
)
from app.services.profile_resolver import (
    ProfileEmpty,
    ProfileRevisionConflict,
    ProfileRuleError,
    ResolutionRequest,
    get_resolver,
)


class _ProfileRequired(Exception):
    pass


async def _lock_and_verify_resolution_revisions(
    db: AsyncSession,
    request: ResolutionRequest,
    *,
    resolved_profile_revision: int,
    resolved_asset_revision: int,
    resolved_release_version: str,
) -> None:
    """提交执行前锁定双 revision，封闭预检解析与快照落库之间的竞态。"""
    asset_revision = await db.scalar(
        select(Project.test_asset_revision)
        .where(Project.id == request.project_id, Project.deleted_at.is_(None))
        .with_for_update()
    )
    if asset_revision != resolved_asset_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "TEST_ASSET_REVISION_CONFLICT",
                "current": asset_revision or 0,
                "expected": resolved_asset_revision,
            },
        )
    profile_revision = await db.scalar(
        select(AppProfile.revision)
        .where(AppProfile.id == request.profile_id, AppProfile.deleted_at.is_(None))
        .with_for_update()
    )
    if profile_revision != resolved_profile_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PROFILE_REVISION_CONFLICT",
                "current": profile_revision or 0,
                "expected": resolved_profile_revision,
            },
        )
    release_row = (
        await db.execute(
            select(AppProfileRelease.status, AppProfileRelease.version)
            .where(
                AppProfileRelease.id == request.release_id,
                AppProfileRelease.profile_id == request.profile_id,
                AppProfileRelease.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).one_or_none()
    if (
        release_row is None
        or release_row.status != "active"
        or release_row.version != resolved_release_version
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "APP_RELEASE_CHANGED", "message": "发布版本已变化，请重新预检"},
        )


async def _build_resolution_request(
    db: AsyncSession,
    *,
    project_id: int,
    type_: str,
    suite_id: int | None,
    case_id: int | None,
    body,
) -> ResolutionRequest:
    """从创建请求构造解析请求；缺档案/版本时抛 APP_PROFILE_REQUIRED。"""
    if body.app_profile_id is None:
        raise _ProfileRequired()
    target_type = "case" if type_ == "case" else ("suite" if type_ == "suite" else "batch")
    target_ids = [case_id] if case_id is not None else ([suite_id] if suite_id is not None else [])
    if type_ == "batch":
        target_ids = list((body.parameters or {}).get("suite_ids") or [])
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
) -> Execution:
    """方案 §6.1：创建执行并在同一事务固化快照/排除项/队列。

    revision 二次检查：resolver 解析前要求 expected 与档案/项目当前 revision 一致，
    任一变化抛 ProfileRevisionConflict → 409，不创建任何执行。
    """
    request = await _build_resolution_request(
        db, project_id=project_id, type_=type_, suite_id=suite_id, case_id=case_id, body=body,
    )
    try:
        result = await get_resolver().preview(request, db)
    except ProfileRevisionConflict as err:
        from app.services.metrics import inc_conflict

        type_ = "profile" if "PROFILE_REVISION" in err.code else "asset"
        inc_conflict(type_)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": err.code, "current": err.current, "expected": err.expected},
        ) from None
    except ProfileEmpty:
        from app.services.metrics import inc_resolve

        inc_resolve("empty")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PROFILE_EMPTY"},
        ) from None
    except ProfileRuleError as err:
        from app.services.metrics import inc_resolve

        inc_resolve("invalid")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": err.code, "message": err.message},
        ) from None

    from app.services.metrics import inc_resolve, observe_snapshot

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
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "SNAPSHOT_TOO_LARGE", "message": f"快照超过上限 {settings.max_execution_snapshot_bytes // (1024 * 1024)} MB"},
        )

    execution = Execution(
        project_id=project_id,
        type=type_,
        suite_id=suite_id,
        case_id=case_id,
        device_id=device_id,
        status="queued",
        parameters=parameters or {},
        timeout_seconds=timeout_seconds or settings.default_execution_timeout,
        created_by=user.id,
        retry_of=retry_of,
        app_profile_id=body.app_profile_id,
        app_profile_name_snapshot=result.profile_name,
        app_release_id=body.app_release_id,
        app_release_version_snapshot=result.release_version or "",
        profile_revision=result.profile_revision,
        test_asset_revision=result.test_asset_revision,
        profile_resolution_summary={**result.summary},
    )
    db.add(execution)
    await db.flush()

    # 同事务固化：执行用例快照 + 排除项 + 队列
    import time as _time

    from app.services.execution_snapshot import materialize_snapshot

    _t0 = _time.monotonic()
    await materialize_snapshot(db, execution, result)
    _t1 = _time.monotonic()

    observe_snapshot(snapshot_bytes, (_t1 - _t0) * 1000.0)
    for ex in result.exclusions:
        display = ex.display_snapshot or {}
        db.add(
            ExecutionExclusion(
                execution_id=execution.id,
                app_profile_id=body.app_profile_id,
                target_type=ex.target_type,
                suite_id_snapshot=ex.suite_id,
                suite_name_snapshot=display.get("suite_name"),
                case_id_snapshot=ex.case_id,
                case_name_snapshot=display.get("case_name"),
                node_key=ex.node_key,
                node_name_snapshot=display.get("node_name"),
                source_type=ex.source_type,
                reason_code=ex.reason_code,
                reason_note=ex.reason_note,
                details=display,
            )
        )
    db.add(ExecutionQueue(execution_id=execution.id))
    await db.commit()
    await db.refresh(execution)
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
    execution = Execution(
        project_id=project_id,
        type=type_,
        suite_id=suite_id,
        case_id=case_id,
        device_id=device_id,
        status="queued",
        parameters=parameters or {},
        timeout_seconds=timeout_seconds or settings.default_execution_timeout,
        created_by=user.id,
        retry_of=retry_of,
    )
    db.add(execution)
    await db.flush()
    db.add(ExecutionQueue(execution_id=execution.id))
    await db.commit()
    await db.refresh(execution)
    return execution


async def create_case_execution(
    db: AsyncSession,
    case: TestCase,
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
    body=None,
) -> Execution:
    if body is None:
        return await _create_and_enqueue(
            db, project_id=case.project_id, type_="case", user=user, device_id=device_id,
            parameters=parameters, timeout_seconds=timeout_seconds, case_id=case.id,
        )
    return await _create_execution_with_profile(
        db, project_id=case.project_id, type_="case", user=user, device_id=device_id,
        parameters=parameters, timeout_seconds=timeout_seconds, body=body, case_id=case.id,
    )


async def create_suite_execution(
    db: AsyncSession,
    suite: TestSuite,
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
    body=None,
) -> Execution:
    if body is None:
        return await _create_and_enqueue(
            db, project_id=suite.project_id, type_="suite", user=user, device_id=device_id,
            parameters=parameters, timeout_seconds=timeout_seconds, suite_id=suite.id,
        )
    return await _create_execution_with_profile(
        db, project_id=suite.project_id, type_="suite", user=user, device_id=device_id,
        parameters=parameters, timeout_seconds=timeout_seconds, body=body, suite_id=suite.id,
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
    project_id = suites[0].project_id
    parameters = dict(parameters or {})
    suite_ids = parameters.get("suite_ids") or []
    suite_ids = list(dict.fromkeys([*suite_ids, *[s.id for s in suites]]))
    parameters["suite_ids"] = suite_ids
    if body is None:
        return await _create_and_enqueue(
            db, project_id=project_id, type_="batch", user=user, device_id=device_id,
            parameters=parameters, timeout_seconds=timeout_seconds,
        )
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
        result = await db.execute(
            update(Execution)
            .where(Execution.id == execution.id, Execution.status == "queued")
            .values(status="cancelled", finished_at=now, stop_requested_at=now, finalized_at=now)
        )
        await db.execute(
            update(ExecutionQueue)
            .where(ExecutionQueue.execution_id == execution.id)
            .values(status="done")
        )
        await db.commit()
        if result.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="执行已非排队态，无法取消")
        return "cancelled"
    if execution.status == "running":
        result = await db.execute(
            update(Execution)
            .where(Execution.id == execution.id, Execution.status == "running")
            .values(status="stopping", stop_requested_at=datetime.now(UTC))
        )
        await db.commit()
        if result.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="执行已非运行态，无需停止")
        return "stopping"
    if execution.status == "stopping":
        return "stopping"
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"执行状态为 {execution.status}，无法停止")


async def retry_execution(
    db: AsyncSession,
    execution: Execution,
    user: User,
    device_id: int,
    timeout_seconds: int | None = None,
) -> Execution:
    """方案 §6.5：重试复用原执行目标/档案/版本/运行选项，按当前档案 revision 创建新执行。"""
    if execution.app_profile_id is None:
        return await _create_and_enqueue(
            db,
            project_id=execution.project_id,
            type_=execution.type,
            user=user,
            device_id=device_id,
            parameters=execution.parameters or {},
            timeout_seconds=timeout_seconds or execution.timeout_seconds,
            suite_id=execution.suite_id,
            case_id=execution.case_id,
            retry_of=execution.id,
        )
    from types import SimpleNamespace

    from app.models import AppProfile, Project

    profile = await db.get(AppProfile, execution.app_profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="APP 档案不存在")
    project = await db.get(Project, execution.project_id)
    body = SimpleNamespace(
        app_profile_id=execution.app_profile_id,
        app_release_id=execution.app_release_id,
        expected_profile_revision=profile.revision,
        expected_test_asset_revision=project.test_asset_revision,
        parameters=execution.parameters or {},
    )
    return await _create_execution_with_profile(
        db,
        project_id=execution.project_id,
        type_=execution.type,
        user=user,
        device_id=device_id,
        parameters=execution.parameters or {},
        timeout_seconds=timeout_seconds or execution.timeout_seconds,
        body=body,
        suite_id=execution.suite_id,
        case_id=execution.case_id,
        retry_of=execution.id,
    )


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
    query = select(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
    count_query = (
        select(func.count())
        .select_from(ExecutionLog)
        .where(ExecutionLog.execution_id == execution_id)
    )
    if after_timestamp is not None:
        query = query.where(ExecutionLog.created_at > after_timestamp)
        count_query = count_query.where(ExecutionLog.created_at > after_timestamp)
    total = await db.scalar(count_query)
    rows = (
        await db.execute(
            query.order_by(ExecutionLog.created_at.asc(), ExecutionLog.id.asc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return rows, total or 0
