"""内部执行状态和 Agent 执行绑定查询。"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
    Project,
    TestCase,
    TestSuite,
    User,
)
from app.repositories import execution_tree


class SnapshotNotReadyError(Exception):
    """原执行没有足够的不可变快照，不能创建重试执行。"""


async def get_by_id(
    db: AsyncSession, execution_id: int, *, populate_existing: bool = False
) -> Execution | None:
    return await db.get(Execution, execution_id, populate_existing=populate_existing)


async def update_state(
    execution: Execution, *, status: str, now: datetime
) -> Execution:
    execution.status = status
    if status == "running" and execution.started_at is None:
        execution.started_at = now
    if status in {"passed", "failed", "error", "stopped", "cancelled"}:
        execution.finished_at = now
    return execution


async def get_device_for_execution(
    db: AsyncSession, execution: Execution
) -> Device | None:
    return await db.get(Device, execution.device_id) if execution.device_id else None


async def create(db: AsyncSession, *, fields: dict) -> Execution:
    execution = Execution(**fields)
    db.add(execution)
    await db.flush()
    return execution


async def refresh(db: AsyncSession, execution: Execution) -> Execution:
    await db.refresh(execution)
    return execution


async def enqueue(db: AsyncSession, execution_id: int) -> ExecutionQueue:
    queue = ExecutionQueue(execution_id=execution_id)
    db.add(queue)
    await db.flush()
    return queue


async def create_profiled(
    db: AsyncSession, *, fields: dict, result, app_profile_id: int
) -> Execution:
    execution = await create(db, fields=fields)
    await execution_tree.materialize_snapshot(db, execution, result)
    for exclusion in result.exclusions:
        display = exclusion.display_snapshot or {}
        if exclusion.phase is not None:
            display = {**display, "phase": exclusion.phase}
        db.add(ExecutionExclusion(execution_id=execution.id, app_profile_id=app_profile_id, target_type=exclusion.target_type, suite_id_snapshot=exclusion.suite_id, suite_name_snapshot=display.get("suite_name"), case_id_snapshot=exclusion.case_id, case_name_snapshot=display.get("case_name"), occurrence_order=exclusion.occurrence_order, node_key=exclusion.node_key, node_name_snapshot=display.get("node_name"), source_type=exclusion.source_type, reason_code=exclusion.reason_code, reason_note=exclusion.reason_note, details=display))
    await enqueue(db, execution.id)
    return execution


def _snapshot_phase(value: object, default: str) -> str:
    return {
        "setup": "case_setup",
        "main": "case_main",
        "teardown": "case_teardown",
    }.get(str(value), str(value or default))


def _snapshot_order(value: object) -> int:
    if not isinstance(value, (int, float, str)):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _snapshot_key(item: dict, default_phase: str) -> tuple[str, int]:
    return (
        _snapshot_phase(item.get("phase"), default_phase),
        _snapshot_order(item.get("order") or item.get("step_order")),
    )


def _raise_snapshot_not_ready(message: str) -> None:
    raise SnapshotNotReadyError(message)


def _validate_snapshot_tree(
    suites: list[ExecutionSuite],
    cases: list[ExecutionCase],
    suite_steps: list[ExecutionStep],
    case_steps: list[ExecutionStep],
    assertions: list[ExecutionAssertion],
    nodes: list[ExecutionNode],
) -> None:
    if not suites:
        _raise_snapshot_not_ready("执行快照缺少套件")
    suite_ids = {suite.id for suite in suites}
    cases_by_suite: dict[int, list[ExecutionCase]] = {}
    for case in cases:
        if case.execution_suite_id not in suite_ids:
            _raise_snapshot_not_ready("执行快照用例缺少所属套件")
        if not isinstance(case.steps_snapshot, list) or not isinstance(case.flow_snapshot, list):
            _raise_snapshot_not_ready(f"执行快照用例 {case.id} 内容不完整")
        cases_by_suite.setdefault(case.execution_suite_id, []).append(case)

    suite_steps_by_parent: dict[int, list[ExecutionStep]] = {}
    for step in suite_steps:
        if step.execution_suite_id is not None:
            suite_steps_by_parent.setdefault(step.execution_suite_id, []).append(step)
    case_steps_by_parent: dict[int, list[ExecutionStep]] = {}
    for step in case_steps:
        if step.execution_case_id is not None:
            case_steps_by_parent.setdefault(step.execution_case_id, []).append(step)
    assertions_by_step: dict[int, list[ExecutionAssertion]] = {}
    for assertion in assertions:
        assertions_by_step.setdefault(assertion.execution_step_id, []).append(assertion)
    nodes_by_suite: dict[int, list[ExecutionNode]] = {}
    nodes_by_case: dict[int, list[ExecutionNode]] = {}
    for node in nodes:
        if node.execution_suite_id is not None:
            nodes_by_suite.setdefault(node.execution_suite_id, []).append(node)
        if node.execution_case_id is not None:
            nodes_by_case.setdefault(node.execution_case_id, []).append(node)

    for suite in suites:
        setup = suite.setup_steps_snapshot
        teardown = suite.teardown_steps_snapshot
        if not isinstance(setup, list) or not isinstance(teardown, list):
            _raise_snapshot_not_ready(f"执行快照套件 {suite.id} 前后置快照不完整")
        expected_suite_nodes = [
            _snapshot_key(item, "suite_setup")
            for item in setup
            if isinstance(item, dict)
        ] + [
            _snapshot_key(item, "suite_teardown")
            for item in teardown
            if isinstance(item, dict)
        ]
        if len(expected_suite_nodes) != len(setup) + len(teardown):
            _raise_snapshot_not_ready(f"执行快照套件 {suite.id} 步骤快照不完整")
        actual_suite_steps = {
            (_snapshot_phase(step.phase, step.phase), step.step_order): step
            for step in suite_steps_by_parent.get(suite.id, [])
        }
        # Some historical snapshots represent suite setup/teardown only as
        # ExecutionNode rows. Validate step rows when present, without making
        # those otherwise complete snapshots impossible to retry.
        if actual_suite_steps and (
            len(actual_suite_steps) != len(expected_suite_nodes)
            or any(key not in actual_suite_steps for key in expected_suite_nodes)
        ):
            _raise_snapshot_not_ready(f"执行快照套件 {suite.id} 步骤行不完整")
        actual_suite_nodes = {
            (_snapshot_phase(node.phase, node.phase), node.node_order): node
            for node in nodes_by_suite.get(suite.id, [])
        }
        if len(actual_suite_nodes) != len(expected_suite_nodes) or any(
            key not in actual_suite_nodes for key in expected_suite_nodes
        ):
            _raise_snapshot_not_ready(f"执行快照套件 {suite.id} 缺少节点行")

        for case in cases_by_suite.get(suite.id, []):
            expected_steps = [item for item in case.steps_snapshot if isinstance(item, dict)]
            if len(expected_steps) != len(case.steps_snapshot):
                _raise_snapshot_not_ready(f"执行快照用例 {case.id} 步骤快照不完整")
            actual_steps = {
                (_snapshot_phase(step.phase, "case_main"), step.step_order): step
                for step in case_steps_by_parent.get(case.id, [])
            }
            expected_keys = [_snapshot_key(item, "case_main") for item in expected_steps]
            if len(actual_steps) != len(expected_keys) or any(
                key not in actual_steps for key in expected_keys
            ):
                _raise_snapshot_not_ready(f"执行快照用例 {case.id} 缺少步骤行")
            if len(nodes_by_case.get(case.id, [])) != len(case.flow_snapshot):
                _raise_snapshot_not_ready(f"执行快照用例 {case.id} 缺少节点行")
            actual_case_nodes = {
                (_snapshot_phase(node.phase, node.phase), node.node_order): node
                for node in nodes_by_case.get(case.id, [])
            }
            expected_case_nodes = {
                _snapshot_key(item, "case_main")
                for item in case.flow_snapshot
                if isinstance(item, dict)
            }
            if len(actual_case_nodes) != len(expected_case_nodes) or any(
                key not in actual_case_nodes for key in expected_case_nodes
            ):
                _raise_snapshot_not_ready(f"执行快照用例 {case.id} 节点行不完整")
            for item, key in zip(expected_steps, expected_keys, strict=True):
                step = actual_steps[key]
                expected_assertions = [
                    assertion for assertion in item.get("assertions") or []
                    if isinstance(assertion, dict)
                ]
                actual_assertions = assertions_by_step.get(step.id, [])
                if len(expected_assertions) != len(item.get("assertions") or []):
                    _raise_snapshot_not_ready(f"执行快照步骤 {step.id} 断言快照不完整")
                if len(actual_assertions) != len(expected_assertions):
                    _raise_snapshot_not_ready(f"执行快照步骤 {step.id} 缺少断言行")


def _remap_snapshot_node_ids(value: Any, node_map: dict[int, int]) -> Any:
    """Replace copied node references so JSON never points at the old run."""
    if isinstance(value, list):
        return [_remap_snapshot_node_ids(item, node_map) for item in value]
    if isinstance(value, dict):
        copied = {
            key: _remap_snapshot_node_ids(item, node_map) for key, item in value.items()
        }
        old_id = copied.get("execution_node_id")
        if old_id is not None:
            try:
                copied["execution_node_id"] = node_map[int(old_id)]
            except (KeyError, TypeError, ValueError):
                copied.pop("execution_node_id", None)
        return copied
    return value


async def clone_snapshot(
    db: AsyncSession,
    source: Execution,
    *,
    user_id: int,
    device_id: int,
    timeout_seconds: int,
) -> Execution:
    """在当前事务内克隆完整执行快照，不读取任何当前资产或档案。"""
    suites = list(
        (
            await db.execute(
                select(ExecutionSuite)
                .where(ExecutionSuite.execution_id == source.id)
                .order_by(ExecutionSuite.suite_order, ExecutionSuite.id)
            )
        ).scalars().all()
    )
    cases = list(
        (
            await db.execute(
                select(ExecutionCase)
                .where(ExecutionCase.execution_id == source.id)
                .order_by(ExecutionCase.execution_suite_id, ExecutionCase.case_order, ExecutionCase.id)
            )
        ).scalars().all()
    )
    suite_ids = [suite.id for suite in suites]
    case_ids = [case.id for case in cases]
    suite_steps = list(
        (
            await db.execute(
                select(ExecutionStep)
                .where(ExecutionStep.execution_suite_id.in_(suite_ids))
                .order_by(ExecutionStep.execution_suite_id, ExecutionStep.phase, ExecutionStep.step_order)
            )
        ).scalars().all()
    ) if suite_ids else []
    case_steps = list(
        (
            await db.execute(
                select(ExecutionStep)
                .where(ExecutionStep.execution_case_id.in_(case_ids))
                .order_by(ExecutionStep.execution_case_id, ExecutionStep.phase, ExecutionStep.step_order)
            )
        ).scalars().all()
    ) if case_ids else []
    all_steps = [*suite_steps, *case_steps]
    step_ids = [step.id for step in all_steps]
    assertions = list(
        (
            await db.execute(
                select(ExecutionAssertion)
                .where(ExecutionAssertion.execution_step_id.in_(step_ids))
                .order_by(ExecutionAssertion.execution_step_id, ExecutionAssertion.assertion_order)
            )
        ).scalars().all()
    ) if step_ids else []
    nodes = list(
        (
            await db.execute(
                select(ExecutionNode)
                .where(
                    (ExecutionNode.execution_suite_id.in_(suite_ids))
                    | (ExecutionNode.execution_case_id.in_(case_ids))
                )
                .order_by(ExecutionNode.id)
            )
        ).scalars().all()
    ) if suite_ids or case_ids else []
    exclusions = list(
        (
            await db.execute(
                select(ExecutionExclusion)
                .where(ExecutionExclusion.execution_id == source.id)
                .order_by(ExecutionExclusion.id)
            )
        ).scalars().all()
    )
    _validate_snapshot_tree(suites, cases, suite_steps, case_steps, assertions, nodes)

    retry = await create(
        db,
        fields={
            "project_id": source.project_id,
            "type": source.type,
            "suite_id": source.suite_id,
            "case_id": source.case_id,
            "device_id": device_id,
            "status": "queued",
            "parameters": deepcopy(source.parameters or {}),
            "sensitive_variable_names": deepcopy(source.sensitive_variable_names or []),
            "timeout_seconds": timeout_seconds,
            "created_by": user_id,
            "retry_of": source.id,
            "app_profile_id": source.app_profile_id,
            "app_profile_name_snapshot": source.app_profile_name_snapshot,
            "app_release_id": source.app_release_id,
            "app_release_version_snapshot": source.app_release_version_snapshot,
            "profile_revision": source.profile_revision,
            "test_asset_revision": source.test_asset_revision,
            "profile_resolution_summary": deepcopy(source.profile_resolution_summary or {}),
        },
    )
    suite_map: dict[int, ExecutionSuite] = {}
    for source_suite in suites:
        target_suite = ExecutionSuite(
            execution_id=retry.id,
            suite_id=source_suite.suite_id,
            suite_name=source_suite.suite_name,
            suite_order=source_suite.suite_order,
            is_virtual=source_suite.is_virtual,
            status="pending",
            setup_steps_snapshot=deepcopy(source_suite.setup_steps_snapshot),
            teardown_steps_snapshot=deepcopy(source_suite.teardown_steps_snapshot),
            elements_snapshot=deepcopy(source_suite.elements_snapshot or {}),
        )
        db.add(target_suite)
        suite_map[source_suite.id] = target_suite
    await db.flush()

    case_map: dict[int, ExecutionCase] = {}
    for source_case in cases:
        target_case = ExecutionCase(
            execution_id=retry.id,
            execution_suite_id=suite_map[source_case.execution_suite_id].id,
            case_id=source_case.case_id,
            case_name=source_case.case_name,
            module_name=source_case.module_name,
            case_order=source_case.case_order,
            status="pending",
            steps_snapshot=deepcopy(source_case.steps_snapshot),
            flow_snapshot=deepcopy(source_case.flow_snapshot),
            elements_snapshot=deepcopy(source_case.elements_snapshot or {}),
        )
        db.add(target_case)
        case_map[source_case.id] = target_case
    await db.flush()

    step_map: dict[int, ExecutionStep] = {}
    for source_step in all_steps:
        target_step = ExecutionStep(
            execution_suite_id=(
                suite_map[source_step.execution_suite_id].id
                if source_step.execution_suite_id is not None
                else None
            ),
            execution_case_id=(
                case_map[source_step.execution_case_id].id
                if source_step.execution_case_id is not None
                else None
            ),
            phase=source_step.phase,
            step_order=source_step.step_order,
            action=source_step.action,
            source_key=source_step.source_key,
            source_order=source_step.source_order,
            parameters=deepcopy(source_step.parameters or {}),
            sensitive_parameter_paths=deepcopy(source_step.sensitive_parameter_paths or []),
            continue_on_failure=source_step.continue_on_failure,
            status="pending",
        )
        db.add(target_step)
        step_map[source_step.id] = target_step
    await db.flush()

    for source_assertion in assertions:
        db.add(
            ExecutionAssertion(
                execution_step_id=step_map[source_assertion.execution_step_id].id,
                assertion_order=source_assertion.assertion_order,
                assertion_type=source_assertion.assertion_type,
                expected_value=source_assertion.expected_value,
                sensitive_parameter_paths=deepcopy(source_assertion.sensitive_parameter_paths or []),
                status="pending",
            )
        )
    await db.flush()

    node_objects: dict[int, ExecutionNode] = {}
    for source_node in nodes:
        target_node = ExecutionNode(
            execution_suite_id=(
                suite_map[source_node.execution_suite_id].id
                if source_node.execution_suite_id is not None
                else None
            ),
            execution_case_id=(
                case_map[source_node.execution_case_id].id
                if source_node.execution_case_id is not None
                else None
            ),
            kind=source_node.kind,
            node_order=source_node.node_order,
            phase=source_node.phase,
            node_key=source_node.node_key,
            action=source_node.action,
            assertion_type=source_node.assertion_type,
            description=source_node.description,
            element_id=source_node.element_id,
            parameters=deepcopy(source_node.parameters or {}),
            sensitive_parameter_paths=deepcopy(source_node.sensitive_parameter_paths or []),
            max_wait_seconds=source_node.max_wait_seconds,
            continue_on_failure=source_node.continue_on_failure,
            status="pending",
            expected_value=source_node.expected_value,
        )
        db.add(target_node)
        node_objects[source_node.id] = target_node
    await db.flush()
    node_map = {source_id: node.id for source_id, node in node_objects.items()}
    for source_case in cases:
        target_case = case_map[source_case.id]
        target_case.flow_snapshot = _remap_snapshot_node_ids(
            source_case.flow_snapshot, node_map
        )
        target_case.steps_snapshot = _remap_snapshot_node_ids(
            source_case.steps_snapshot, node_map
        )
    for source_suite in suites:
        target_suite = suite_map[source_suite.id]
        target_suite.setup_steps_snapshot = _remap_snapshot_node_ids(
            source_suite.setup_steps_snapshot, node_map
        )
        target_suite.teardown_steps_snapshot = _remap_snapshot_node_ids(
            source_suite.teardown_steps_snapshot, node_map
        )
    for source_exclusion in exclusions:
        db.add(
            ExecutionExclusion(
                execution_id=retry.id,
                app_profile_id=source_exclusion.app_profile_id,
                target_type=source_exclusion.target_type,
                suite_id_snapshot=source_exclusion.suite_id_snapshot,
                suite_name_snapshot=source_exclusion.suite_name_snapshot,
                case_id_snapshot=source_exclusion.case_id_snapshot,
                case_name_snapshot=source_exclusion.case_name_snapshot,
                occurrence_order=source_exclusion.occurrence_order,
                node_key=source_exclusion.node_key,
                node_name_snapshot=source_exclusion.node_name_snapshot,
                source_type=source_exclusion.source_type,
                source_rule_id=source_exclusion.source_rule_id,
                reason_code=source_exclusion.reason_code,
                reason_note=source_exclusion.reason_note,
                details=deepcopy(source_exclusion.details or {}),
            )
        )
    await enqueue(db, retry.id)
    return retry


async def list_page(
    db: AsyncSession, *, project_ids: list[int], project_id: int | None,
    status: str, type_: str, keyword: str, device_id: int | None,
    created_from, created_to, offset: int, limit: int,
) -> tuple[int, list[Execution], dict[str, dict[int, str]]]:
    query = select(Execution)
    query = query.where(Execution.project_id == project_id) if project_id is not None else query.where(Execution.project_id.in_(project_ids))
    if status:
        query = query.where(Execution.status == status)
    if type_:
        query = query.where(Execution.type == type_)
    if device_id is not None:
        query = query.where(Execution.device_id == device_id)
    if created_from is not None:
        query = query.where(Execution.created_at >= created_from)
    if created_to is not None:
        query = query.where(Execution.created_at <= created_to)
    if keyword:
        like = f"%{keyword}%"
        name_cond = Execution.case_id.in_(select(TestCase.id).where(TestCase.name.ilike(like))) | Execution.suite_id.in_(select(TestSuite.id).where(TestSuite.name.ilike(like)))
        query = query.where((Execution.id == int(keyword)) | name_cond if keyword.isdigit() else name_cond)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = list((await db.execute(query.order_by(Execution.id.desc()).offset(offset).limit(limit))).scalars().all())
    ids = {"case": {r.case_id for r in rows if r.case_id}, "suite": {r.suite_id for r in rows if r.suite_id}, "project": {r.project_id for r in rows}, "device": {r.device_id for r in rows if r.device_id}, "creator": {r.created_by for r in rows if r.created_by}}
    names = {
        "case": {x.id: x.name for x in (await db.execute(select(TestCase).where(TestCase.id.in_(ids["case"])))) .scalars().all()} if ids["case"] else {},
        "suite": {x.id: x.name for x in (await db.execute(select(TestSuite).where(TestSuite.id.in_(ids["suite"])))) .scalars().all()} if ids["suite"] else {},
        "project": {x.id: x.name for x in (await db.execute(select(Project).where(Project.id.in_(ids["project"])))) .scalars().all()} if ids["project"] else {},
        "device": {x.id: x.name for x in (await db.execute(select(Device).where(Device.id.in_(ids["device"])))) .scalars().all()} if ids["device"] else {},
        "creator": {x.id: x.username for x in (await db.execute(select(User).where(User.id.in_(ids["creator"])))) .scalars().all()} if ids["creator"] else {},
    }
    return total or 0, rows, names


async def list_logs(db: AsyncSession, execution_id: int, after_timestamp, offset: int, limit: int) -> tuple[list[ExecutionLog], int]:
    query = select(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
    count_query = select(func.count()).select_from(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
    if after_timestamp is not None:
        query = query.where(ExecutionLog.created_at > after_timestamp)
        count_query = count_query.where(ExecutionLog.created_at > after_timestamp)
    total = await db.scalar(count_query)
    rows = list((await db.execute(query.order_by(ExecutionLog.created_at.asc(), ExecutionLog.id.asc()).offset(offset).limit(limit))).scalars().all())
    return rows, total or 0


def parse_artifact_reference(reference: str) -> tuple[str, int] | None:
    """解析带类型的执行附件标识（``step:123`` / ``node:456``）。"""
    kind, separator, raw_id = str(reference or "").partition(":")
    if not separator or kind not in {"step", "node"}:
        return None
    try:
        artifact_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    return (kind, artifact_id) if artifact_id > 0 else None


async def get_artifact(
    db: AsyncSession, execution_id: int, reference: str
) -> "ExecutionStep | ExecutionNode | None":
    """按带类型标识读取属于当前执行的步骤或统一节点附件。"""
    from app.models import ExecutionCase, ExecutionNode, ExecutionStep, ExecutionSuite

    parsed = parse_artifact_reference(reference)
    if parsed is None:
        return None
    kind, artifact_id = parsed
    model = ExecutionStep if kind == "step" else ExecutionNode
    artifact = await db.get(model, artifact_id)
    if artifact is None:
        return None
    if artifact.execution_case_id is not None:
        parent = await db.get(ExecutionCase, artifact.execution_case_id)
    elif artifact.execution_suite_id is not None:
        parent = await db.get(ExecutionSuite, artifact.execution_suite_id)
    else:
        return None
    return artifact if parent is not None and parent.execution_id == execution_id else None


async def stop_queued(db: AsyncSession, execution_id: int, now) -> int:
    result = await db.execute(update(Execution).where(Execution.id == execution_id, Execution.status == "queued").values(status="cancelled", finished_at=now, stop_requested_at=now, termination_reason="user_stop", finalized_at=now))
    await db.execute(update(ExecutionQueue).where(ExecutionQueue.execution_id == execution_id).values(status="done"))
    return int(getattr(result, "rowcount", 0))


async def stop_running(db: AsyncSession, execution_id: int, now) -> int:
    result = await db.execute(update(Execution).where(Execution.id == execution_id, Execution.status == "running").values(status="stopping", stop_requested_at=now, termination_reason="user_stop"))
    return int(getattr(result, "rowcount", 0))


async def request_timeout_stop(db: AsyncSession, execution_id: int, now: datetime) -> bool:
    """Atomically enter the timeout stopping phase without finalizing children."""
    result = await db.execute(
        update(Execution)
        .where(
            Execution.id == execution_id,
            Execution.status == "running",
            Execution.finalized_at.is_(None),
            Execution.timeout_requested_at.is_(None),
        )
        .values(
            status="stopping",
            stop_requested_at=now,
            timeout_requested_at=now,
            termination_reason="timeout",
        )
        .returning(Execution.id)
    )
    return result.scalar_one_or_none() is not None


async def claim_stop_command(
    db: AsyncSession,
    execution_id: int,
    now: datetime,
    retry_before: datetime,
) -> bool:
    """Claim one stop_test attempt, with a persisted cross-worker backoff."""
    result = await db.execute(
        update(Execution)
        .where(
            Execution.id == execution_id,
            Execution.status == "stopping",
            Execution.finalized_at.is_(None),
            or_(
                Execution.stop_command_sent_at.is_(None),
                Execution.stop_command_sent_at < retry_before,
            ),
        )
        .values(stop_command_sent_at=now)
        .returning(Execution.id)
    )
    return result.scalar_one_or_none() is not None


async def claim_running(db: AsyncSession, execution_id: int, session_token: str, now) -> bool:
    result = await db.execute(update(Execution).where(Execution.id == execution_id, Execution.status == "queued").values(status="running", session_token=session_token, started_at=now).returning(Execution.id))
    return result.scalar_one_or_none() is not None


async def begin_dispatch(
    db: AsyncSession, execution_id: int, session_token: str, now
) -> bool:
    """取得唯一的 start_test 下发权（reserved -> dispatching）。"""
    result = await db.execute(
        update(Execution)
        .where(
            Execution.id == execution_id,
            Execution.status == "running",
            Execution.dispatch_state == "reserved",
            Execution.session_token == session_token,
            Execution.finalized_at.is_(None),
        )
        .values(dispatch_state="dispatching", dispatch_started_at=now)
        .returning(Execution.id)
    )
    return result.scalar_one_or_none() is not None


async def mark_dispatched(
    db: AsyncSession, execution_id: int, session_token: str, now
) -> bool:
    """记录 start_test 已成功写入 Agent WebSocket。"""
    result = await db.execute(
        update(Execution)
        .where(
            Execution.id == execution_id,
            Execution.dispatch_state == "dispatching",
            Execution.session_token == session_token,
            Execution.status.in_(("running", "stopping")),
            Execution.finalized_at.is_(None),
        )
        .values(dispatch_state="dispatched", dispatched_at=now)
        .returning(Execution.id)
    )
    return result.scalar_one_or_none() is not None


async def restore_reserved_execution(
    db: AsyncSession,
    execution_id: int,
    *,
    worker_id: str,
    session_token: str,
    dispatch_state: str = "reserved",
) -> bool:
    """恢复尚未确认下发的执行，并原子释放设备与队列认领。"""
    now = datetime.now(UTC)
    restored = await db.execute(
        update(Execution)
        .where(
            Execution.id == execution_id,
            Execution.status == "running",
            Execution.finalized_at.is_(None),
            Execution.session_token == session_token,
            Execution.dispatch_state == dispatch_state,
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
    locked_devices = (
        await db.execute(
            select(Device).where(Device.locked_by_execution == execution_id)
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
            ExecutionQueue.execution_id == execution_id,
            ExecutionQueue.status == "claimed",
            ExecutionQueue.claimed_by == worker_id,
        )
        .values(status="pending", claimed_by=None, claimed_at=None)
    )
    if int(getattr(queue_result, "rowcount", 0)) != 1:
        raise RuntimeError(
            f"恢复执行 {execution_id} 时队列认领不匹配，事务必须回滚"
        )
    return True


async def mark_queue_done(db: AsyncSession, execution_id: int) -> None:
    await db.execute(update(ExecutionQueue).where(ExecutionQueue.execution_id == execution_id).values(status="done"))


async def delete_logs_before(db: AsyncSession, cutoff) -> int:
    result = await db.execute(delete(ExecutionLog).where(ExecutionLog.created_at < cutoff))
    return int(getattr(result, "rowcount", 0))
