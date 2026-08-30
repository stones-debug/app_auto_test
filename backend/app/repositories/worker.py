import json
import logging
import re
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

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
    TestSuite,
    TestSuiteCase,
    Variable,
)
from app.services.execution_summary import (
    CaseStatusInput,
    aggregate_statuses,
    compute_counts,
    compute_exclusion_summary,
    merge_case_status,
    merge_execution_status,
    rate_percent,
)

logger = logging.getLogger("worker")

_VAR_RE = re.compile(r"\$\{(\w+)\}")
TERMINAL_STATES = {"passed", "failed", "error", "stopped", "cancelled"}
_PHASE_MAP = {"setup": "case_setup", "main": "case_main", "teardown": "case_teardown"}


def _numeric_order(value: object) -> int:
    return int(str(value)) if isinstance(value, (int, float, str)) else 0
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


async def _collect_element_ids(steps: list) -> set[int]:
    ids: set[int] = set()
    assertions = [a for step in steps for a in (step.get("assertions") or [])]
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
        phase_steps.sort(key=lambda step: _numeric_order(step.get("order")))
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
    element_ids = await _collect_element_ids(steps)
    elements: dict = {}
    if element_ids:
        rows = (
            await db.execute(select(TestElement).where(TestElement.id.in_(element_ids)))
        ).scalars().all()
        for el in rows:
            elements[str(el.id)] = _element_snapshot(el, variable_map)
    return {"steps": steps, "elements": elements}


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


async def _clear_execution_tree(db: AsyncSession, execution_id: int) -> None:
    existing_cases = (
        await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == execution_id))
    ).scalars().all()
    old_case_ids = [c.id for c in existing_cases]
    if old_case_ids:
        old_step_ids = select(ExecutionStep.id).where(ExecutionStep.execution_case_id.in_(old_case_ids))
        await db.execute(delete(ExecutionAssertion).where(ExecutionAssertion.execution_step_id.in_(old_step_ids)))
        await db.execute(delete(ExecutionStep).where(ExecutionStep.execution_case_id.in_(old_case_ids)))
    await db.execute(delete(ExecutionCase).where(ExecutionCase.execution_id == execution_id))
    await db.execute(delete(ExecutionStep).where(ExecutionStep.execution_suite_id.in_(
        select(ExecutionSuite.id).where(ExecutionSuite.execution_id == execution_id)
    )))
    await db.execute(delete(ExecutionSuite).where(ExecutionSuite.execution_id == execution_id))
    await db.flush()


def _suite_step_snapshots(nodes: list, variable_map: dict, phase: str) -> list[dict]:
    rendered = render_value(deepcopy(nodes or []), variable_map)
    snapshots: list[dict] = []
    for node in sorted(
        (item for item in rendered if isinstance(item, dict)),
        key=lambda item: _numeric_order(item.get("order")),
    ):
        snapshots.append({
            **node,
            "phase": phase,
            "source_order": node.get("order"),
            "order": len(snapshots) + 1,
        })
    return snapshots


async def _materialize_unprofiled_tree(db: AsyncSession, execution: Execution) -> list[ExecutionCase]:
    """无档案执行也按真实套件关系物化，保留套件前后置和重复上下文。"""
    await _clear_execution_tree(db, execution.id)
    parameters = execution.parameters or {}
    if execution.type == "case" and execution.case_id is not None:
        suite_specs = [(None, [execution.case_id])]
    elif execution.type == "suite" and execution.suite_id is not None:
        suite_specs = [(execution.suite_id, None)]
    else:
        suite_specs = [(int(sid), None) for sid in parameters.get("suite_ids") or []]

    created: list[ExecutionCase] = []
    for suite_order, (suite_id, selected_case_ids) in enumerate(suite_specs, start=1):
        suite = await db.get(TestSuite, suite_id) if suite_id is not None else None
        if suite_id is not None and (suite is None or suite.deleted_at is not None):
            continue
        suite_variable_map = await build_variable_map(db, execution)
        if suite is None:
            suite_name = "虚拟套件"
            setup_snapshot: list[dict] = []
            teardown_snapshot: list[dict] = []
        else:
            suite_name = suite.name
            suite_variables = (
                await db.execute(
                    select(Variable).where(Variable.scope == "suite", Variable.suite_id == suite.id)
                )
            ).scalars().all()
            suite_variable_map.update({item.name: item.value for item in suite_variables})
            setup_snapshot = _suite_step_snapshots(suite.setup_steps, suite_variable_map, "suite_setup")
            teardown_snapshot = _suite_step_snapshots(suite.teardown_steps, suite_variable_map, "suite_teardown")

        suite_steps = [*setup_snapshot, *teardown_snapshot]
        suite_element_ids = await _collect_element_ids(suite_steps)
        suite_elements: dict = {}
        if suite_element_ids:
            suite_element_rows = (
                await db.execute(select(TestElement).where(TestElement.id.in_(suite_element_ids)))
            ).scalars().all()
            for element in suite_element_rows:
                suite_elements[str(element.id)] = _element_snapshot(element, suite_variable_map)

        exec_suite = ExecutionSuite(
            execution_id=execution.id, suite_id=suite_id, suite_name=suite_name,
            suite_order=suite_order, is_virtual=suite_id is None, status="pending",
            setup_steps_snapshot=setup_snapshot, teardown_steps_snapshot=teardown_snapshot,
            elements_snapshot=suite_elements,
        )
        db.add(exec_suite)
        await db.flush()
        for step in [*setup_snapshot, *teardown_snapshot]:
            db.add(ExecutionStep(
                execution_suite_id=exec_suite.id, phase=step["phase"],
                step_order=int(step.get("order") or 0), action=step.get("action") or "",
                source_key=step.get("source_key") or step.get("key"), source_order=step.get("source_order"),
                parameters=step.get("params") or {}, continue_on_failure=bool(step.get("continue_on_failure", False)),
                status="pending",
            ))

        if selected_case_ids is None:
            selected_case_ids = list((await db.execute(
                select(TestSuiteCase.case_id).where(TestSuiteCase.suite_id == suite_id).order_by(TestSuiteCase.sort_order)
            )).scalars().all())
        for case_order, case_id in enumerate(selected_case_ids, start=1):
            case = await db.get(TestCase, case_id)
            if case is None or case.deleted_at is not None:
                continue
            variable_map = dict(suite_variable_map)
            # 批量执行会逐套件物化；套件变量位于项目/全局之上，
            # 但不能覆盖当前用例变量或执行参数。
            variable_map.update(case.variables or {})
            variable_map.update(parameters.get("variables") or {})
            snapshot = await build_case_snapshot(
                db, case, variable_map,
                use_pre_steps=bool(parameters.get("use_pre_steps")),
                use_post_steps=bool(parameters.get("use_post_steps")),
            )
            exec_case = ExecutionCase(
                execution_id=execution.id, execution_suite_id=exec_suite.id, case_id=case.id,
                case_name=case.name, module_name=None, case_order=case_order, status="pending",
                steps_snapshot=snapshot["steps"], elements_snapshot=snapshot["elements"],
            )
            db.add(exec_case)
            await db.flush()
            for step in snapshot["steps"]:
                exec_step = ExecutionStep(
                    execution_case_id=exec_case.id, phase={"setup": "case_setup", "main": "case_main", "teardown": "case_teardown"}.get(str(step.get("phase")), "case_main"),
                    step_order=int(step.get("order") or 0), action=step.get("action") or "",
                    source_key=step.get("source_key") or step.get("key"), source_order=step.get("source_order"),
                    parameters=step.get("params") or {}, continue_on_failure=bool(step.get("continue_on_failure", False)), status="pending",
                )
                db.add(exec_step)
                await db.flush()
                for assertion in step.get("assertions") or []:
                    expected = assertion.get("expected") or (assertion.get("params") or {}).get("expected")
                    db.add(ExecutionAssertion(
                        execution_step_id=exec_step.id, assertion_order=int(assertion.get("order") or 0),
                        assertion_type=assertion.get("type") or assertion.get("assertion_type") or "",
                        expected_value=str(expected) if expected is not None else None, status="pending",
                    ))
            created.append(exec_case)
    await db.flush()
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
    return await _materialize_unprofiled_tree(db, execution)


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
    await db.flush()
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
    await db.flush()
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
    """兼容旧调用方；实际规则位于 execution_summary 纯函数模块。"""
    return aggregate_statuses(statuses)


def _normalize_stuck_status(child_status: str, execution_status: str) -> str:
    """Step 7.3：终态汇总前把残留 pending/running 子节点按"是否执行过"归并。

    - pending：从未执行 → skipped；
    - running：执行被打断 → stopped（执行为 stopped/cancelled）或 error；
    - 其余状态原样保留。
    """
    if child_status == "pending":
        return "skipped"
    if child_status == "running":
        return "stopped" if execution_status in ("stopped", "cancelled") else "error"
    return child_status


def _rate_percent(numerator: int, denominator: int) -> Decimal:
    """兼容旧调用方；实际规则位于 execution_summary 纯函数模块。"""
    return rate_percent(numerator, denominator)


async def _load_terminal_children(
    db: AsyncSession,
    suite_ids: list[int],
    case_ids: list[int],
) -> tuple[
    list[ExecutionStep],
    dict[int, list[ExecutionStep]],
    dict[int, list[ExecutionStep]],
    list[ExecutionAssertion],
    dict[int, list[ExecutionAssertion]],
]:
    """批量加载终态汇总所需的步骤/断言，并按父节点建立内存索引。"""
    all_steps: list[ExecutionStep] = []
    if suite_ids or case_ids:
        step_filters = []
        if suite_ids:
            step_filters.append(ExecutionStep.execution_suite_id.in_(suite_ids))
        if case_ids:
            step_filters.append(ExecutionStep.execution_case_id.in_(case_ids))
        all_steps = list(
            (
                await db.execute(select(ExecutionStep).where(or_(*step_filters)))
            ).scalars().all()
        )

    case_steps_by_case: dict[int, list[ExecutionStep]] = {}
    suite_steps_by_suite: dict[int, list[ExecutionStep]] = {}
    steps_by_id: dict[int, ExecutionStep] = {}
    for step in all_steps:
        steps_by_id[step.id] = step
        if step.execution_case_id is not None:
            case_steps_by_case.setdefault(step.execution_case_id, []).append(step)
        elif step.execution_suite_id is not None:
            suite_steps_by_suite.setdefault(step.execution_suite_id, []).append(step)

    all_assertions: list[ExecutionAssertion] = []
    if steps_by_id:
        all_assertions = list(
            (
                await db.execute(
                    select(ExecutionAssertion).where(
                        ExecutionAssertion.execution_step_id.in_(steps_by_id.keys())
                    )
                )
            ).scalars().all()
        )
    assertions_by_case: dict[int, list[ExecutionAssertion]] = {}
    for assertion in all_assertions:
        step = steps_by_id.get(assertion.execution_step_id)
        if step is not None and step.execution_case_id is not None:
            assertions_by_case.setdefault(step.execution_case_id, []).append(assertion)

    return all_steps, case_steps_by_case, suite_steps_by_suite, all_assertions, assertions_by_case


async def _merge_cases_and_skip_pending(
    db: AsyncSession,
    cases: Sequence[ExecutionCase],
    case_steps_by_case: dict[int, list[ExecutionStep]],
    assertions_by_case: dict[int, list[ExecutionAssertion]],
    all_steps: list[ExecutionStep],
    all_assertions: list[ExecutionAssertion],
    terminal_status: str,
) -> None:
    """归并用例状态，并批量收敛用例下的 pending 步骤/断言。"""
    pending_case_step_ids: set[int] = set()
    pending_assertion_ids: set[int] = set()
    for case in cases:
        should_merge = case.status in ("pending", "running")
        steps = case_steps_by_case.get(case.id, [])
        assertions = assertions_by_case.get(case.id, [])
        merged_case = merge_case_status(
            CaseStatusInput(
                status=case.status,
                started=case.started_at is not None if should_merge else False,
                step_statuses=tuple(step.status for step in steps) if should_merge else (),
                assertion_statuses=tuple(assertion.status for assertion in assertions)
                if should_merge
                else (),
                error_message=case.error_message,
            ),
            terminal_status,
        )
        case.status = merged_case.status
        case.error_message = merged_case.error_message
        pending_assertion_ids.update(
            assertion.id for assertion in assertions if assertion.status == "pending"
        )
        pending_case_step_ids.update(step.id for step in steps if step.status == "pending")

    if pending_assertion_ids:
        for assertion in all_assertions:
            if assertion.id in pending_assertion_ids:
                assertion.status = "skipped"
        await db.execute(
            update(ExecutionAssertion)
            .where(ExecutionAssertion.id.in_(pending_assertion_ids))
            .values(status="skipped")
        )
    if pending_case_step_ids:
        for step in all_steps:
            if step.id in pending_case_step_ids:
                step.status = "skipped"
        await db.execute(
            update(ExecutionStep)
            .where(ExecutionStep.id.in_(pending_case_step_ids))
            .values(status="skipped")
        )


async def _merge_suites_and_skip_pending(
    db: AsyncSession,
    suites: Sequence[ExecutionSuite],
    cases_by_suite: dict[int, list[ExecutionCase]],
    suite_steps_by_suite: dict[int, list[ExecutionStep]],
    all_steps: list[ExecutionStep],
    terminal_status: str,
) -> None:
    """按套件子节点优先级归并套件，并批量收敛套件下的 pending 步骤。"""
    pending_suite_step_ids: set[int] = set()
    for suite in suites:
        if suite.status not in ("pending", "running"):
            continue
        suite_was_running = suite.status == "running"
        suite_steps = suite_steps_by_suite.get(suite.id, [])
        child_statuses = [
            _normalize_stuck_status(step.status, terminal_status) for step in suite_steps
        ] + [case.status for case in cases_by_suite.get(suite.id, [])]
        merged = _aggregate_status(child_statuses)
        if (
            merged == "skipped"
            and suite_was_running
            and terminal_status in ("stopped", "cancelled")
        ):
            merged = "stopped"
        suite.status = merged
        if suite.status not in ("passed",):
            suite.error_message = suite.error_message or (
                f"执行被中断（{terminal_status}）"
                if terminal_status in ("stopped", "cancelled")
                else None
            )
        pending_suite_step_ids.update(
            step.id for step in suite_steps if step.status == "pending"
        )

    if pending_suite_step_ids:
        for step in all_steps:
            if step.id in pending_suite_step_ids:
                step.status = "skipped"
        await db.execute(
            update(ExecutionStep)
            .where(ExecutionStep.id.in_(pending_suite_step_ids))
            .values(status="skipped")
        )


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
        await db.flush()
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

    all_steps, case_steps_by_case, suite_steps_by_suite, all_assertions, assertions_by_case = (
        await _load_terminal_children(db, suite_ids, case_ids)
    )
    await _merge_cases_and_skip_pending(
        db,
        cases,
        case_steps_by_case,
        assertions_by_case,
        all_steps,
        all_assertions,
        status_,
    )
    await _merge_suites_and_skip_pending(
        db,
        suites,
        cases_by_suite,
        suite_steps_by_suite,
        all_steps,
        status_,
    )

    # ---------- Step 7.3：报告落库前按已落库分层终态合并顶层执行状态 ----------
    # Agent/WS 上报的终态只作初值；顶层以已落库的套件/用例终态为准——
    # failed/stopped 不能覆盖已落库 error，passed 不能覆盖任何失败。
    # 执行级没有 skipped：合并输入只取 blocking 终态（error/failed/stopped）。
    stored_statuses = [s.status for s in suites] + [c.status for c in cases]
    merged_execution_status = merge_execution_status(status_, stored_statuses)
    if merged_execution_status != status_:
        _add_log(
            db,
            execution.id,
            "WARN",
            f"执行终态按已落库分层结果修正：{status_} → {merged_execution_status}",
        )
        status_ = merged_execution_status
        execution.status = status_

    # ---------- 三步统计：用例 / 套件 / 步骤（含套件步骤与用例步骤） ----------
    case_counts = compute_counts(tuple(c.status for c in cases))
    rate = rate_percent(case_counts.passed, case_counts.passed + case_counts.failed + case_counts.error_count)

    suite_counts = compute_counts(tuple(s.status for s in suites))
    suite_rate = rate_percent(
        suite_counts.passed,
        suite_counts.passed + suite_counts.failed + suite_counts.error_count,
    )

    step_counts = compute_counts(tuple(s.status for s in all_steps))
    step_rate = rate_percent(
        step_counts.passed,
        step_counts.passed + step_counts.failed + step_counts.error_count,
    )

    # ---------- 排除项统计：not_applicable（用例）与 not_applicable_suites（套件） ----------
    exclusions = (
        await db.execute(select(ExecutionExclusion).where(ExecutionExclusion.execution_id == execution.id))
    ).scalars().all()
    exclusion = compute_exclusion_summary(tuple(ex.target_type for ex in exclusions))

    await db.execute(
        pg_insert(Report)
        .values(
            execution_id=execution.id,
            total=case_counts.total,
            passed=case_counts.passed,
            failed=case_counts.failed,
            error_count=case_counts.error_count,
            skipped=case_counts.skipped,
            success_rate=rate,
            duration=execution.duration,
            not_applicable=exclusion.na_cases,
            exclusion_summary=exclusion.as_dict(),
            suite_total=suite_counts.total,
            suite_passed=suite_counts.passed,
            suite_failed=suite_counts.failed,
            suite_error_count=suite_counts.error_count,
            suite_skipped=suite_counts.skipped,
            suite_success_rate=suite_rate,
            step_total=step_counts.total,
            step_passed=step_counts.passed,
            step_failed=step_counts.failed,
            step_error_count=step_counts.error_count,
            step_skipped=step_counts.skipped,
            step_success_rate=step_rate,
            not_applicable_suites=exclusion.na_suites,
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
    await db.flush()


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
            .where(ExecutionAssertion.execution_step_id.in_([step.id for step in _case_step_rows]))
            .order_by(ExecutionAssertion.execution_step_id, ExecutionAssertion.assertion_order)
        )
    ).scalars().all()
    assertion_id_by_key: dict[tuple[int, int], int] = {
        (a.execution_step_id, a.assertion_order): a.id for a in _assertion_rows
    }

    def _inject_step_ids(case: ExecutionCase, steps: list) -> list:
        out: list[dict] = []
        for raw_step in steps:
            entry = dict(raw_step)
            order = int(entry.get("order") or 0)
            phase = str(entry.get("phase") or "case_main")
            if phase in _PHASE_MAP:
                phase = _PHASE_MAP[phase]
            step_id = step_id_by_key.get((case.id, phase, order))
            if step_id is None:
                raise RuntimeError(
                    f"执行快照缺少步骤行 execution_case_id={case.id}, phase={phase}, order={order}"
                )
            entry["execution_step_id"] = step_id
            injected_assertions: list[dict] = []
            for raw_assertion in entry.get("assertions") or []:
                assertion = dict(raw_assertion)
                assertion_order = int(assertion.get("order") or 0)
                assertion_id = assertion_id_by_key.get((step_id, assertion_order))
                if assertion_id is None:
                    raise RuntimeError(
                        f"执行快照缺少断言行 execution_step_id={step_id}, order={assertion_order}"
                    )
                assertion["execution_assertion_id"] = assertion_id
                injected_assertions.append(assertion)
            entry["assertions"] = injected_assertions
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
        if st.execution_suite_id is None:
            continue
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
    await db.flush()


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
    await db.flush()
