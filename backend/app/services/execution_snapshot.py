"""执行快照固化（方案 §6.1/§6.2）。

与 Execution 创建同事务调用 `materialize_snapshot`，写入：
ExecutionCase（完整快照）、ExecutionStep（pending）、ExecutionExclusion、ExecutionQueue。
事务失败不得留下无队列执行或空快照执行。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Execution, ExecutionCase, ExecutionStep
from app.services.profile_resolver import ResolutionResult


async def materialize_snapshot(
    db: AsyncSession, execution: Execution, result: ResolutionResult
) -> None:
    """把解析结果固化到 ExecutionCase/ExecutionStep（pending），并补队列项。

    调用方（execution_service）负责与 Execution/ExecutionQueue/ExecutionExclusion 的
    写入处于同一事务；本函数只写入用例快照与步骤，事务控制交由上层。
    """
    for case in result.cases:
        exec_case = ExecutionCase(
            execution_id=execution.id,
            case_id=case.case_id,
            case_name=case.case_name,
            module_name=case.module_name,
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
                    step_order=int(step.get("order") or 0),
                    action=step.get("action") or "",
                    parameters=step.get("params") or {},
                    status="pending",
                )
            )
