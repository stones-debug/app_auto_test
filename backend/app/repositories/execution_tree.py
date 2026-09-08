"""执行快照、执行详情树及状态归并的数据访问。"""

import logging
import time
from copy import deepcopy

from sqlalchemy import case as sql_case
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import (
    ExecutionAssertion,
    ExecutionCase,
    ExecutionNode,
    ExecutionStep,
    ExecutionSuite,
)

logger = logging.getLogger("app.execution_tree")


def _node_phase_rank():
    """统一节点阶段顺序，避免按 phase 字符串字典序排序。"""
    return sql_case(
        (ExecutionNode.phase == "suite_setup", 0),
        (ExecutionNode.phase == "case_setup", 1),
        (ExecutionNode.phase == "case_main", 2),
        (ExecutionNode.phase == "case_teardown", 3),
        (ExecutionNode.phase == "suite_teardown", 4),
        else_=99,
    )


async def materialize_snapshot(db: AsyncSession, execution, result) -> None:
    started = time.monotonic()
    suites = [suite for suite in result.suites if not suite.is_na]
    execution_suites = [
        ExecutionSuite(
            execution_id=execution.id, suite_id=suite.suite_id, suite_name=suite.suite_name,
            suite_order=suite.suite_order, is_virtual=suite.is_virtual, status="pending",
            setup_steps_snapshot=suite.setup_steps_snapshot,
            teardown_steps_snapshot=suite.teardown_steps_snapshot,
            elements_snapshot=suite.elements_snapshot,
        )
        for suite in suites
    ]
    db.add_all(execution_suites)
    await db.flush()
    flush_count = 1

    suite_nodes: list[ExecutionNode] = []
    for suite, execution_suite in zip(suites, execution_suites, strict=True):
        for phase_steps in (suite.setup_steps_snapshot, suite.teardown_steps_snapshot):
            for node_order, step in enumerate(phase_steps, start=1):
                suite_nodes.append(ExecutionNode(
                    execution_suite_id=execution_suite.id,
                    kind="action", node_order=node_order,
                    phase=step.get("phase") or "suite_setup",
                    node_key=step.get("source_key") or step.get("key"),
                    action=step.get("action") or "",
                    description=step.get("description"),
                    element_id=step.get("element_id"), parameters=step.get("params") or {},
                    continue_on_failure=bool(step.get("continue_on_failure", False)), status="pending",
                ))

    execution_cases: list[ExecutionCase] = []
    case_node_refs: list[tuple[ExecutionCase, dict, dict]] = []
    case_inputs = [
        (suite, execution_suite, case)
        for suite, execution_suite in zip(suites, execution_suites, strict=True)
        for case in suite.cases
    ]
    for _suite, execution_suite, case in case_inputs:
        flow_snapshot = deepcopy(case.flow_snapshot)
        execution_case = ExecutionCase(
            execution_id=execution.id, execution_suite_id=execution_suite.id, case_id=case.case_id,
            case_name=case.case_name, module_name=case.module_name, case_order=case.case_order,
            status="pending", steps_snapshot=deepcopy(case.steps_snapshot),
            elements_snapshot=deepcopy(case.elements_snapshot), flow_snapshot=flow_snapshot,
        )
        execution_cases.append(execution_case)
        for node, snapshot_node in zip(case.flow_snapshot, flow_snapshot, strict=True):
            case_node_refs.append((execution_case, node, snapshot_node))

    db.add_all([*suite_nodes, *execution_cases])
    await db.flush()
    flush_count += 1

    case_nodes: list[ExecutionNode] = []
    for execution_case, _source_node, snapshot_node in case_node_refs:
        case_nodes.append(ExecutionNode(
            execution_case_id=execution_case.id,
            kind=snapshot_node.get("kind") or ("assertion" if snapshot_node.get("type") else "action"),
            node_order=int(snapshot_node.get("order") or 0),
            phase=snapshot_node.get("phase") or "case_main",
            node_key=snapshot_node.get("source_key") or snapshot_node.get("key"),
            action=snapshot_node.get("action"),
            assertion_type=snapshot_node.get("type") or snapshot_node.get("assertion_type"),
            description=snapshot_node.get("description"),
            element_id=snapshot_node.get("element_id"),
            parameters=snapshot_node.get("params") or snapshot_node.get("parameters") or {},
            max_wait_seconds=snapshot_node.get("max_wait_seconds"),
            continue_on_failure=bool(snapshot_node.get("continue_on_failure", False)), status="pending",
            expected_value=(
                str((snapshot_node.get("params") or {}).get("expected"))
                if snapshot_node.get("kind") == "assertion"
                and (snapshot_node.get("params") or {}).get("expected") is not None
                else None
            ),
        ))
    db.add_all(case_nodes)
    await db.flush()
    flush_count += 1
    for (_, _source_node, snapshot_node), execution_node in zip(case_node_refs, case_nodes, strict=True):
        snapshot_node["execution_node_id"] = execution_node.id
    for execution_case in execution_cases:
        flag_modified(execution_case, "flow_snapshot")

    execution_steps: list[ExecutionStep] = []
    step_assertions: list[tuple[ExecutionStep, dict]] = []
    for execution_case, (_suite, _execution_suite, case) in zip(
        execution_cases, case_inputs, strict=True
    ):
        for step in case.steps_snapshot:
            execution_step = ExecutionStep(
                execution_case_id=execution_case.id, phase=step.get("phase") or "case_main",
                step_order=int(step.get("order") or 0), action=step.get("action") or "",
                source_key=step.get("source_key"), source_order=step.get("source_order"),
                parameters=deepcopy(step.get("params") or {}),
                continue_on_failure=bool(step.get("continue_on_failure", False)), status="pending",
            )
            execution_steps.append(execution_step)
            step_assertions.extend((execution_step, assertion) for assertion in step.get("assertions") or [])
    db.add_all(execution_steps)
    await db.flush()
    flush_count += 1

    assertions = []
    for execution_step, assertion in step_assertions:
        expected = assertion.get("expected")
        if expected is None:
            expected = assertion.get("expected_value")
        if expected is None:
            expected = (assertion.get("params") or {}).get("expected")
        assertions.append(ExecutionAssertion(
            execution_step_id=execution_step.id, assertion_order=int(assertion.get("order") or 0),
            assertion_type=assertion.get("type") or assertion.get("assertion_type") or "",
            expected_value=str(expected) if expected is not None else None, status="pending",
        ))
    db.add_all(assertions)
    logger.info(
        "execution_snapshot stage=materialize execution_id=%s suite_count=%s case_count=%s "
        "node_count=%s step_count=%s assertion_count=%s flush_count=%s elapsed_ms=%.1f",
        execution.id, len(execution_suites), len(execution_cases),
        len(suite_nodes) + len(case_nodes), len(execution_steps), len(assertions),
        flush_count, (time.monotonic() - started) * 1000,
    )


async def load_case_tree_rows(db: AsyncSession, execution_id: int):
    cases = list((await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == execution_id).order_by(ExecutionCase.id))).scalars().all())
    if not cases:
        return [], [], []
    case_ids = [case.id for case in cases]
    steps = list((await db.execute(select(ExecutionStep).where(ExecutionStep.execution_case_id.in_(case_ids)).order_by(ExecutionStep.execution_case_id, ExecutionStep.step_order))).scalars().all())
    step_ids = [step.id for step in steps]
    assertions = list((await db.execute(select(ExecutionAssertion).where(ExecutionAssertion.execution_step_id.in_(step_ids)).order_by(ExecutionAssertion.execution_step_id, ExecutionAssertion.assertion_order))).scalars().all()) if step_ids else []
    return cases, steps, assertions


async def load_case_nodes(db: AsyncSession, case_ids: list[int]) -> list[ExecutionNode]:
    if not case_ids:
        return []
    return list(
        (
            await db.execute(
                select(ExecutionNode)
                .where(ExecutionNode.execution_case_id.in_(case_ids))
                .order_by(ExecutionNode.execution_case_id, _node_phase_rank(), ExecutionNode.node_order)
            )
        ).scalars().all()
    )


async def load_suite_tree_rows(db: AsyncSession, execution_id: int):
    suites = list((await db.execute(select(ExecutionSuite).where(ExecutionSuite.execution_id == execution_id).order_by(ExecutionSuite.suite_order))).scalars().all())
    if not suites:
        return [], [], [], [], []
    suite_ids = [suite.id for suite in suites]
    cases = list((await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == execution_id).order_by(ExecutionCase.execution_suite_id, ExecutionCase.case_order))).scalars().all())
    case_ids = [case.id for case in cases]
    suite_steps = list((await db.execute(select(ExecutionStep).where(ExecutionStep.execution_suite_id.in_(suite_ids)).order_by(ExecutionStep.execution_suite_id, ExecutionStep.phase, ExecutionStep.step_order))).scalars().all())
    case_steps = list((await db.execute(select(ExecutionStep).where(ExecutionStep.execution_case_id.in_(case_ids)).order_by(ExecutionStep.execution_case_id, ExecutionStep.step_order))).scalars().all()) if case_ids else []
    assertions = list((await db.execute(select(ExecutionAssertion).where(ExecutionAssertion.execution_step_id.in_([step.id for step in case_steps])).order_by(ExecutionAssertion.execution_step_id, ExecutionAssertion.assertion_order))).scalars().all()) if case_steps else []
    return suites, cases, suite_steps, case_steps, assertions


async def load_suite_nodes(
    db: AsyncSession, suite_ids: list[int], case_ids: list[int]
) -> list[ExecutionNode]:
    if not suite_ids and not case_ids:
        return []
    conditions = []
    if suite_ids:
        conditions.append(ExecutionNode.execution_suite_id.in_(suite_ids))
    if case_ids:
        conditions.append(ExecutionNode.execution_case_id.in_(case_ids))
    from sqlalchemy import or_

    return list(
        (
            await db.execute(
                select(ExecutionNode)
                .where(or_(*conditions))
                .order_by(
                    ExecutionNode.execution_suite_id,
                    ExecutionNode.execution_case_id,
                    _node_phase_rank(),
                    ExecutionNode.node_order,
                )
            )
        ).scalars().all()
    )
