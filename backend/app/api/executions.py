from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_project_permission,
    require_device_access,
    require_project_write,
)
from app.core.database import get_db
from app.core.errors import api_error
from app.core.ratelimit import rate_limit
from app.models import (
    Agent,
    AppProfile,
    Device,
    Execution,
    ExecutionCase,
    ExecutionStep,
    Project,
    TestCase,
    TestSuite,
    User,
)
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
)
from app.services import execution_service
from app.services.access_scope import visible_project_ids
from app.services.execution_detail_service import load_case_tree
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


@router.post("/executions/preview", response_model=ExecutionPreviewResponse)
async def preview_execution(
    body: ExecutionPreviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """执行预检（方案 §4.7）：只读解析，不创建执行、不抢设备。"""
    project, role = await get_project_permission(body.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权执行")
    # 读取当前档案/项目 revision 作为 expected（预检返回给前端，供提交时二次校验）
    profile = await db.get(AppProfile, body.app_profile_id)
    if profile is None or profile.deleted_at is not None or profile.project_id != body.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="APP 档案不存在")
    expected_profile_rev = profile.revision
    expected_asset_rev = project.test_asset_revision
    request = ResolutionRequest(
        project_id=body.project_id,
        profile_id=body.app_profile_id,
        release_id=body.app_release_id,
        target_type=body.target.type,
        target_ids=body.target.ids,
        expected_profile_revision=expected_profile_rev,
        expected_test_asset_revision=expected_asset_rev,
        run_options=body.parameters,
        execution_variables=(body.parameters or {}).get("variables") or {},
    )
    try:
        result = await get_resolver().preview(request, db)
    except ProfileRevisionConflict as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": err.code, "current": err.current, "expected": err.expected}) from None
    except ProfileEmpty as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PROFILE_EMPTY", "exclusions": [{"target_type": e.target_type, "name": e.display_snapshot.get("name")} for e in err.exclusions]},
        ) from None
    except ProfileRuleError as err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": err.code, "message": err.message}) from None

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
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行记录不存在")
    return execution


async def _require_execution_access(execution: Execution, user: User, db: AsyncSession) -> None:
    """读权限：项目可访问即可（viewer 只读）。"""
    await get_project_permission(execution.project_id, user, db)


async def _require_execution_write(execution: Execution, user: User, db: AsyncSession) -> None:
    """写权限（CR-04）：创建/停止/重试要求 owner/admin/member。"""
    await require_project_write(execution.project_id, user, db)


async def _get_case_or_404(case_id: int, db: AsyncSession) -> TestCase:
    case = await db.get(TestCase, case_id)
    if case is None or case.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
    return case


async def _get_suite_or_404(suite_id: int, db: AsyncSession) -> TestSuite:
    suite = await db.get(TestSuite, suite_id)
    if suite is None or suite.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
    return suite


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
    agent = await db.get(Agent, device.agent_id)
    if agent is None or agent.status != "online":
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="AGENT_OFFLINE",
            message="Agent 离线，设备不可用",
        )
    return device


def _reject_current_screen_for_non_case(parameters: dict) -> None:
    if parameters.get("attach_to_current_app"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="复用当前设备界面仅支持执行单个用例",
        )


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
    await _validate_device_for_execution(body.device_id, user, db)
    return await execution_service.create_case_execution(
        db, case, user, body.device_id, body.parameters, body.timeout_seconds
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="批量执行的套件必须属于同一项目")
        suites.append(suite)
    if project_id is not None:
        await require_project_write(project_id, user, db)
    await _validate_device_for_execution(body.device_id, user, db)
    return await execution_service.create_batch_execution(
        db, suites, user, body.device_id, body.parameters, body.timeout_seconds
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
    return await execution_service.create_suite_execution(
        db, suite, user, body.device_id, body.parameters, body.timeout_seconds
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
        query = select(Execution).where(Execution.project_id == project_id)
    else:
        # Step 9：owner/member/public viewer 使用统一可见范围（公开项目执行可发现）
        query = select(Execution).where(
            Execution.project_id.in_(visible_project_ids(user.id))
        )

    if status:
        query = query.where(Execution.status == status)
    if type:
        query = query.where(Execution.type == type)
    if device_id is not None:
        query = query.where(Execution.device_id == device_id)
    if created_from is not None:
        query = query.where(Execution.created_at >= created_from)
    if created_to is not None:
        query = query.where(Execution.created_at <= created_to)
    if keyword:
        # 匹配执行 ID 或关联对象名（case/suite 名称）；先解析数字 ID
        like = f"%{keyword}%"
        name_cond = Execution.case_id.in_(
            select(TestCase.id).where(TestCase.name.ilike(like))
        ) | Execution.suite_id.in_(select(TestSuite.id).where(TestSuite.name.ilike(like)))
        if keyword.isdigit():
            query = query.where((Execution.id == int(keyword)) | name_cond)
        else:
            query = query.where(name_cond)

    # CR-15：count 与 items 从同一过滤后的 base query 派生
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)
    rows = (
        await db.execute(
            query.order_by(Execution.id.desc()).offset(pagination.offset).limit(pagination.limit)
        )
    ).scalars().all()

    case_ids = {r.case_id for r in rows if r.case_id}
    suite_ids = {r.suite_id for r in rows if r.suite_id}
    project_ids = {r.project_id for r in rows}
    device_ids = {r.device_id for r in rows if r.device_id}
    creator_ids = {r.created_by for r in rows if r.created_by}
    case_names: dict[int, str] = {}
    suite_names: dict[int, str] = {}
    project_names: dict[int, str] = {}
    device_names: dict[int, str] = {}
    creator_names: dict[int, str] = {}
    if case_ids:
        cases = (await db.execute(select(TestCase).where(TestCase.id.in_(case_ids)))).scalars().all()
        case_names = {c.id: c.name for c in cases}
    if suite_ids:
        suites = (await db.execute(select(TestSuite).where(TestSuite.id.in_(suite_ids)))).scalars().all()
        suite_names = {s.id: s.name for s in suites}
    if project_ids:
        projects = (await db.execute(select(Project).where(Project.id.in_(project_ids)))).scalars().all()
        project_names = {p.id: p.name for p in projects}
    if device_ids:
        devices = (await db.execute(select(Device).where(Device.id.in_(device_ids)))).scalars().all()
        device_names = {d.id: d.name for d in devices}
    if creator_ids:
        creators = (await db.execute(select(User).where(User.id.in_(creator_ids)))).scalars().all()
        creator_names = {u.id: u.username for u in creators}

    items = []
    for row in rows:
        item = ExecutionListItem.model_validate(row)
        item.case_name = case_names.get(row.case_id) if row.case_id else None
        item.suite_name = suite_names.get(row.suite_id) if row.suite_id else None
        item.project_name = project_names.get(row.project_id)
        item.device_name = device_names.get(row.device_id) if row.device_id else None
        item.created_by_name = creator_names.get(row.created_by) if row.created_by else None
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
    # Step 8：case tree 经常数级聚合服务加载（3 条查询，不随 N 增长）
    tree = await load_case_tree(db, execution_id)
    case_outs: list[ExecutionCaseOut] = []
    for case in tree:
        case_out = ExecutionCaseOut.model_validate(case)
        case_out.steps = []
        for src in case.get("steps") or []:
            step_out = ExecutionStepOut.model_validate(src)
            step_out.artifact_id = src["id"] if src["screenshot_path"] else None
            case_out.steps.append(step_out)
        case_outs.append(case_out)

    detail = ExecutionDetail.model_validate(execution)
    detail.cases = case_outs
    project = await db.get(Project, execution.project_id)
    detail.project_name = project.name if project else None
    if execution.device_id:
        device = await db.get(Device, execution.device_id)
        detail.device_name = device.name if device else None
    if execution.created_by:
        creator = await db.get(User, execution.created_by)
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
    artifact_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    execution = await _get_execution_or_404(execution_id, db)
    await _require_execution_access(execution, user, db)
    step = (
        await db.execute(
            select(ExecutionStep)
            .join(ExecutionCase, ExecutionStep.execution_case_id == ExecutionCase.id)
            .where(
                ExecutionCase.execution_id == execution_id,
                ExecutionStep.id == artifact_id,
            )
        )
    ).scalar_one_or_none()
    if step is None or not step.screenshot_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行附件不存在")
    target = resolve_screenshot_path(execution_id, step.screenshot_path)
    if target is None or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行附件不存在")
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
