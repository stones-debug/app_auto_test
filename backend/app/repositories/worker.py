import json
import logging
import re
import secrets
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

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
    ExecutionNode,
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
from app.repositories import executions as executions_repo
from app.schemas.generated_case_params import ELEMENT_PARAM_FIELDS
from app.services.execution_summary import (
    CaseStatusInput,
    aggregate_statuses,
    compute_counts,
    compute_exclusion_summary,
    merge_case_status,
    merge_execution_status,
    rate_percent,
)
from app.services.profile_resolver_nodes import runtime_variable_names
from app.services.random_variables import resolve_rows

logger = logging.getLogger("worker")

_VAR_RE = re.compile(r"\$\{(\w+)\}")
TERMINAL_STATES = {"passed", "failed", "error", "stopped", "cancelled"}
_PHASE_MAP = {"setup": "case_setup", "main": "case_main", "teardown": "case_teardown"}


@dataclass(frozen=True)
class UnavailableQueueItem:
    queue: ExecutionQueue
    execution: Execution
    device: Device | None
    agent: Agent | None
    message: str


def _numeric_order(value: object) -> int:
    return int(str(value)) if isinstance(value, (int, float, str)) else 0


def _legacy_steps_from_flow(nodes: list[dict]) -> list[dict]:
    """只为旧执行明细字段生成嵌套投影，V3 下发使用原始 flow_nodes。"""
    steps: list[dict] = []
    for node in nodes:
        if node.get("kind") == "assertion":
            if steps:
                assertion = {
                    key: value for key, value in node.items() if key not in {"kind", "order", "phase"}
                }
                assertion["order"] = len(steps[-1].get("assertions") or []) + 1
                steps[-1].setdefault("assertions", []).append(assertion)
            continue
        step = {key: value for key, value in node.items() if key != "kind"}
        step["order"] = len(steps) + 1
        steps.append(step)
    return steps


def _flatten_legacy_steps(nodes: list[dict]) -> list[dict]:
    """仅兼容尚未通过用例 API 更新的旧数据库行。"""
    result: list[dict] = []
    phase_counters: dict[str, int] = {}
    for raw_step in nodes:
        if not isinstance(raw_step, dict):
            continue
        phase = str(raw_step.get("phase") or "main")
        phase_counters[phase] = phase_counters.get(phase, 0) + 1
        action = {key: value for key, value in raw_step.items() if key != "assertions"}
        action.update(kind="action", order=phase_counters[phase], phase=phase)
        result.append(action)
        for raw_assertion in raw_step.get("assertions") or []:
            if not isinstance(raw_assertion, dict):
                continue
            phase_counters[phase] += 1
            assertion = dict(raw_assertion)
            assertion.update(
                kind="assertion",
                order=phase_counters[phase],
                phase=phase,
                max_wait_seconds=raw_assertion.get("max_wait_seconds", 10),
            )
            result.append(assertion)
    return result
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


def render_text(text: str, variables: dict, runtime_variables: set[str] | frozenset[str] = frozenset()) -> str:
    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            if name in runtime_variables:
                return match.group(0)
            raise ValueError(f"未定义变量: ${{{name}}}")
        return str(variables[name])

    return _VAR_RE.sub(repl, text)


def render_value(
    value: Any,
    variables: dict,
    runtime_variables: set[str] | frozenset[str] = frozenset(),
) -> Any:
    if isinstance(value, str):
        return render_text(value, variables, runtime_variables)
    if isinstance(value, dict):
        return {k: render_value(v, variables, runtime_variables) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, variables, runtime_variables) for v in value]
    return value


async def build_base_variable_map(
    db: AsyncSession, execution: Execution, *, excluded_names: set[str] | frozenset[str] = frozenset()
) -> dict:
    """最低两层变量：全局 → 项目。

    用例/套件/执行参数由调用方按 §10.6 的固定顺序叠加，避免各处自行合并时
    出现"后写入者反而优先级更低"的逆序。
    """
    cache = getattr(execution, "_variable_resolution_cache", None)
    if cache is None:
        cache = {}
        object.__setattr__(execution, "_variable_resolution_cache", cache)
    merged: dict = {}
    project_rows = (await db.execute(
        select(Variable).where(Variable.scope == "project", Variable.project_id == execution.project_id)
    )).scalars().all()
    global_rows = (await db.execute(select(Variable).where(Variable.scope == "global"))).scalars().all()
    merged.update(resolve_rows(
        list(global_rows), cache, ("global",),
        skip_names=set(excluded_names) | {row.name for row in project_rows},
    ))
    merged.update(resolve_rows(list(project_rows), cache, ("project", execution.project_id), skip_names=excluded_names))
    return merged


async def build_variable_map(
    db: AsyncSession,
    execution: Execution,
    case: TestCase | None = None,
    *,
    suite_id: int | None = None,
    use_execution_suite: bool = True,
    case_occurrence: int | None = None,
    membership_overrides: dict[str, str] | None = None,
) -> dict:
    """按 §10.6 优先级构造变量表：全局 → 项目 → 用例 → 套件 → 编排项 → 执行参数。

    suite_id 显式传入时以它为准（批量执行会逐套件物化）；未传入且
    use_execution_suite 为真时退回 execution.suite_id（仅单套件执行）。
    需要"明确无套件上下文"时传 use_execution_suite=False。
    """
    execution_variables = (execution.parameters or {}).get("variables") or {}
    target_suite_id = suite_id
    if target_suite_id is None and use_execution_suite and execution.type == "suite":
        target_suite_id = execution.suite_id
    case_rows = []
    if case is not None:
        case_rows = (await db.execute(
            select(Variable).where(Variable.scope == "case", Variable.case_id == case.id)
        )).scalars().all()
    suite_rows = []
    if target_suite_id is not None:
        suite_rows = (await db.execute(
            select(Variable).where(Variable.scope == "suite", Variable.suite_id == target_suite_id)
        )).scalars().all()
    case_values = set(case.variables or {}) if case is not None else set()
    membership_values = set(membership_overrides or {})
    merged = await build_base_variable_map(
        db, execution,
        excluded_names=set(execution_variables) | case_values | membership_values |
        {row.name for row in case_rows} | {row.name for row in suite_rows},
    )
    cache = getattr(execution, "_variable_resolution_cache", {})
    if case is not None:
        merged.update(resolve_rows(
            list(case_rows), cache,
            ("case", suite_id, case.id, case_occurrence if case_occurrence is not None else case.id),
            skip_names=case_values | membership_values,
        ))
        if case.variables:
            merged.update({key: str(value) for key, value in case.variables.items()})
    if target_suite_id is not None:
        merged.update(resolve_rows(
            list(suite_rows), cache, ("suite", target_suite_id),
            skip_names=set(execution_variables) | membership_values,
        ))
    merged.update(membership_overrides or {})
    merged.update(execution_variables)
    return merged


# ---------- 快照（§10.3：元素定位快照） ----------


async def _collect_element_ids(steps: list) -> set[int]:
    ids: set[int] = set()
    for item in steps:
        if not isinstance(item, dict):
            continue
        values = [item.get("element_id")]
        node_name = str(item.get("action") or item.get("type") or "")
        params = item.get("params")
        if isinstance(params, dict):
            values.extend(params.get(field) for field in ELEMENT_PARAM_FIELDS.get(node_name, ()))
        for element_id in values:
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
    stored_flow = list(case.flow_nodes or [])
    # 新写入的数据始终以 flow_nodes 为准；只有发现旧 steps 被独立修改时，
    # 才将其一次性展开，避免历史管理脚本改过 steps 后整条执行链读到旧快照。
    if case.steps and (not stored_flow or _legacy_steps_from_flow(stored_flow) != case.steps):
        stored_flow = _flatten_legacy_steps(case.steps)
    selected_steps: list[dict] = []
    runtime_variables: set[str] = set()
    for phase in ("setup", "main", "teardown"):
        if phase == "setup" and not use_pre_steps:
            continue
        if phase == "teardown" and not use_post_steps:
            continue
        phase_steps = [
            step
            for step in stored_flow
            if isinstance(step, dict) and str(step.get("phase") or "main") == phase
        ]
        phase_steps.sort(key=lambda step: _numeric_order(step.get("order")))
        phase_order = 0
        for raw_step in phase_steps:
            phase_order += 1
            step = render_value(deepcopy(raw_step), variable_map, runtime_variables)
            selected_steps.append(
                {
                    **step,
                    "phase": phase,
                    "source_order": step.get("order"),
                    "order": phase_order,
                }
            )
            runtime_variables.update(runtime_variable_names(step))
    steps = selected_steps
    element_ids = await _collect_element_ids(steps)
    elements: dict = {}
    if element_ids:
        rows = (
            await db.execute(select(TestElement).where(TestElement.id.in_(element_ids)))
        ).scalars().all()
        for el in rows:
            elements[str(el.id)] = _element_snapshot(el, variable_map, runtime_variables)
    return {"flow_nodes": steps, "steps": _legacy_steps_from_flow(steps), "elements": elements}


def _element_snapshot(
    el: TestElement,
    variables: dict,
    runtime_variables: set[str] | frozenset[str] = frozenset(),
) -> dict:
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
        "locator_value": render_value(el.locator_value, variables, runtime_variables),
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


def _suite_step_snapshots(
    nodes: list,
    variable_map: dict,
    phase: str,
    runtime_variables: set[str] | None = None,
) -> list[dict]:
    available_runtime_variables = runtime_variables if runtime_variables is not None else set()
    snapshots: list[dict] = []
    for raw_node in sorted(
        (item for item in (nodes or []) if isinstance(item, dict)),
        key=lambda item: _numeric_order(item.get("order")),
    ):
        node = render_value(deepcopy(raw_node), variable_map, available_runtime_variables)
        snapshots.append({
            **node,
            "phase": phase,
            "source_order": node.get("order"),
            "order": len(snapshots) + 1,
        })
        available_runtime_variables.update(runtime_variable_names(node))
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
    # §10.6 固定顺序：global → project → case → suite → 执行参数。
    execution_variables = parameters.get("variables") or {}
    # 基础两层与执行参数在循环外求一次值；套件变量随套件切换，逐层叠加即可，
    # 避免"先复制完整映射再用低优先级覆盖高优先级"的逆序写法。
    base_map = await build_base_variable_map(db, execution, excluded_names=set(execution_variables))
    for suite_order, (suite_id, selected_case_ids) in enumerate(suite_specs, start=1):
        suite = await db.get(TestSuite, suite_id) if suite_id is not None else None
        if suite_id is not None and (suite is None or suite.deleted_at is not None):
            continue
        suite_variables: dict = {}
        if suite is None:
            suite_name = "虚拟套件"
        else:
            suite_name = suite.name
            suite_rows = (await db.execute(
                select(Variable).where(Variable.scope == "suite", Variable.suite_id == suite.id)
            )).scalars().all()
            suite_variables = resolve_rows(
                list(suite_rows), getattr(execution, "_variable_resolution_cache", {}),
                ("suite", suite_order, suite.id), skip_names=set(execution_variables),
            )
        suite_variable_map = {**base_map, **suite_variables, **execution_variables}
        suite_runtime_variables: set[str] = set()
        setup_snapshot = (
            _suite_step_snapshots(suite.setup_steps, suite_variable_map, "suite_setup", suite_runtime_variables)
            if suite is not None
            else []
        )
        teardown_snapshot = (
            _suite_step_snapshots(suite.teardown_steps, suite_variable_map, "suite_teardown", suite_runtime_variables)
            if suite is not None
            else []
        )

        suite_steps = [*setup_snapshot, *teardown_snapshot]
        suite_element_ids = await _collect_element_ids(suite_steps)
        suite_elements: dict = {}
        if suite_element_ids:
            suite_element_rows = (
                await db.execute(select(TestElement).where(TestElement.id.in_(suite_element_ids)))
            ).scalars().all()
            for element in suite_element_rows:
                suite_elements[str(element.id)] = _element_snapshot(
                    element, suite_variable_map, suite_runtime_variables
                )

        exec_suite = ExecutionSuite(
            execution_id=execution.id, suite_id=suite_id, suite_name=suite_name,
            suite_order=suite_order, is_virtual=suite_id is None, status="pending",
            setup_steps_snapshot=setup_snapshot, teardown_steps_snapshot=teardown_snapshot,
            elements_snapshot=suite_elements,
        )
        db.add(exec_suite)
        await db.flush()
        for phase_steps in (setup_snapshot, teardown_snapshot):
            for node_order, step in enumerate(phase_steps, start=1):
                db.add(ExecutionNode(
                    execution_suite_id=exec_suite.id, kind="action", node_order=node_order,
                    phase=step["phase"], node_key=step.get("source_key") or step.get("key"),
                    action=step.get("action") or "", description=step.get("description"),
                    element_id=step.get("element_id"),
                    parameters=step.get("params") or {},
                    max_wait_seconds=step.get("max_wait_seconds"),
                    continue_on_failure=bool(step.get("continue_on_failure", False)), status="pending",
                ))

        if selected_case_ids is None:
            member_rows = (await db.execute(
                select(TestSuiteCase.id, TestSuiteCase.case_id, TestSuiteCase.variable_overrides)
                .where(TestSuiteCase.suite_id == suite_id)
                .order_by(TestSuiteCase.sort_order, TestSuiteCase.id)
            )).all()
            members = [(membership_id, case_id, dict(overrides or {})) for membership_id, case_id, overrides in member_rows]
        else:
            # 单用例执行无套件编排上下文，不应用编排项覆盖
            members = [(None, case_id, {}) for case_id in selected_case_ids]
        for case_order, (_membership_id, case_id, membership_overrides) in enumerate(members, start=1):
            case = await db.get(TestCase, case_id)
            if case is None or case.deleted_at is not None:
                continue
            # 用例变量位于项目/全局之上、套件变量之下；编排项覆盖高于套件变量、低于执行参数。
            variable_map = {**base_map}
            case_rows = (await db.execute(
                select(Variable).where(Variable.scope == "case", Variable.case_id == case.id)
            )).scalars().all()
            variable_map.update(resolve_rows(
                list(case_rows), getattr(execution, "_variable_resolution_cache", {}),
                ("case", suite_order, suite_id, case.id, case_order),
                skip_names=set(case.variables or {}) | set(membership_overrides),
            ))
            variable_map.update({key: str(value) for key, value in (case.variables or {}).items()})
            variable_map.update(suite_variables)
            variable_map.update(membership_overrides)
            variable_map.update(execution_variables)
            snapshot = await build_case_snapshot(
                db, case, variable_map,
                use_pre_steps=bool(parameters.get("use_pre_steps")),
                use_post_steps=bool(parameters.get("use_post_steps")),
            )
            exec_case = ExecutionCase(
                execution_id=execution.id, execution_suite_id=exec_suite.id, case_id=case.id,
                case_name=case.name, module_name=None, case_order=case_order, status="pending",
                steps_snapshot=snapshot["steps"], flow_snapshot=snapshot["flow_nodes"], elements_snapshot=snapshot["elements"],
            )
            db.add(exec_case)
            await db.flush()
            for node in snapshot["flow_nodes"]:
                node_kind = node.get("kind") or ("assertion" if node.get("type") else "action")
                db.add(ExecutionNode(
                    execution_case_id=exec_case.id, kind=node_kind,
                    node_order=int(node.get("order") or 0),
                    phase={"setup": "case_setup", "main": "case_main", "teardown": "case_teardown"}.get(str(node.get("phase")), "case_main"),
                    node_key=node.get("source_key") or node.get("key"), action=node.get("action"),
                    assertion_type=node.get("type") or node.get("assertion_type"),
                    description=node.get("description"),
                    element_id=node.get("element_id"), parameters=node.get("params") or {},
                    max_wait_seconds=node.get("max_wait_seconds"),
                    continue_on_failure=bool(node.get("continue_on_failure", False)), status="pending",
                ))
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
    snapshot_suites = await db.scalar(
        select(ExecutionSuite.id)
        .where(ExecutionSuite.execution_id == execution.id)
        .limit(1)
    )
    existing = list(
        (
            await db.execute(
                select(ExecutionCase)
                .where(ExecutionCase.execution_id == execution.id)
                .order_by(ExecutionCase.execution_suite_id, ExecutionCase.case_order)
            )
        ).scalars().all()
    )
    if existing or snapshot_suites is not None:
        return existing
    if execution.app_profile_id is not None:
        return []
    return await _materialize_unprofiled_tree(db, execution)


# ---------- 队列认领（SKIP LOCKED） ----------


async def claim_next_queue(db: AsyncSession, worker_id: str) -> ExecutionQueue | None:
    """原子认领一个当前可运行的执行，并同时占用其指定设备。

    查询按队列顺序筛选在线 Agent 的空闲设备，因此忙设备不会阻塞其他设备的
    任务；同一设备的任务仍由 ``created_at/id`` 保持 FIFO。队列、执行和设备
    通过同一个行锁事务完成状态转换，避免先写 running 后锁设备的中间态。
    """
    stmt = (
        select(ExecutionQueue, Execution, Device)
        .join(Execution, Execution.id == ExecutionQueue.execution_id)
        .join(Device, Device.id == Execution.device_id)
        .join(Agent, Agent.id == Device.agent_id)
        .where(
            ExecutionQueue.status == "pending",
            Execution.status == "queued",
            Execution.dispatch_state == "pending",
            Execution.device_id.is_not(None),
            Device.status == "idle",
            Device.locked_by_execution.is_(None),
            Agent.status == "online",
            Agent.deleted_at.is_(None),
        )
        .order_by(ExecutionQueue.created_at, ExecutionQueue.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    selected = (await db.execute(stmt)).first()
    if selected is None:
        return None
    row, execution, device = selected
    now = datetime.now(UTC)
    session_token = secrets.token_urlsafe(32)
    locked = await db.execute(
        update(Device)
        .where(
            Device.id == device.id,
            Device.status == "idle",
            Device.locked_by_execution.is_(None),
        )
        .values(status="busy", locked_by_execution=execution.id, updated_at=now)
        .returning(Device.id)
    )
    if locked.scalar_one_or_none() is None:
        return None
    row.status = "claimed"
    row.claimed_by = worker_id
    row.claimed_at = now
    execution.status = "running"
    execution.dispatch_state = "reserved"
    execution.dispatch_started_at = None
    execution.dispatched_at = None
    execution.session_token = session_token
    execution.started_at = now
    await db.flush()
    return row


def _unavailable_message(
    execution: Execution, device: Device | None, agent: Agent | None
) -> str | None:
    if execution.device_id is None:
        return "执行未指定设备"
    if device is None:
        return "指定设备不存在"
    if agent is None or agent.deleted_at is not None or agent.status != "online":
        return "指定 Agent 当前离线"
    if device.status == "offline":
        return "指定设备当前离线"
    if device.status == "unauthorized":
        return "指定设备未授权，请在设备上允许 USB 调试"
    if device.status == "error":
        return "指定设备当前不可用"
    return None


async def claim_unavailable_queue(db: AsyncSession) -> UnavailableQueueItem | None:
    """锁定一个明确不可运行的队列项，但不占用 Worker 执行槽位。

    在线 Agent 的 busy/已锁设备不在条件内，必须继续排队；仅资源明确失效
    的任务才由调用方立即终结为 error。
    """
    unavailable = or_(
        Execution.device_id.is_(None),
        Device.id.is_(None),
        Device.status.in_(("offline", "unauthorized", "error")),
        Agent.id.is_(None),
        Agent.deleted_at.is_not(None),
        Agent.status != "online",
    )
    stmt = (
        select(ExecutionQueue, Execution, Device, Agent)
        .join(Execution, Execution.id == ExecutionQueue.execution_id)
        .outerjoin(Device, Device.id == Execution.device_id)
        .outerjoin(Agent, Device.agent_id == Agent.id)
        .where(
            ExecutionQueue.status == "pending",
            Execution.status == "queued",
            Execution.dispatch_state == "pending",
            unavailable,
        )
        .order_by(ExecutionQueue.created_at, ExecutionQueue.id)
        # 外连接的 Device/Agent 允许缺失，不能让 PostgreSQL 尝试锁定其 NULL
        # 行；这里只锁定必然存在的队列与执行记录。
        .with_for_update(of=(ExecutionQueue, Execution), skip_locked=True)
        .limit(1)
    )
    selected = (await db.execute(stmt)).first()
    if selected is None:
        return None
    queue, execution, device, agent = selected
    message = _unavailable_message(execution, device, agent)
    if message is None:
        return None
    return UnavailableQueueItem(queue, execution, device, agent, message)


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
    case_nodes_by_case: dict[int, list[ExecutionNode]],
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
        nodes = case_nodes_by_case.get(case.id, [])
        steps = case_steps_by_case.get(case.id, [])
        assertions = assertions_by_case.get(case.id, [])
        merged_case = merge_case_status(
            CaseStatusInput(
                status=case.status,
                started=case.started_at is not None if should_merge else False,
                step_statuses=(
                    tuple(node.status for node in nodes if node.kind == "action")
                    if nodes and (not steps or any(node.status != "pending" for node in nodes))
                    else tuple(step.status for step in steps)
                ) if should_merge else (),
                assertion_statuses=(
                    tuple(node.status for node in nodes if node.kind == "assertion")
                    if nodes and (not assertions or any(node.status != "pending" for node in nodes))
                    else tuple(assertion.status for assertion in assertions)
                ) if should_merge else (),
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
    suite_nodes_by_suite: dict[int, list[ExecutionNode]],
    suite_steps_by_suite: dict[int, list[ExecutionStep]],
    all_steps: list[ExecutionStep],
    terminal_status: str,
) -> None:
    """按统一执行节点归并套件，并收敛未执行的套件节点。

    套件前后置步骤的执行结果只来自 ExecutionNode。ExecutionStep 是旧的
    步骤模型，不能再参与套件状态归并，否则节点已 passed 而旧行仍 pending
    时会把套件错误汇总成 skipped。
    """
    pending_suite_step_ids: set[int] = set()
    pending_suite_node_ids: set[int] = set()
    for suite in suites:
        if suite.status not in ("pending", "running"):
            continue
        suite_was_running = suite.status == "running"
        suite_nodes = suite_nodes_by_suite.get(suite.id, [])
        suite_steps = suite_steps_by_suite.get(suite.id, [])
        # 仅兼容没有统一节点的历史/手工数据；正常新执行一定有 suite_nodes，
        # 不会再让旧 ExecutionStep 参与套件状态判断。
        child_items = suite_nodes or suite_steps
        child_statuses = [
            _normalize_stuck_status(item.status, terminal_status) for item in child_items
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
        pending_suite_node_ids.update(
            node.id for node in suite_nodes if node.status == "pending"
        )
        # 保持旧详情行的终态可读，但它不再参与套件状态或报告统计。
        pending_suite_step_ids.update(
            step.id for step in suite_steps if step.status == "pending"
        )

    if pending_suite_node_ids:
        for node in suite_nodes_by_suite.values():
            for item in node:
                if item.id in pending_suite_node_ids:
                    item.status = "skipped"
        await db.execute(
            update(ExecutionNode)
            .where(ExecutionNode.id.in_(pending_suite_node_ids))
            .values(status="skipped")
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
    node_filters = []
    if suite_ids:
        node_filters.append(ExecutionNode.execution_suite_id.in_(suite_ids))
    if case_ids:
        node_filters.append(ExecutionNode.execution_case_id.in_(case_ids))
    all_nodes = list((await db.execute(select(ExecutionNode).where(or_(*node_filters)))).scalars().all()) if node_filters else []
    suite_nodes_by_suite: dict[int, list[ExecutionNode]] = {}
    case_nodes_by_case: dict[int, list[ExecutionNode]] = {}
    for node in all_nodes:
        if node.execution_suite_id is not None:
            suite_nodes_by_suite.setdefault(node.execution_suite_id, []).append(node)
        elif node.execution_case_id is not None:
            case_nodes_by_case.setdefault(node.execution_case_id, []).append(node)
    await _merge_cases_and_skip_pending(
        db,
        cases,
        case_nodes_by_case,
        case_steps_by_case,
        assertions_by_case,
        all_steps,
        all_assertions,
        status_,
    )
    for node in all_nodes:
        node.status = _normalize_stuck_status(node.status, status_)
    await _merge_suites_and_skip_pending(
        db,
        suites,
        cases_by_suite,
        suite_nodes_by_suite,
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

    action_nodes = [node for node in all_nodes if node.kind == "action"]
    assertion_nodes = [node for node in all_nodes if node.kind == "assertion"]
    step_counts = compute_counts(tuple(node.status for node in action_nodes)) if action_nodes else compute_counts(tuple(s.status for s in all_steps))
    step_rate = rate_percent(
        step_counts.passed,
        step_counts.passed + step_counts.failed + step_counts.error_count,
    )
    assertion_counts = compute_counts(tuple(node.status for node in assertion_nodes))
    assertion_rate = rate_percent(
        assertion_counts.passed,
        assertion_counts.passed + assertion_counts.failed + assertion_counts.error_count,
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
            assertion_total=assertion_counts.total,
            assertion_passed=assertion_counts.passed,
            assertion_failed=assertion_counts.failed,
            assertion_error_count=assertion_counts.error_count,
            assertion_skipped=assertion_counts.skipped,
            assertion_success_rate=assertion_rate,
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
    - 套件前后置步骤读取已固化的 ExecutionNode，携带 execution_node_id 供 Agent
      精确回传；旧 setup_steps/teardown_steps 仅作为详情兼容投影保留；
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
    _case_ids = [c.id for c in cases]

    # V3 预建统一节点按父节点、阶段和 node_order 建立 id 映射。
    _node_rows = (
        await db.execute(
            select(ExecutionNode)
            .where(ExecutionNode.execution_case_id.in_(_case_ids))
            .order_by(ExecutionNode.execution_case_id, ExecutionNode.phase, ExecutionNode.node_order)
        )
    ).scalars().all() if _case_ids else []
    node_id_by_key: dict[tuple[int, str, int], int] = {
        (node.execution_case_id, node.phase, node.node_order): node.id
        for node in _node_rows if node.execution_case_id is not None
    }

    suite_node_rows = list(
        (
            await db.execute(
                select(ExecutionNode)
                .where(ExecutionNode.execution_suite_id.in_(suite_ids))
                .order_by(ExecutionNode.execution_suite_id, ExecutionNode.phase, ExecutionNode.node_order)
            )
        ).scalars().all()
    )
    nodes_by_suite: dict[int, list[ExecutionNode]] = {}
    for node in suite_node_rows:
        if node.execution_suite_id is not None:
            nodes_by_suite.setdefault(node.execution_suite_id, []).append(node)

    # 旧步骤/断言映射保留给旧快照；新的 flow_snapshot 不依赖它。
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

    def _inject_node_ids(case: ExecutionCase, nodes: list) -> list:
        out: list[dict] = []
        for raw_node in nodes:
            entry = dict(raw_node)
            order = int(entry.get("order") or 0)
            phase = str(entry.get("phase") or "case_main")
            phase = _PHASE_MAP.get(phase, phase)
            node_id = node_id_by_key.get((case.id, phase, order))
            if node_id is None:
                raise RuntimeError(
                    f"执行快照缺少统一节点行 execution_case_id={case.id}, phase={phase}, order={order}"
                )
            entry["execution_node_id"] = node_id
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
        suite_step_snapshots = {
            "suite_setup": s.setup_steps_snapshot or [],
            "suite_teardown": s.teardown_steps_snapshot or [],
        }

        def _suite_step_payload(
            st: ExecutionStep, snapshots: dict[str, list] = suite_step_snapshots
        ) -> dict:
            """将执行步骤行与原始步骤快照合并，保留元素引用供 Agent 定位。"""
            phase = st.phase
            snapshot = next(
                (
                    item
                    for item in snapshots.get(phase, [])
                    if isinstance(item, dict)
                    and int(item.get("order") or item.get("step_order") or 0) == st.step_order
                ),
                {},
            )
            payload = {
                "execution_step_id": st.id,
                "action": st.action,
                "order": st.step_order,
                "params": st.parameters or {},
                "source_key": st.source_key,
                "source_order": st.source_order,
                "continue_on_failure": st.continue_on_failure,
            }
            if snapshot.get("element_id") is not None:
                payload["element_id"] = snapshot["element_id"]
            return payload

        setup_steps = [
            _suite_step_payload(st)
            for st in steps_by_suite.get(s.id, [])
            if st.phase == "suite_setup"
        ]
        teardown_steps = [
            _suite_step_payload(st)
            for st in steps_by_suite.get(s.id, [])
            if st.phase == "suite_teardown"
        ]

        def _suite_node_payload(node: ExecutionNode) -> dict:
            payload = {
                "execution_node_id": node.id,
                "kind": node.kind,
                "phase": node.phase,
                "order": node.node_order,
                "action": node.action,
                "params": node.parameters or {},
                "source_key": node.node_key,
                "element_id": node.element_id,
                "continue_on_failure": node.continue_on_failure,
            }
            if node.max_wait_seconds is not None:
                payload["max_wait_seconds"] = float(node.max_wait_seconds)
            return payload

        setup_nodes = [
            _suite_node_payload(node)
            for node in nodes_by_suite.get(s.id, [])
            if node.phase == "suite_setup"
        ]
        teardown_nodes = [
            _suite_node_payload(node)
            for node in nodes_by_suite.get(s.id, [])
            if node.phase == "suite_teardown"
        ]
        suite_cases = [
            {
                "execution_case_id": c.id,
                "case_id": c.case_id,
                "case_name": c.case_name,
                "case_order": c.case_order,
                "module_name": c.module_name,
                "flow_snapshot": _inject_node_ids(c, c.flow_snapshot or []),
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
                "setup_nodes": setup_nodes,
                "setup_steps": setup_steps,
                "cases": suite_cases,
                "teardown_nodes": teardown_nodes,
                "teardown_steps": teardown_steps,
            }
        )
    return payload_suites


# ---------- 扫描任务（仅 worker-001 启用） ----------


async def _restore_stale_reserved(
    db: AsyncSession, row: ExecutionQueue, execution: Execution
) -> bool:
    """恢复 Worker 异常退出时遗留的 reserved 认领。"""
    if not row.claimed_by or not execution.session_token:
        return False
    restored = await db.execute(
        update(Execution)
        .where(
            Execution.id == execution.id,
            Execution.status == "running",
            Execution.dispatch_state == "reserved",
            Execution.finalized_at.is_(None),
            Execution.session_token == execution.session_token,
        )
        .values(
            status="queued",
            dispatch_state="pending",
            dispatch_started_at=None,
            dispatched_at=None,
            session_token=None,
            started_at=None,
        )
        .returning(Execution.id)
    )
    if restored.scalar_one_or_none() is None:
        return False

    now = datetime.now(UTC)
    locked_devices = (
        await db.execute(
            select(Device).where(Device.locked_by_execution == execution.id)
        )
    ).scalars().all()
    for device in locked_devices:
        agent = await db.get(Agent, device.agent_id)
        device.status = (
            "idle" if agent is not None and agent.status == "online" and agent.deleted_at is None
            else "offline"
        )
        device.locked_by_execution = None
        device.updated_at = now

    queue_result = await db.execute(
        update(ExecutionQueue)
        .where(
            ExecutionQueue.id == row.id,
            ExecutionQueue.execution_id == execution.id,
            ExecutionQueue.status == "claimed",
            ExecutionQueue.claimed_by == row.claimed_by,
        )
        .values(status="pending", claimed_by=None, claimed_at=None)
    )
    if int(getattr(queue_result, "rowcount", 0)) != 1:
        raise RuntimeError(f"恢复 stale reserved 执行 {execution.id} 时队列认领不匹配")
    return True


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
            if execution.dispatch_state == "reserved":
                if row.retry_count >= 2:
                    row.status = "failed"
                    await _mark_terminal(
                        db, execution, "error", "队列认领超时，重试次数超限"
                    )
                elif await _restore_stale_reserved(db, row, execution):
                    row.retry_count += 1
                continue
            if execution.dispatch_state in {"dispatching", "dispatched"}:
                logger.warning(
                    "执行下发状态不确定，保留认领 execution=%s dispatch_state=%s",
                    execution.id,
                    execution.dispatch_state,
                )
                continue
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


async def timeout_scan(db: AsyncSession) -> list[int]:
    """Request timeout termination and return executions needing stop_test.

    The atomic running -> stopping update is the only action for a newly timed
    out execution. Child rows and the device lock remain untouched until Agent
    confirmation or the persisted grace deadline.
    """
    now = datetime.now(UTC)
    stop_candidates: list[int] = []
    active = (
        await db.execute(select(Execution).where(Execution.status.in_(["running", "stopping"])))
    ).scalars().all()
    for execution in active:
        if execution.started_at is None and execution.stop_requested_at is None:
            continue
        if execution.status == "stopping":
            # stopping 执行只在 Agent 确认或宽限期到期时汇总；超时请求必须落 error。
            if _stop_grace_exceeded(execution):
                forced_status = "error" if execution.termination_reason == "timeout" else "stopped"
                forced_message = (
                    f"执行超时（>{execution.timeout_seconds}s），停止宽限期到期，强制结束"
                    if execution.termination_reason == "timeout"
                    else f"停止宽限期超时（>{settings.execution_stop_grace_seconds}s），强制结束"
                )
                await _mark_terminal(
                    db, execution, forced_status, forced_message,
                )
            elif (
                execution.stop_command_sent_at is None
                or execution.stop_command_sent_at
                < now - timedelta(seconds=settings.execution_stop_retry_seconds)
            ):
                stop_candidates.append(execution.id)
        elif execution.started_at is not None:
            elapsed = (now - execution.started_at).total_seconds()
            if elapsed > execution.timeout_seconds:
                if await executions_repo.request_timeout_stop(db, execution.id, now):
                    stop_candidates.append(execution.id)
                    logger.warning(
                        "execution=%s timeout requested; waiting for Agent stop acknowledgement",
                        execution.id,
                    )
    return stop_candidates


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
            execution = None
            if device.locked_by_execution is not None:
                execution = await db.get(Execution, device.locked_by_execution)
                if execution is not None and execution.status == "running":
                    await _mark_terminal(db, execution, "error", "Agent 心跳超时失联")
                elif execution is not None and execution.status == "stopping":
                    # 超时 stopping 仍需等待 stop_test 确认/宽限期，不能因心跳扫描
                    # 提前释放设备并把 pending 子节点汇总掉。
                    if execution.termination_reason != "timeout":
                        await _mark_terminal(db, execution, "stopped", "Agent 心跳超时失联（停止中）")
            if not (
                execution is not None
                and execution.status == "stopping"
                and execution.termination_reason == "timeout"
                and execution.finalized_at is None
            ):
                device.status = "idle"
                device.locked_by_execution = None
    await db.flush()
