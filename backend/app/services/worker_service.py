import asyncio
import json
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Agent,
    Device,
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionLog,
    ExecutionQueue,
    ExecutionStep,
    Report,
    TestCase,
    TestElement,
    TestSuiteCase,
    Variable,
)

logger = logging.getLogger("worker")

_VAR_RE = re.compile(r"\$\{(\w+)\}")
TERMINAL_STATES = {"passed", "failed", "error", "stopped", "cancelled"}
# Step 10：协议版本来自 Registry 生成产物（单一来源）
_PROTOCOL_JSON = Path(__file__).resolve().parents[1] / "protocol" / "action_registry.json"


def protocol_version() -> str:
    try:
        with _PROTOCOL_JSON.open("r", encoding="utf-8") as fh:
            return str(json.load(fh).get("protocol_version", "0.0.0"))
    except OSError:  # pragma: no cover
        return "0.0.0"


# ---------- 变量渲染（§10.6：优先级 执行参数 > 套件 > 用例 > 项目 > 全局） ----------


def render_text(text: str, variables: dict) -> str:
    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            raise ValueError(f"未定义变量: ${{{name}}}")
        return str(variables[name])

    return _VAR_RE.sub(repl, text)


def render_value(value, variables: dict):
    if isinstance(value, str):
        return render_text(value, variables)
    if isinstance(value, dict):
        return {k: render_value(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, variables) for v in value]
    return value


async def build_variable_map(
    db: AsyncSession,
    execution: Execution,
    case: TestCase | None = None,
) -> dict:
    merged: dict = {}
    for v in (
        await db.execute(select(Variable).where(Variable.scope == "global"))
    ).scalars().all():
        merged[v.name] = v.value
    for v in (
        await db.execute(
            select(Variable).where(Variable.scope == "project", Variable.project_id == execution.project_id)
        )
    ).scalars().all():
        merged[v.name] = v.value
    if case is not None and case.variables:
        merged.update(case.variables)
    if execution.type == "suite" and execution.suite_id is not None:
        for v in (
            await db.execute(
                select(Variable).where(Variable.scope == "suite", Variable.suite_id == execution.suite_id)
            )
        ).scalars().all():
            merged[v.name] = v.value
    merged.update((execution.parameters or {}).get("variables") or {})
    return merged


# ---------- 快照（§10.3：元素定位快照） ----------


async def _collect_element_ids(steps: list, assertions: list) -> set[int]:
    ids: set[int] = set()
    for item in [*steps, *assertions]:
        element_id = item.get("element_id") if isinstance(item, dict) else None
        if element_id is not None:
            try:
                ids.add(int(element_id))
            except (TypeError, ValueError):
                continue
    return ids


async def build_case_snapshot(
    db: AsyncSession,
    case: TestCase,
    variable_map: dict,
    *,
    use_pre_steps: bool = False,
    use_post_steps: bool = False,
) -> dict:
    """构建不可变快照，并按运行选项选择前置/后置阶段。

    存储态的三个阶段各自独立排序；下发前按 setup → main → teardown
    重排为执行级唯一顺序，以兼容 execution_steps 唯一约束和实时消息。
    """
    stored_steps = render_value(deepcopy(case.steps or []), variable_map)
    selected_steps: list[dict] = []
    for phase in ("setup", "main", "teardown"):
        if phase == "setup" and not use_pre_steps:
            continue
        if phase == "teardown" and not use_post_steps:
            continue
        phase_steps = [
            step
            for step in stored_steps
            if isinstance(step, dict) and str(step.get("phase") or "main") == phase
        ]
        phase_steps.sort(key=lambda step: int(step.get("order") or 0))
        for step in phase_steps:
            selected_steps.append(
                {
                    **step,
                    "phase": phase,
                    "source_order": step.get("order"),
                    "order": len(selected_steps) + 1,
                }
            )
    steps = selected_steps
    assertions = render_value(deepcopy(case.assertions or []), variable_map)

    element_ids = await _collect_element_ids(steps, assertions)
    elements: dict = {}
    if element_ids:
        rows = (
            await db.execute(select(TestElement).where(TestElement.id.in_(element_ids)))
        ).scalars().all()
        for el in rows:
            elements[str(el.id)] = {
                "name": el.name,
                "platform": el.platform,
                "locator_type": el.locator_type,
                "locator_value": render_value(el.locator_value, variable_map),
            }
    return {"steps": steps, "assertions": assertions, "elements": elements}


async def _resolve_cases(db: AsyncSession, execution: Execution) -> list[TestCase]:
    if execution.type == "case" and execution.case_id is not None:
        case = await db.get(TestCase, execution.case_id)
        return [case] if case is not None else []
    suite_ids: list[int] = []
    if execution.type == "suite" and execution.suite_id is not None:
        suite_ids = [execution.suite_id]
    elif execution.type == "batch":
        suite_ids = (execution.parameters or {}).get("suite_ids") or []
    if not suite_ids:
        return []
    case_ids: list[int] = []
    for sid in suite_ids:
        rows = (
            await db.execute(
                select(TestSuiteCase.case_id)
                .where(TestSuiteCase.suite_id == sid)
                .order_by(TestSuiteCase.sort_order)
            )
        ).scalars().all()
        case_ids.extend(rows)
    case_ids = list(dict.fromkeys(case_ids))
    cases: list[TestCase] = []
    for cid in case_ids:
        case = await db.get(TestCase, cid)
        if case is not None:
            cases.append(case)
    return cases


async def create_execution_cases_from_execution(db: AsyncSession, execution: Execution) -> list[ExecutionCase]:
    # 幂等：先清除该执行已存在的快照（避免重复执行/重试导致重复行），Core delete 按依赖顺序执行
    existing = (
        await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == execution.id))
    ).scalars().all()
    for ec in existing:
        step_ids = (
            await db.execute(select(ExecutionStep.id).where(ExecutionStep.execution_case_id == ec.id))
        ).scalars().all()
        if step_ids:
            await db.execute(
                delete(ExecutionAssertion).where(ExecutionAssertion.execution_step_id.in_(step_ids))
            )
            await db.execute(delete(ExecutionStep).where(ExecutionStep.id.in_(step_ids)))
        await db.execute(delete(ExecutionCase).where(ExecutionCase.id == ec.id))
    await db.flush()

    cases = await _resolve_cases(db, execution)
    created: list[ExecutionCase] = []
    for case in cases:
        variable_map = await build_variable_map(db, execution, case)
        options = execution.parameters or {}
        snapshot = await build_case_snapshot(
            db,
            case,
            variable_map,
            use_pre_steps=bool(options.get("use_pre_steps")),
            use_post_steps=bool(options.get("use_post_steps")),
        )
        ec = ExecutionCase(
            execution_id=execution.id,
            case_id=case.id,
            case_name=case.name,
            module_name=None,
            status="pending",
            steps_snapshot=snapshot["steps"],
            assertions_snapshot=snapshot["assertions"],
            elements_snapshot=snapshot["elements"],
        )
        db.add(ec)
        created.append(ec)
    await db.commit()
    for ec in created:
        await db.refresh(ec)
    return created


# ---------- 队列认领（SKIP LOCKED） ----------


async def claim_next_queue(db: AsyncSession, worker_id: str) -> ExecutionQueue | None:
    stmt = (
        select(ExecutionQueue)
        .where(ExecutionQueue.status == "pending")
        .order_by(ExecutionQueue.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.status = "claimed"
    row.claimed_by = worker_id
    row.claimed_at = datetime.now(UTC)
    await db.commit()
    return row


# ---------- 设备原子抢占（§10.5） ----------


async def _lock_device(db: AsyncSession, device_id: int, execution_id: int) -> bool:
    result = await db.execute(
        update(Device)
        .where(
            Device.id == device_id,
            Device.status == "idle",
            Device.locked_by_execution.is_(None),
        )
        .values(status="busy", locked_by_execution=execution_id, updated_at=datetime.now(UTC))
        .returning(Device.id)
    )
    await db.commit()
    return result.scalar_one_or_none() is not None


async def select_and_lock_device(db: AsyncSession, execution: Execution) -> Device | None:
    """Windows 方案 §3.3：只认领执行指定的设备（device_id 必填），不再从全平台设备池随机选择。

    并发安全由 _lock_device 的条件 UPDATE（idle + 未锁）保证。
    """
    if execution.device_id is None:
        return None
    device = await db.get(Device, execution.device_id)
    if (
        device is not None
        and device.status == "idle"
        and device.locked_by_execution is None
    ):
        agent = await db.get(Agent, device.agent_id)
        if agent is not None and agent.status == "online":
            if await _lock_device(db, device.id, execution.id):
                return device
    return None


# ---------- 终态处理 ----------


def _add_log(db: AsyncSession, execution_id: int, level: str, message: str) -> None:
    db.add(ExecutionLog(execution_id=execution_id, level=level, message=message, source="worker"))


def _stop_grace_exceeded(execution: Execution) -> bool:
    """Windows 方案 §2：停止宽限期从 stop_requested_at（用户请求停止时刻）起算。

    无 stop_requested_at（历史数据/兼容路径）时回退旧口径：started_at + timeout + 宽限期。
    """
    now = datetime.now(UTC)
    if execution.stop_requested_at is not None:
        return (now - execution.stop_requested_at).total_seconds() > settings.execution_stop_grace_seconds
    if execution.started_at is None:
        return False
    return (now - execution.started_at).total_seconds() > (
        execution.timeout_seconds + settings.execution_stop_grace_seconds
    )


async def _mark_terminal(db: AsyncSession, execution: Execution, status_: str, message: str | None = None) -> None:
    """终态汇总（CR-10/CR-11）。

    - 条件更新：以 `finalized_at is null` 做最终汇总 CAS，只有一个调用方成为 finalizer；
      没有抢到 finalizer 的调用方立即返回（status 已终态的重复路径），避免重复汇总；
    - 状态归并：中断时当前 case 置 stopped/error、未执行步骤置 skipped、未执行 case 置 skipped；
    - 报告幂等：reports.execution_id 唯一 + on_conflict_do_nothing，重复汇总不会产生多份报告；
    - Windows 方案 §2：推进终态时写入 finalized_at（唯一终态汇总完成时刻）。
    """
    if message:
        _add_log(db, execution.id, "ERROR" if status_ == "error" else "INFO", message)
    now = datetime.now(UTC)
    status_ = status_ if status_ in TERMINAL_STATES else "error"

    # Step 6：最终汇总 CAS 以 `finalized_at is null` 为唯一所有权条件——
    # Agent 回传的终态只写 status/finished_at，谁先抢到 finalized_at 谁做汇总，
    # 未抢到的调用方立即返回，避免重复汇总。
    claimed = await db.execute(
        update(Execution)
        .where(Execution.id == execution.id, Execution.finalized_at.is_(None))
        .values(status=status_, finished_at=now, finalized_at=now)
        .returning(Execution.id)
    )
    await db.flush()
    if claimed.scalar_one_or_none() is None:
        # 已被其他调用方汇总：此调用方不重复汇总
        await db.rollback()
        return
    execution.status = status_
    execution.finished_at = now
    execution.finalized_at = now
    if execution.started_at is not None:
        execution.duration = int((now - execution.started_at).total_seconds() * 1000)

    cases = (
        await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == execution.id))
    ).scalars().all()
    for c in cases:
        if c.status in ("pending", "running"):
            steps = (
                await db.execute(select(ExecutionStep).where(ExecutionStep.execution_case_id == c.id))
            ).scalars().all()
            assertions = (
                await db.execute(
                    select(ExecutionAssertion)
                    .join(ExecutionStep, ExecutionAssertion.execution_step_id == ExecutionStep.id)
                    .where(ExecutionStep.execution_case_id == c.id)
                )
            ).scalars().all()
            failed = any(s.status == "failed" for s in steps) or any(
                a.status in ("fail", "failed") for a in assertions
            )
            if failed:
                c.status = "failed"
            elif status_ == "passed":
                c.status = "passed"
            elif steps or assertions:
                # CR-11：已有执行痕迹但被中断 → 当前项 stopped/error
                c.status = "stopped" if status_ in ("stopped", "cancelled") else "error"
                c.error_message = c.error_message or f"执行被中断（{status_}）"
                # 未执行步骤标记 skipped
                await db.execute(
                    update(ExecutionStep)
                    .where(
                        ExecutionStep.execution_case_id == c.id,
                        ExecutionStep.status == "pending",
                    )
                    .values(status="skipped")
                )
            else:
                c.status = "skipped"

    total = len(cases)
    passed = sum(1 for c in cases if c.status == "passed")
    failed = sum(1 for c in cases if c.status == "failed")
    error_count = sum(1 for c in cases if c.status == "error")
    skipped = sum(1 for c in cases if c.status == "skipped")
    # CR-10：DECIMAL(5,2)（如 66.67）；0/0 → 0
    rate = Decimal(str(round(passed / total * 100, 2))) if total else Decimal("0")
    await db.execute(
        pg_insert(Report)
        .values(
            execution_id=execution.id,
            total=total,
            passed=passed,
            failed=failed,
            error_count=error_count,
            skipped=skipped,
            success_rate=rate,
            duration=execution.duration,
        )
        .on_conflict_do_nothing(constraint="uq_reports_execution_id")
    )
    if execution.device_id is not None:
        await db.execute(
            update(Device)
            .where(Device.id == execution.device_id, Device.locked_by_execution == execution.id)
            .values(status="idle", locked_by_execution=None, updated_at=now)
        )
    await db.execute(
        update(ExecutionQueue).where(ExecutionQueue.execution_id == execution.id).values(status="done")
    )
    await db.commit()


async def _default_agent_sender(agent_id: int, payload: dict) -> bool:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{settings.backend_base_url}/internal/ws/agents/{agent_id}/send",
            json=payload,
            headers={"X-Internal-Token": settings.internal_token},
        )
        return resp.status_code in (200, 202)


async def run_execution(
    db: AsyncSession,
    execution_id: int,
    worker_id: str,
    *,
    agent_sender: Callable[[int, dict], Awaitable[bool]] | None = None,
    poll_interval: float = 5.0,
) -> None:
    agent_sender = agent_sender or _default_agent_sender
    execution = await db.get(Execution, execution_id)
    if execution is None:
        return

    # CR-12：原子认领 queued → running，杜绝与「取消 queued」竞争；
    # rowcount 为 0 说明已被取消/已非排队态，直接结束队列项。
    claimed = await db.execute(
        update(Execution)
        .where(Execution.id == execution_id, Execution.status == "queued")
        .values(
            status="running",
            session_token=secrets.token_urlsafe(32),
            started_at=datetime.now(UTC),
        )
        .returning(Execution.id)
    )
    await db.commit()
    if claimed.scalar_one_or_none() is None:
        logger.info("[%s] execution=%s 已非 queued（可能已被取消），跳过", worker_id, execution_id)
        await db.execute(
            update(ExecutionQueue)
            .where(ExecutionQueue.execution_id == execution_id)
            .values(status="done")
        )
        await db.commit()
        return
    await db.refresh(execution)

    try:
        await create_execution_cases_from_execution(db, execution)
    except ValueError as exc:
        await _mark_terminal(db, execution, "error", f"变量渲染失败: {exc}")
        return

    device = await select_and_lock_device(db, execution)
    if device is None:
        await _mark_terminal(db, execution, "error", "无可用的在线 Agent/设备")
        return

    execution.device_id = device.id
    await db.commit()
    logger.info("[%s] execution=%s 开始执行，设备=%s", worker_id, execution.id, device.name)

    case_rows = (
        await db.execute(
            select(ExecutionCase)
            .where(ExecutionCase.execution_id == execution.id)
            .order_by(ExecutionCase.id)
        )
    ).scalars().all()
    payload_cases = [
        {
            "execution_case_id": c.id,
            "case_id": c.case_id,
            "case_name": c.case_name,
            "module_name": c.module_name,
            "steps_snapshot": c.steps_snapshot,
            "assertions_snapshot": c.assertions_snapshot,
            "elements_snapshot": c.elements_snapshot,
        }
        for c in case_rows
    ]
    ok = await agent_sender(
        device.agent_id,
        {
            "type": "start_test",
            "execution_id": execution.id,
            "session_token": execution.session_token,
            "parameters": execution.parameters,
            "protocol_version": protocol_version(),
            "device": {"udid": device.udid, "platform": device.platform},
            "cases": payload_cases,
        },
    )
    if not ok:
        await _mark_terminal(db, execution, "error", "Agent 不在线或未连接 WS，无法开始执行")
        return

    # Agent 经 WS 回传状态由 FastAPI 落库（§10.1）；Worker 轮询终态
    while True:
        current = await db.get(Execution, execution.id, populate_existing=True)
        if current is None:
            return
        if current.status in TERMINAL_STATES:
            await _mark_terminal(db, current, current.status)
            return
        if current.status == "stopping":
            await agent_sender(device.agent_id, {"type": "stop_test", "execution_id": execution.id})
            # Windows 方案 §2：停止宽限期从 stop_requested_at 起算，超时强制终态（与 timeout_scan 同口径）
            if _stop_grace_exceeded(current):
                await _mark_terminal(
                    db, current, "stopped",
                    f"停止宽限期超时（>{settings.execution_stop_grace_seconds}s），强制结束",
                )
                return
        await asyncio.sleep(poll_interval)


# ---------- 扫描任务（仅 worker-001 启用） ----------


async def reclaim_stale_claimed(db: AsyncSession, stale_minutes: int = 10) -> None:
    threshold = datetime.now(UTC) - timedelta(minutes=stale_minutes)
    rows = (
        await db.execute(
            select(ExecutionQueue).where(
                ExecutionQueue.status == "claimed",
                ExecutionQueue.claimed_at < threshold,
            )
        )
    ).scalars().all()
    for row in rows:
        execution = await db.get(Execution, row.execution_id)
        if execution is not None and execution.status == "running":
            continue
        if row.retry_count >= 2:
            row.status = "failed"
            if execution is not None and execution.status == "queued":
                await _mark_terminal(db, execution, "error", "队列认领超时，重试次数超限")
                continue
        else:
            row.retry_count += 1
            row.status = "pending"
            row.claimed_by = None
            row.claimed_at = None
    await db.commit()


async def timeout_scan(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    active = (
        await db.execute(select(Execution).where(Execution.status.in_(["running", "stopping"])))
    ).scalars().all()
    for execution in active:
        if execution.started_at is None and execution.stop_requested_at is None:
            continue
        if execution.status == "stopping":
            # Windows 方案 §2：stopping 宽限期从 stop_requested_at 起算，到点强制 stopped
            if _stop_grace_exceeded(execution):
                await _mark_terminal(
                    db, execution, "stopped",
                    f"停止宽限期超时（>{settings.execution_stop_grace_seconds}s），强制结束",
                )
        elif execution.started_at is not None:
            elapsed = (now - execution.started_at).total_seconds()
            if elapsed > execution.timeout_seconds:
                await _mark_terminal(db, execution, "error", f"执行超时（>{execution.timeout_seconds}s）")


async def finalize_unfinished_terminal(db: AsyncSession) -> None:
    """Step 6：恢复扫描——terminal 且 `finalized_at is null` 的执行调用 Worker 终态汇总。

    覆盖 Agent 回传终态后进程崩溃、或 release 返回 finalization_pending 的场景；
    具体汇总由 _mark_terminal 的 `finalized_at is null` CAS 保证只执行一次。
    """
    rows = (
        await db.execute(
            select(Execution).where(
                Execution.status.in_(TERMINAL_STATES),
                Execution.finalized_at.is_(None),
            )
        )
    ).scalars().all()
    for execution in rows:
        try:
            await _mark_terminal(db, execution, execution.status)
            logger.info("恢复汇总 terminal execution=%s (%s)", execution.id, execution.status)
        except Exception:
            logger.exception("恢复汇总 execution=%s 失败", execution.id)


async def agent_heartbeat_scan(db: AsyncSession) -> None:
    threshold = datetime.now(UTC) - timedelta(seconds=settings.agent_heartbeat_timeout)
    # last_heartbeat IS NULL：从未心跳或数据异常，同样视为失联（NULL < threshold 恒为假）
    stale_agents = (
        await db.execute(
            select(Agent).where(
                Agent.status == "online",
                Agent.deleted_at.is_(None),
                or_(Agent.last_heartbeat.is_(None), Agent.last_heartbeat < threshold),
            )
        )
    ).scalars().all()
    for agent in stale_agents:
        agent.status = "offline"
        devices = (
            await db.execute(select(Device).where(Device.agent_id == agent.id))
        ).scalars().all()
        for device in devices:
            if device.locked_by_execution is not None:
                execution = await db.get(Execution, device.locked_by_execution)
                if execution is not None and execution.status == "running":
                    await _mark_terminal(db, execution, "error", "Agent 心跳超时失联")
                elif execution is not None and execution.status == "stopping":
                    # CR-06：失联的 stopping 同样要终结，避免永久卡住
                    await _mark_terminal(db, execution, "stopped", "Agent 心跳超时失联（停止中）")
            device.status = "idle"
            device.locked_by_execution = None
    await db.commit()
