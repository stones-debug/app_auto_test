"""执行详情 case tree 聚合（Step 8：常数级查询）。

固定 3 条查询，不再随 case/step 数量增长：
1. 一次查询 ExecutionCase；
2. 一次按 case IDs 查询全部 ExecutionStep；
3. 一次 join ExecutionStep 查询全部 ExecutionAssertion；
随后在 Python 中按 execution_case_id / step_id 分组并保持 order。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExecutionAssertion, ExecutionCase, ExecutionStep


async def load_case_tree(db: AsyncSession, execution_id: int) -> list[dict]:
    """返回 execution 的 case tree（dict 形式，供 API 与报告聚合复用）。"""
    case_rows = (
        await db.execute(
            select(ExecutionCase)
            .where(ExecutionCase.execution_id == execution_id)
            .order_by(ExecutionCase.id)
        )
    ).scalars().all()
    if not case_rows:
        return []

    case_ids = [c.id for c in case_rows]
    steps = (
        await db.execute(
            select(ExecutionStep)
            .where(ExecutionStep.execution_case_id.in_(case_ids))
            .order_by(ExecutionStep.execution_case_id, ExecutionStep.step_order)
        )
    ).scalars().all()
    assertion_rows = ()
    if steps:
        assertion_rows = (
            await db.execute(
                select(ExecutionAssertion, ExecutionStep.execution_case_id)
                .join(ExecutionStep, ExecutionAssertion.execution_step_id == ExecutionStep.id)
                .where(ExecutionStep.execution_case_id.in_(case_ids))
                .order_by(ExecutionAssertion.id)
            )
        ).all()

    step_by_case: dict[int, list[dict]] = {}
    snapshot_parameters: dict[tuple[int, int], dict] = {}
    for case in case_rows:
        for item in case.steps_snapshot or []:
            if not isinstance(item, dict):
                continue
            order = item.get("order") or item.get("step_order")
            params = item.get("params")
            if isinstance(order, int) and isinstance(params, dict):
                snapshot_parameters[(case.id, order)] = params
    for s in steps:
        case_steps = step_by_case.setdefault(s.execution_case_id, [])
        case_steps.append(
            {
                "id": s.id,
                "step_order": s.step_order,
                "action": s.action,
                "parameters": s.parameters
                or snapshot_parameters.get((s.execution_case_id, s.step_order), {}),
                "status": s.status,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "duration": s.duration,
                "actual_value": s.actual_value,
                "error_message": s.error_message,
                "screenshot_path": s.screenshot_path,
            }
        )
    assertion_by_case: dict[int, list[dict]] = {}
    for a, execution_case_id in assertion_rows:
        case_assertions = assertion_by_case.setdefault(execution_case_id, [])
        case_assertions.append(
            {
                "id": a.id,
                "execution_step_id": a.execution_step_id,
                "assertion_type": a.assertion_type,
                "expected_value": a.expected_value,
                "actual_value": a.actual_value,
                "status": a.status,
                "error_message": a.error_message,
            }
        )

    result: list[dict] = []
    for c in case_rows:
        steps_out = []
        for s in step_by_case.get(c.id, []):
            steps_out.append(
                {
                    "id": s["id"],
                    "step_order": s["step_order"],
                    "action": s["action"],
                    "parameters": s["parameters"],
                    "status": s["status"],
                    "duration": s["duration"],
                    "actual_value": s["actual_value"],
                    "error_message": s["error_message"],
                    "screenshot_path": s["screenshot_path"],
                }
            )
        result.append(
            {
                "id": c.id,
                "case_id": c.case_id,
                "case_name": c.case_name,
                "module_name": c.module_name,
                "status": c.status,
                "started_at": c.started_at,
                "finished_at": c.finished_at,
                "duration": c.duration,
                "error_message": c.error_message,
                "elements": c.elements_snapshot or {},
                "steps": steps_out,
                "assertions": assertion_by_case.get(c.id, []),
            }
        )
    return result
