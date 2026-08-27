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
    ExecutionExclusion,
    ExecutionLog,
    ExecutionQueue,
    ExecutionStep,
    ExecutionSuite,
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


def min_agent_version() -> str:
    """Agent 注册准入的语义化最低版本，来自 Registry 生成产物（单一来源）。"""
    try:
        with _PROTOCOL_JSON.open("r", encoding="utf-8") as fh:
            return str(json.load(fh).get("min_agent_version", "0.0.0"))
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
            elements[str(el.id)] = _element_snapshot(el, variable_map)
    return {"steps": steps, "assertions": assertions, "elements": elements}


def _element_snapshot(el: TestElement, variables: dict) -> dict:
    """构造单元素执行快照：普通定位渲染变量；smart 定位 locator_config 原样透传。

    smart 定位的 locator_config 不能调用 render_value：其中可能包含 ${device_name}
    等运行时占位符，后端求值会报未定义变量，必须 raw 透传给 Agent。
    """
    if el.locator_type == "smart":
        return {
            "name": el.name,
            "platform": el.platform,
            "locator_type": "smart",
            "locator_config": el.locator_config or None,
            "locator_value": None,
        }
    return {
        "name": el.name,
        "platform": el.platform,
        "locator_type": el.locator_type,
        "locator_config": None,
        "locator_value": render_value(el.locator_value, variables),
    }


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


_LEGACY_PHASE_MAP = {"setup": "case_setup", "main": "case_main", "teardown": "case_teardown"}


def _legacy_phase_to_exec_phase(phase: str | None) -> str:
    return _LEGACY_PHASE_MAP.get(str(phase or "main"), "case_main")


async def _rebuild_legacy_snapshot(db: AsyncSession, execution: Execution) -> list[ExecutionCase]:
    """无档案执行：按套件级虚拟套件重建 V2 快照。

    - 先行清除旧快照（避免重复执行/重试产生重复行），按依赖顺序删除；
    - 旧扁平用例归入单个虚拟 ExecutionSuite（is_virtual=True、无套件前后置）；
    - 用例快照（steps/assertions/elements）落库，并预建步骤/断言 pending 行，
      确保所有 V2 消息都使用不可歧义的 execution_*_id。
    """
    existing_cases = (
        await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == execution.id))
    ).scalars().all()
    old_case_ids = [c.id for c in existing_cases]
    if old_case_ids:
        await db.execute(
            delete(ExecutionAssertion).where(ExecutionAssertion.execution_case_id.in_(old_case_ids))
        )
        await db.execute(delete(ExecutionStep).where(ExecutionStep.execution_case_id.in_(old_case_ids)))
    await db.execute(delete(ExecutionCase).where(ExecutionCase.execution_id == execution.id))
    await db.execute(delete(ExecutionStep).where(ExecutionStep.execution_suite_id.in_(
        select(ExecutionSuite.id).where(ExecutionSuite.execution_id == execution.id)
    )))
    await db.execute(delete(ExecutionSuite).where(ExecutionSuite.execution_id == execution.id))
    await db.flush()

    cases = await _resolve_cases(db, execution)
    if not cases:
        await db.commit()
        return []

    suite = ExecutionSuite(
        execution_id=execution.id,
        suite_id=None,
        suite_name="虚拟套件",
        suite_order=1,
        is_virtual=True,
        status="pending",
        setup_steps_snapshot=[],
        teardown_steps_snapshot=[],
        elements_snapshot={},
    )
    db.add(suite)
    await db.flush()

    created: list[ExecutionCase] = []
    for case_order, case in enumerate(cases, start=1):
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
            execution_suite_id=suite.id,
            case_id=case.id,
            case_name=case.name,
            module_name=None,
            case_order=case_order,
            status="pending",
            steps_snapshot=snapshot["steps"],
            assertions_snapshot=snapshot["assertions"],
            elements_snapshot=snapshot["elements"],
        )
        db.add(ec)
        await db.flush()
        for step in snapshot["steps"]:
            db.add(
                ExecutionStep(
                    execution_case_id=ec.id,
                    phase=_legacy_phase_to_exec_phase(step.get("phase")),
                    step_order=int(step.get("order") or 0),
                    action=step.get("action") or "",
                    source_key=step.get("source_key") or step.get("key"),
                    source_order=step.get("source_order"),
                    parameters=step.get("params") or {},
                    continue_on_failure=bool(step.get("continue_on_failure", False)),
                    status="pending",
                )
            )
        for assertion in snapshot["assertions"]:
            expected = assertion.get("expected")
            if expected is None:
                expected = assertion.get("expected_value")
            db.add(
                ExecutionAssertion(
                    execution_case_id=ec.id,
                    assertion_order=int(assertion.get("order") or 0),
                    assertion_type=assertion.get("type") or assertion.get("assertion_type") or "",
                    expected_value=str(expected) if expected is not None else None,
                    status="pending",
                )
            )
        created.append(ec)
    await db.commit()
    for ec in created:
        await db.refresh(ec)
    return created


async def create_execution_cases_from_execution(db: AsyncSession, execution: Execution) -> list[ExecutionCase]:
    # 方案 §6.1：档案执行的快照已在创建事务固化，Worker 直接消费，不重建。
    if execution.app_profile_id is not None:
        return list(
            (
                await db.execute(
                    select(ExecutionCase)
                    .where(ExecutionCase.execution_id == execution.id)
                    .order_by(ExecutionCase.execution_suite_id, ExecutionCase.case_order)
                )
            ).scalars().all()
        )
    # 兼容期旧执行（无档案）：改写为套件级（虚拟套件）重建
    return await _rebuild_legacy_snapshot(db, execution)


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


def _aggregate_status(statuses: list[str]) -> str:
    """套件/父节点终态汇总优先级：error > failed > stopped > skipped > passed。"""
    if "error" in statuses:
        return "error"
    if "failed" in statuses:
        return "failed"
    if "stopped" in statuses:
        return "stopped"
    if "skipped" in statuses:
        return "skipped"
    if statuses and all(s == "passed" for s in statuses):
        return "passed"
    if statuses:
        return "passed"
    return "skipped"


def _rate_percent(numerator: int, denominator: int) -> Decimal:
    """方案 §7.1：成功率仅按 passed/(passed+failed+error_count)，N/A 与停止 skipped 不入分母；分母 0 → 0。"""
    if denominator:
        return Decimal(str(round(numerator / denominator * 100, 2)))
    return Decimal("0")


async def _mark_terminal(db: AsyncSession, execution: Execution, status_: str, message: str | None = None) -> None:
    """终态汇总（CR-10/CR-11，套件级 / §2 三层统计）。

    - 条件更新：以 `finalized_at is null` 做最终汇总 CAS，只有一个调用方成为 finalizer；
      没有抢到 finalizer 的调用方立即返回（status 已终态的重复路径），避免重复汇总；
    - 状态归并（套件级）：ExecutionSuite / ExecutionCase 层次归并——套件按子节点优先级汇成终态，
      用例沿用旧逻辑（failed 有步骤/断言失败；passed 等；stopped/error 有执行痕迹；skipped 否则），
      未执行 ExecutionStep（pending 且父节点 pending/running）置 skipped；
    - 三层报告统计：total/passed/... 按用例；suite_* 按套件；step_* 含套件步骤与用例步骤；
      not_applicable_suites 统计 target_type == 'suite' 排除项；exclusion_summary 增加 na_suite_steps；
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

    suites = (
        await db.execute(
            select(ExecutionSuite)
            .where(ExecutionSuite.execution_id == execution.id)
            .order_by(ExecutionSuite.suite_order)
        )
    ).scalars().all()
    suite_ids = [s.id for s in suites]
    cases = (
        await db.execute(
            select(ExecutionCase)
            .where(ExecutionCase.execution_id == execution.id)
            .order_by(ExecutionCase.execution_suite_id, ExecutionCase.case_order)
        )
    ).scalars().all()
    case_ids = [c.id for c in cases]
    cases_by_suite: dict[int, list[ExecutionCase]] = {}
    for case in cases:
        cases_by_suite.setdefault(case.execution_suite_id, []).append(case)

    # ---------- 用例状态归并（沿用既有逻辑；CR-11 修正：快照预建行不算"开始"信号） ----------
    for c in cases:
        if c.status in ("pending", "running"):
            steps = (
                await db.execute(select(ExecutionStep).where(ExecutionStep.execution_case_id == c.id))
            ).scalars().all()
            assertions = (
                await db.execute(
                    select(ExecutionAssertion).where(ExecutionAssertion.execution_case_id == c.id)
                )
            ).scalars().all()
            failed = any(s.status == "failed" for s in steps) or any(
                a.status in ("fail", "failed") for a in assertions
            )
            if failed:
                c.status = "failed"
            elif status_ == "passed":
                c.status = "passed"
            # 用例是否真正开始过：started_at / status=running / 任一子行非 pending
            # （快照在创建时已预建全部 pending 步骤断言行，行存在≠开始过）
            elif c.started_at is not None or c.status == "running" or any(
                s.status != "pending" for s in steps
            ) or any(a.status != "pending" for a in assertions):
                c.status = "stopped" if status_ in ("stopped", "cancelled") else "error"
                c.error_message = c.error_message or f"执行被中断（{status_}）"
            else:
                c.status = "skipped"
        # 未执行断言（pending）置 skipped（隶属用例）
        await db.execute(
            update(ExecutionAssertion)
            .where(ExecutionAssertion.execution_case_id == c.id, ExecutionAssertion.status == "pending")
            .values(status="skipped")
        )
        # 未执行用例步骤（pending）置 skipped（父节点 pending/running → 已归并，未执行即 skipped）
        await db.execute(
            update(ExecutionStep)
            .where(ExecutionStep.execution_case_id == c.id, ExecutionStep.status == "pending")
            .values(status="skipped")
        )

    # ---------- 套件状态归并（按子节点优先级） ----------
    for suite in suites:
        if suite.status not in ("pending", "running"):
            continue
        suite_steps = (
            await db.execute(select(ExecutionStep).where(ExecutionStep.execution_suite_id == suite.id))
        ).scalars().all()
        child_statuses = [s.status for s in suite_steps] + [
            c.status for c in cases_by_suite.get(suite.id, [])
        ]
        suite.status = _aggregate_status(child_statuses)
        if suite.status not in ("passed",):
            suite.error_message = suite.error_message or (
                f"执行被中断（{status_}）" if status_ in ("stopped", "cancelled") else None
            )
        # 未执行套件步骤（pending）置 skipped
        await db.execute(
            update(ExecutionStep)
            .where(ExecutionStep.execution_suite_id == suite.id, ExecutionStep.status == "pending")
            .values(status="skipped")
        )

    # ---------- 三步统计：用例 / 套件 / 步骤（含套件步骤与用例步骤） ----------
    total = len(cases)
    passed = sum(1 for c in cases if c.status == "passed")
    failed = sum(1 for c in cases if c.status == "failed")
    error_count = sum(1 for c in cases if c.status == "error")
    skipped = sum(1 for c in cases if c.status == "skipped")
    rate = _rate_percent(passed, passed + failed + error_count)

    suite_total = len(suites)
    suite_passed = sum(1 for s in suites if s.status == "passed")
    suite_failed = sum(1 for s in suites if s.status == "failed")
    suite_error_count = sum(1 for s in suites if s.status == "error")
    suite_skipped = sum(1 for s in suites if s.status == "skipped")
    suite_rate = _rate_percent(suite_passed, suite_passed + suite_failed + suite_error_count)

    all_steps: list[ExecutionStep] = []
    if case_ids:
        all_steps = (
            await db.execute(
                select(ExecutionStep)
                .where(ExecutionStep.execution_case_id.in_(case_ids))
            )
        ).scalars().all()
    if suite_ids:
        all_steps = [
            *all_steps,
            *(
                await db.execute(
                    select(ExecutionStep).where(ExecutionStep.execution_suite_id.in_(suite_ids))
                )
            ).scalars().all(),
        ]
    step_total = len(all_steps)
    step_passed = sum(1 for s in all_steps if s.status == "passed")
    step_failed = sum(1 for s in all_steps if s.status == "failed")
    step_error_count = sum(1 for s in all_steps if s.status == "error")
    step_skipped = sum(1 for s in all_steps if s.status == "skipped")
    step_rate = _rate_percent(step_passed, step_passed + step_failed + step_error_count)

    # ---------- 排除项统计：not_applicable（用例）与 not_applicable_suites（套件） ----------
    na_cases = 0
    na_suites = 0
    exclusion_summary: dict[str, int] = {
        "na_suites": 0, "na_cases": 0, "na_steps": 0, "na_assertions": 0, "na_suite_steps": 0,
    }
    exclusions = (
        await db.execute(select(ExecutionExclusion).where(ExecutionExclusion.execution_id == execution.id))
    ).scalars().all()
    for ex in exclusions:
        if ex.target_type == "case":
            na_cases += 1
        elif ex.target_type == "suite":
            na_suites += 1
        key = f"na_{ex.target_type}s"
        if key in exclusion_summary:
            exclusion_summary[key] += 1

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
            not_applicable=na_cases,
            exclusion_summary=exclusion_summary,
            suite_total=suite_total,
            suite_passed=suite_passed,
            suite_failed=suite_failed,
            suite_error_count=suite_error_count,
            suite_skipped=suite_skipped,
            suite_success_rate=suite_rate,
            step_total=step_total,
            step_passed=step_passed,
            step_failed=step_failed,
            step_error_count=step_error_count,
            step_skipped=step_skipped,
            step_success_rate=step_rate,
            not_applicable_suites=na_suites,
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


async def _build_suites_payload(db: AsyncSession, execution: Execution) -> list[dict]:
    """协议 V2：start_test 下发套件级结构（suites[] + execution_*_id）。

    - 以固化的 ExecutionSuite / ExecutionCase 为单元下发（含 execution_suite_id/execution_case_id）；
    - 套件前后置步骤读取已固化的 ExecutionStep（execution_suite_id 非空、phase 为
      suite_setup/suite_teardown），携带 execution_step_id 供 Agent 精确回传；
    - 用例步骤和断言均从预建行注入 execution_step_id/execution_assertion_id；缺行视为
      服务端快照不完整并立即失败，不再回退按顺序猜测或动态创建。
    """
    suites = (
        await db.execute(
            select(ExecutionSuite)
            .where(ExecutionSuite.execution_id == execution.id)
            .order_by(ExecutionSuite.suite_order)
        )
    ).scalars().all()
    if not suites:
        return []
    suite_ids = [s.id for s in suites]

    cases = (
        await db.execute(
            select(ExecutionCase)
            .where(ExecutionCase.execution_id == execution.id)
            .order_by(ExecutionCase.execution_suite_id, ExecutionCase.case_order)
        )
    ).scalars().all()
    cases_by_suite: dict[int, list[ExecutionCase]] = {}
    for c in cases:
        cases_by_suite.setdefault(c.execution_suite_id, []).append(c)

    # 预建步骤/断言：按父节点、阶段和顺序建立 id 映射，供 Agent 精确回传。
    _case_ids = [c.id for c in cases]
    _case_step_rows = (
        await db.execute(
            select(ExecutionStep)
            .where(ExecutionStep.execution_case_id.in_(_case_ids))
            .order_by(ExecutionStep.execution_case_id, ExecutionStep.phase, ExecutionStep.step_order)
        )
    ).scalars().all()
    step_id_by_key: dict[tuple[int, str, int], int] = {
        (step.execution_case_id, step.phase, step.step_order): step.id
        for step in _case_step_rows
        if step.execution_case_id is not None
    }
    _assertion_rows = (
        await db.execute(
            select(ExecutionAssertion)
            .where(ExecutionAssertion.execution_case_id.in_(_case_ids))
            .order_by(ExecutionAssertion.execution_case_id, ExecutionAssertion.assertion_order)
        )
    ).scalars().all()
    assertion_id_by_key: dict[tuple[int, int], int] = {
        (a.execution_case_id, a.assertion_order): a.id for a in _assertion_rows
    }

    def _inject_step_ids(case: ExecutionCase, steps: list) -> list:
        out: list[dict] = []
        for raw_step in steps:
            entry = dict(raw_step)
            order = int(entry.get("order") or 0)
            phase = str(entry.get("phase") or "case_main")
            if phase in _LEGACY_PHASE_MAP:
                phase = _legacy_phase_to_exec_phase(phase)
            step_id = step_id_by_key.get((case.id, phase, order))
            if step_id is None:
                raise RuntimeError(
                    f"执行快照缺少步骤行 execution_case_id={case.id}, phase={phase}, order={order}"
                )
            entry["execution_step_id"] = step_id
            out.append(entry)
        return out

    def _inject_assertion_ids(case: ExecutionCase, assertions: list) -> list:
        """向断言快照注入 execution_assertion_id；缺行说明快照物化不完整。"""
        out: list[dict] = []
        for a in assertions:
            entry = dict(a)
            order = int(entry.get("order") or 0)
            assertion_id = assertion_id_by_key.get((case.id, order))
            if assertion_id is None:
                raise RuntimeError(
                    f"执行快照缺少断言行 execution_case_id={case.id}, order={order}"
                )
            entry["execution_assertion_id"] = assertion_id
            out.append(entry)
        return out

    suite_steps = (
        await db.execute(
            select(ExecutionStep)
            .where(
                ExecutionStep.execution_suite_id.in_(suite_ids),
                ExecutionStep.phase.in_(("suite_setup", "suite_teardown")),
            )
            .order_by(ExecutionStep.execution_suite_id, ExecutionStep.phase, ExecutionStep.step_order)
        )
    ).scalars().all()
    steps_by_suite: dict[int, list[ExecutionStep]] = {}
    for st in suite_steps:
        steps_by_suite.setdefault(st.execution_suite_id, []).append(st)

    payload_suites: list[dict] = []
    for s in suites:
        setup_steps = [
            {
                "execution_step_id": st.id,
                "action": st.action,
                "order": st.step_order,
                "params": st.parameters or {},
                "source_key": st.source_key,
                "source_order": st.source_order,
                "continue_on_failure": st.continue_on_failure,
            }
            for st in steps_by_suite.get(s.id, [])
            if st.phase == "suite_setup"
        ]
        teardown_steps = [
            {
                "execution_step_id": st.id,
                "action": st.action,
                "order": st.step_order,
                "params": st.parameters or {},
                "source_key": st.source_key,
                "source_order": st.source_order,
                "continue_on_failure": st.continue_on_failure,
            }
            for st in steps_by_suite.get(s.id, [])
            if st.phase == "suite_teardown"
        ]
        suite_cases = [
            {
                "execution_case_id": c.id,
                "case_id": c.case_id,
                "case_name": c.case_name,
                "case_order": c.case_order,
                "module_name": c.module_name,
                "steps_snapshot": _inject_step_ids(c, c.steps_snapshot or []),
                "assertions_snapshot": _inject_assertion_ids(c, c.assertions_snapshot or []),
                "elements_snapshot": c.elements_snapshot,
            }
            for c in cases_by_suite.get(s.id, [])
        ]
        payload_suites.append(
            {
                "execution_suite_id": s.id,
                "suite_id": s.suite_id,
                "suite_name": s.suite_name,
                "suite_order": s.suite_order,
                "is_virtual": s.is_virtual,
                "elements_snapshot": s.elements_snapshot or {},
                "setup_steps": setup_steps,
                "cases": suite_cases,
                "teardown_steps": teardown_steps,
            }
        )
    return payload_suites


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

    payload_suites = await _build_suites_payload(db, execution)
    ok = await agent_sender(
        device.agent_id,
        {
            "type": "start_test",
            "execution_id": execution.id,
            "session_token": execution.session_token,
            "parameters": execution.parameters,
            "protocol_version": protocol_version(),
            "device": {"udid": device.udid, "platform": device.platform},
            "suites": payload_suites,
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
