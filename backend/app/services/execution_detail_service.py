"""执行详情 case tree 聚合（Step 8：常数级查询）。

固定 3 条查询，不再随 case/step 数量增长：
1. 一次查询 ExecutionCase；
2. 一次按 case IDs 查询全部 ExecutionStep；
3. 一次 join ExecutionStep 查询全部 ExecutionAssertion；
随后在 Python 中按 execution_case_id / step_id 分组并保持 order。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExecutionAssertion, ExecutionCase, ExecutionStep, ExecutionSuite


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
    step_ids = [step.id for step in steps]
    assertion_rows = ()
    if step_ids:
        assertion_rows = (
            await db.execute(
                select(ExecutionAssertion)
                .where(ExecutionAssertion.execution_step_id.in_(step_ids))
                .order_by(ExecutionAssertion.execution_step_id, ExecutionAssertion.assertion_order)
            )
        ).scalars().all()

    # 键中的 case_id 来自 ExecutionStep.execution_case_id（可空列：套件阶段步骤不挂用例）。
    # 本函数的 steps 已按 execution_case_id.in_(case_ids) 过滤，运行时不为 None，
    # 但类型系统无法感知，故键类型放宽为 int | None（纯标注，运行时行为不变）。
    step_by_case: dict[int | None, list[dict]] = {}
    snapshot_parameters: dict[tuple[int | None, int], dict] = {}
    snapshot_phases: dict[tuple[int | None, int], str] = {}
    # 断言快照：按 (case_id, order) 携带 params / description（断言行本身不落库这些字段）
    snapshot_assertion_info: dict[tuple[int | None, int, int], dict] = {}
    for case in case_rows:
        for item in case.steps_snapshot or []:
            if not isinstance(item, dict):
                continue
            order = item.get("order") or item.get("step_order")
            params = item.get("params")
            if isinstance(order, int) and isinstance(params, dict):
                snapshot_parameters[(case.id, order)] = params
            if isinstance(order, int):
                snapshot_phases[(case.id, order)] = str(item.get("phase") or "main")
            if not isinstance(order, int):
                continue
            for assertion in item.get("assertions") or []:
                assertion_order = assertion.get("order")
                if not isinstance(assertion_order, int):
                    continue
                info: dict = {}
                if isinstance(assertion.get("params"), dict):
                    info["params"] = assertion["params"]
                description = assertion.get("description")
                if isinstance(description, str) and description.strip():
                    info["description"] = description.strip()
                snapshot_assertion_info[(case.id, order, assertion_order)] = info
    for s in steps:
        case_steps = step_by_case.setdefault(s.execution_case_id, [])
        case_steps.append(
            {
                "id": s.id,
                "step_order": s.step_order,
                "action": s.action,
                "phase": snapshot_phases.get((s.execution_case_id, s.step_order), "main"),
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
    assertion_by_step: dict[int, list[dict]] = {}
    step_case_order = {s.id: (s.execution_case_id, s.step_order) for s in steps}
    for a in assertion_rows:
        case_id, step_order = step_case_order[a.execution_step_id]
        assertion_by_step.setdefault(a.execution_step_id, []).append(
            {
                "id": a.id,
                "assertion_order": a.assertion_order,
                "assertion_type": a.assertion_type,
                "expected_value": a.expected_value,
                "actual_value": a.actual_value,
                "status": a.status,
                "error_message": a.error_message,
                **snapshot_assertion_info.get((case_id, step_order, a.assertion_order), {}),
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
                    "phase": s["phase"],
                    "parameters": s["parameters"],
                    "status": s["status"],
                    "duration": s["duration"],
                    "actual_value": s["actual_value"],
                    "error_message": s["error_message"],
                    "screenshot_path": s["screenshot_path"],
                    "assertions": assertion_by_step.get(s["id"], []),
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
            }
        )
    return result


async def load_suite_tree(db: AsyncSession, execution_id: int) -> list[dict]:
    """返回 execution 的套件树（S1-7 §2：执行详情嵌套 suites 聚合）。

    每个 suite 含 setup_steps / teardown_steps（快照 JSON + ExecutionStep(套件阶段) 行合并状态）
    与 cases（steps/assertions）。常数级查询：套件、用例、用例步骤、套件步骤、断言各一次。
    """
    suite_rows = (
        await db.execute(
            select(ExecutionSuite)
            .where(ExecutionSuite.execution_id == execution_id)
            .order_by(ExecutionSuite.suite_order)
        )
    ).scalars().all()
    if not suite_rows:
        return []
    suite_ids = [s.id for s in suite_rows]
    cases = (
        await db.execute(
            select(ExecutionCase)
            .where(ExecutionCase.execution_id == execution_id)
            .order_by(ExecutionCase.execution_suite_id, ExecutionCase.case_order)
        )
    ).scalars().all()
    case_ids = [c.id for c in cases]

    suite_step_rows = (
        await db.execute(
            select(ExecutionStep)
            .where(ExecutionStep.execution_suite_id.in_(suite_ids))
            .order_by(ExecutionStep.execution_suite_id, ExecutionStep.phase, ExecutionStep.step_order)
        )
    ).scalars().all()
    case_step_rows = (
        await db.execute(
            select(ExecutionStep)
            .where(ExecutionStep.execution_case_id.in_(case_ids))
            .order_by(ExecutionStep.execution_case_id, ExecutionStep.step_order)
        )
    ).scalars().all() if case_ids else []
    assertion_rows = (
        await db.execute(
            select(ExecutionAssertion)
            .where(ExecutionAssertion.execution_step_id.in_([step.id for step in case_step_rows]))
            .order_by(ExecutionAssertion.execution_step_id, ExecutionAssertion.assertion_order)
        )
    ).scalars().all() if case_step_rows else []

    # 同 load_case_tree：键中的 case_id 来自可空列，运行时已过滤但类型系统无法感知
    snapshot_parameters: dict[tuple[int | None, int], dict] = {}
    snapshot_phases: dict[tuple[int | None, int], str] = {}
    # 断言快照：按 (case_id, order) 携带 params / description
    snapshot_assertion_info: dict[tuple[int | None, int, int], dict] = {}
    for c in cases:
        for item in c.steps_snapshot or []:
            if not isinstance(item, dict):
                continue
            order = item.get("order") or item.get("step_order")
            params = item.get("params")
            if isinstance(order, int) and isinstance(params, dict):
                snapshot_parameters[(c.id, order)] = params
            if isinstance(order, int):
                snapshot_phases[(c.id, order)] = str(item.get("phase") or "main")
            if not isinstance(order, int):
                continue
            for assertion in item.get("assertions") or []:
                assertion_order = assertion.get("order")
                if not isinstance(assertion_order, int):
                    continue
                info: dict = {}
                if isinstance(assertion.get("params"), dict):
                    info["params"] = assertion["params"]
                description = assertion.get("description")
                if isinstance(description, str) and description.strip():
                    info["description"] = description.strip()
                snapshot_assertion_info[(c.id, order, assertion_order)] = info
    suite_snapshot_parameters: dict[tuple[int | None, str, int], dict] = {}
    for s in suite_rows:
        for src, default_phase in (
            (s.setup_steps_snapshot or [], "suite_setup"),
            (s.teardown_steps_snapshot or [], "suite_teardown"),
        ):
            for item in src:
                if not isinstance(item, dict):
                    continue
                order = item.get("order") or item.get("step_order")
                params = item.get("params")
                phase = str(item.get("phase") or default_phase)
                if isinstance(order, int) and isinstance(params, dict):
                    suite_snapshot_parameters[(s.id, phase, order)] = params

    steps_by_case: dict[int | None, list] = {}
    for s in case_step_rows:
        steps_by_case.setdefault(s.execution_case_id, []).append(s)
    assertions_by_step: dict[int, list] = {}
    for a in assertion_rows:
        assertions_by_step.setdefault(a.execution_step_id, []).append(a)
    suite_steps_by_suite: dict[int | None, list] = {}
    for s in suite_step_rows:
        suite_steps_by_suite.setdefault(s.execution_suite_id, []).append(s)
    cases_by_suite: dict[int, list] = {}
    for c in cases:
        cases_by_suite.setdefault(c.execution_suite_id, []).append(c)

    def _mk_case(c: ExecutionCase) -> dict:
        steps_out = []
        for s in steps_by_case.get(c.id, []):
            steps_out.append(
                {
                    "id": s.id,
                    "step_order": s.step_order,
                    "action": s.action,
                    "phase": snapshot_phases.get((c.id, s.step_order), s.phase),
                    "parameters": s.parameters or snapshot_parameters.get((c.id, s.step_order), {}),
                    "status": s.status,
                    "started_at": s.started_at,
                    "finished_at": s.finished_at,
                    "duration": s.duration,
                    "actual_value": s.actual_value,
                    "error_message": s.error_message,
                    "screenshot_path": s.screenshot_path,
                    "assertions": [
                        {
                            "id": a.id,
                            "assertion_order": a.assertion_order,
                            "assertion_type": a.assertion_type,
                            "expected_value": a.expected_value,
                            "actual_value": a.actual_value,
                            "status": a.status,
                            "error_message": a.error_message,
                            **snapshot_assertion_info.get((c.id, s.step_order, a.assertion_order), {}),
                        }
                        for a in assertions_by_step.get(s.id, [])
                    ],
                }
            )
        return {
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
        }

    def _mk_suite_step(s: ExecutionStep) -> dict:
        phase = s.phase
        return {
            "id": s.id,
            "step_order": s.step_order,
            "action": s.action,
            "phase": phase,
            "parameters": s.parameters or suite_snapshot_parameters.get((s.execution_suite_id, phase, s.step_order), {}),
            "status": s.status,
            "started_at": s.started_at,
            "finished_at": s.finished_at,
            "duration": s.duration,
            "actual_value": s.actual_value,
            "error_message": s.error_message,
            "screenshot_path": s.screenshot_path,
        }

    result: list[dict] = []
    for suite in suite_rows:
        suite_steps = suite_steps_by_suite.get(suite.id, [])
        result.append(
            {
                "id": suite.id,
                "suite_id": suite.suite_id,
                "suite_name": suite.suite_name,
                "suite_order": suite.suite_order,
                "is_virtual": suite.is_virtual,
                "status": suite.status,
                "error_message": suite.error_message,
                "started_at": suite.started_at,
                "finished_at": suite.finished_at,
                "duration": suite.duration,
                "setup_steps": [_mk_suite_step(s) for s in suite_steps if s.phase == "suite_setup"],
                "teardown_steps": [_mk_suite_step(s) for s in suite_steps if s.phase == "suite_teardown"],
                "cases": [_mk_case(c) for c in cases_by_suite.get(suite.id, [])],
            }
        )
    return result
