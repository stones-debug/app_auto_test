"""执行快照、执行详情树及状态归并的数据访问。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ExecutionAssertion,
    ExecutionCase,
    ExecutionNode,
    ExecutionStep,
    ExecutionSuite,
)


async def materialize_snapshot(db: AsyncSession, execution, result) -> None:
    for suite in result.suites:
        if suite.is_na:
            continue
        exec_suite = ExecutionSuite(
            execution_id=execution.id, suite_id=suite.suite_id, suite_name=suite.suite_name,
            suite_order=suite.suite_order, is_virtual=suite.is_virtual, status="pending",
            setup_steps_snapshot=suite.setup_steps_snapshot, teardown_steps_snapshot=suite.teardown_steps_snapshot,
            elements_snapshot=suite.elements_snapshot,
        )
        db.add(exec_suite)
        await db.flush()
        suite_node_order = 0
        for phase_steps in (suite.setup_steps_snapshot, suite.teardown_steps_snapshot):
            for step in phase_steps:
                suite_node_order += 1
                db.add(ExecutionNode(
                    execution_suite_id=exec_suite.id,
                    kind="action", node_order=suite_node_order,
                    phase=step.get("phase") or "suite_setup",
                    node_key=step.get("source_key") or step.get("key"),
                    action=step.get("action") or "",
                    description=step.get("description"),
                    element_id=step.get("element_id"), parameters=step.get("params") or {},
                    continue_on_failure=bool(step.get("continue_on_failure", False)), status="pending",
                ))
                db.add(ExecutionStep(
                    execution_suite_id=exec_suite.id, phase=step.get("phase") or "suite_setup",
                    step_order=int(step.get("order") or 0), action=step.get("action") or "",
                    source_key=step.get("source_key"), source_order=step.get("source_order"),
                    parameters=step.get("params") or {}, continue_on_failure=bool(step.get("continue_on_failure", False)), status="pending",
                ))
        for case in suite.cases:
            exec_case = ExecutionCase(
                execution_id=execution.id, execution_suite_id=exec_suite.id, case_id=case.case_id,
                case_name=case.case_name, module_name=case.module_name, case_order=case.case_order,
            status="pending", steps_snapshot=case.steps_snapshot, elements_snapshot=case.elements_snapshot,
            flow_snapshot=case.flow_snapshot,
        )
            db.add(exec_case)
            await db.flush()
            for node in case.flow_snapshot:
                execution_node = ExecutionNode(
                    execution_case_id=exec_case.id,
                    kind=node.get("kind") or ("assertion" if node.get("type") else "action"),
                    node_order=int(node.get("order") or 0),
                    phase=node.get("phase") or "case_main",
                    node_key=node.get("source_key") or node.get("key"),
                    action=node.get("action"), assertion_type=node.get("type") or node.get("assertion_type"),
                    description=node.get("description"),
                    element_id=node.get("element_id"),
                    parameters=node.get("params") or node.get("parameters") or {},
                    max_wait_seconds=node.get("max_wait_seconds"),
                    continue_on_failure=bool(node.get("continue_on_failure", False)), status="pending",
                    expected_value=str((node.get("params") or {}).get("expected")) if node.get("kind") == "assertion" and (node.get("params") or {}).get("expected") is not None else None,
                )
                db.add(execution_node)
                await db.flush()
                node["execution_node_id"] = execution_node.id
            exec_case.flow_snapshot = case.flow_snapshot
            for step in case.steps_snapshot:
                exec_step = ExecutionStep(
                    execution_case_id=exec_case.id, phase=step.get("phase") or "case_main",
                    step_order=int(step.get("order") or 0), action=step.get("action") or "",
                    source_key=step.get("source_key"), source_order=step.get("source_order"),
                    parameters=step.get("params") or {}, continue_on_failure=bool(step.get("continue_on_failure", False)), status="pending",
                )
                db.add(exec_step)
                await db.flush()
                for assertion in step.get("assertions") or []:
                    expected = assertion.get("expected")
                    if expected is None:
                        expected = assertion.get("expected_value")
                    if expected is None:
                        expected = (assertion.get("params") or {}).get("expected")
                    db.add(ExecutionAssertion(
                        execution_step_id=exec_step.id, assertion_order=int(assertion.get("order") or 0),
                        assertion_type=assertion.get("type") or assertion.get("assertion_type") or "",
                        expected_value=str(expected) if expected is not None else None, status="pending",
                    ))


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
                .order_by(ExecutionNode.execution_case_id, ExecutionNode.phase, ExecutionNode.node_order)
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
                    ExecutionNode.phase,
                    ExecutionNode.node_order,
                )
            )
        ).scalars().all()
    )
