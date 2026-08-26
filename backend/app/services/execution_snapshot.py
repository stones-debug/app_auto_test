"""执行快照固化（方案 §6.1/§6.2）。

与 Execution 创建同事务调用 `materialize_snapshot`，写入：
ExecutionSuite（含前置/后置步骤与元素）、ExecutionCase、ExecutionStep（pending）、ExecutionAssertion。
N/A 套件（is_na=True）不落库。事务失败不得留下无队列执行或空快照执行。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Execution, ExecutionAssertion, ExecutionCase, ExecutionStep, ExecutionSuite
from app.services.profile_resolver import ResolutionResult


async def materialize_snapshot(
    db: AsyncSession, execution: Execution, result: ResolutionResult
) -> None:
    """把解析结果固化到 ExecutionSuite/ExecutionCase/ExecutionStep/ExecutionAssertion（pending）。

    调用方（execution_service）负责与 Execution/ExecutionQueue/ExecutionExclusion 的
    写入处于同一事务；本函数只写入套件/用例/步骤/断言快照，事务控制交由上层。
    """
    for suite in result.suites:
        if suite.is_na:
            continue
        exec_suite = ExecutionSuite(
            execution_id=execution.id,
            suite_id=suite.suite_id,
            suite_name=suite.suite_name,
            suite_order=suite.suite_order,
            is_virtual=suite.is_virtual,
            status="pending",
            setup_steps_snapshot=suite.setup_steps_snapshot,
            teardown_steps_snapshot=suite.teardown_steps_snapshot,
            elements_snapshot=suite.elements_snapshot,
        )
        db.add(exec_suite)
        await db.flush()

        for step in suite.setup_steps_snapshot:
            db.add(
                ExecutionStep(
                    execution_suite_id=exec_suite.id,
                    phase=step.get("phase") or "suite_setup",
                    step_order=int(step.get("order") or 0),
                    action=step.get("action") or "",
                    source_key=step.get("source_key"),
                    source_order=step.get("source_order"),
                    parameters=step.get("params") or {},
                    continue_on_failure=bool(step.get("continue_on_failure", False)),
                    status="pending",
                )
            )
        for step in suite.teardown_steps_snapshot:
            db.add(
                ExecutionStep(
                    execution_suite_id=exec_suite.id,
                    phase=step.get("phase") or "suite_teardown",
                    step_order=int(step.get("order") or 0),
                    action=step.get("action") or "",
                    source_key=step.get("source_key"),
                    source_order=step.get("source_order"),
                    parameters=step.get("params") or {},
                    continue_on_failure=bool(step.get("continue_on_failure", False)),
                    status="pending",
                )
            )

        for case in suite.cases:
            exec_case = ExecutionCase(
                execution_id=execution.id,
                execution_suite_id=exec_suite.id,
                case_id=case.case_id,
                case_name=case.case_name,
                module_name=case.module_name,
                case_order=case.case_order,
                status="pending",
                steps_snapshot=case.steps_snapshot,
                assertions_snapshot=case.assertions_snapshot,
                elements_snapshot=case.elements_snapshot,
            )
            db.add(exec_case)
            await db.flush()

            for step in case.steps_snapshot:
                db.add(
                    ExecutionStep(
                        execution_case_id=exec_case.id,
                        phase=step.get("phase") or "case_main",
                        step_order=int(step.get("order") or 0),
                        action=step.get("action") or "",
                        source_key=step.get("source_key"),
                        source_order=step.get("source_order"),
                        parameters=step.get("params") or {},
                        continue_on_failure=bool(step.get("continue_on_failure", False)),
                        status="pending",
                    )
                )
            for assertion in case.assertions_snapshot:
                _expected = assertion.get("expected")
                if _expected is None:
                    _expected = assertion.get("expected_value")
                db.add(
                    ExecutionAssertion(
                        execution_case_id=exec_case.id,
                        assertion_order=int(assertion.get("order") or 0),
                        assertion_type=assertion.get("type") or assertion.get("assertion_type") or "",
                        expected_value=str(_expected) if _expected is not None else None,
                        status="pending",
                    )
                )
