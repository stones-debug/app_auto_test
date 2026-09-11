"""执行详情 case tree 聚合（Step 8：常数级查询）。

固定 3 条查询，不再随 case/step 数量增长：
1. 一次查询 ExecutionCase；
2. 一次按 case IDs 查询全部 ExecutionStep；
3. 一次 join ExecutionStep 查询全部 ExecutionAssertion；
随后在 Python 中按 execution_case_id / step_id 分组并保持 order。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExecutionCase, ExecutionStep
from app.repositories import execution_tree
from app.services.sensitive_snapshot import (
    REDACTED,
    mask_sensitive_parameters,
    sensitive_result,
)


async def load_case_tree(db: AsyncSession, execution_id: int) -> list[dict]:
    """返回 execution 的 case tree（dict 形式，供 API 与报告聚合复用）。"""
    case_rows, steps, assertion_rows = await execution_tree.load_case_tree_rows(db, execution_id)
    if not case_rows:
        return []
    node_rows = await execution_tree.load_case_nodes(db, [case.id for case in case_rows])
    nodes_by_case: dict[int, list[dict]] = {}
    for node in node_rows:
        if node.execution_case_id is None:
            continue
        sensitive_paths = node.sensitive_parameter_paths or []
        result_sensitive = bool(sensitive_paths)
        nodes_by_case.setdefault(node.execution_case_id, []).append({
            "id": node.id, "kind": node.kind, "node_order": node.node_order, "phase": node.phase,
            "node_key": node.node_key, "action": node.action, "assertion_type": node.assertion_type,
            "description": node.description,
            "element_id": node.element_id, "parameters": mask_sensitive_parameters(node.parameters or {}, sensitive_paths),
            "max_wait_seconds": node.max_wait_seconds, "continue_on_failure": node.continue_on_failure,
            "status": node.status, "started_at": node.started_at, "finished_at": node.finished_at,
            "duration": node.duration, "attempt_count": node.attempt_count,
            "actual_value": REDACTED if result_sensitive else node.actual_value,
            "expected_value": REDACTED if result_sensitive else node.expected_value,
            "error_message": REDACTED if result_sensitive else node.error_message,
            "screenshot_path": node.screenshot_path,
            "artifact_id": f"node:{node.id}" if node.screenshot_path else None,
        })

    # 键中的 case_id 来自 ExecutionStep.execution_case_id（可空列：套件阶段步骤不挂用例）。
    # 本函数的 steps 已按 execution_case_id.in_(case_ids) 过滤，运行时不为 None，
    # 但类型系统无法感知，故键类型放宽为 int | None（纯标注，运行时行为不变）。
    step_by_case: dict[int | None, list[dict]] = {}
    snapshot_parameters: dict[tuple[int | None, int], dict] = {}
    snapshot_sensitive_paths: dict[tuple[int | None, int], list] = {}
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
            if isinstance(order, int) and isinstance(item.get("sensitive_parameter_paths"), list):
                snapshot_sensitive_paths[(case.id, order)] = item["sensitive_parameter_paths"]
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
        step_paths = s.sensitive_parameter_paths or snapshot_sensitive_paths.get(
            (s.execution_case_id, s.step_order), []
        )
        step_sensitive = bool(step_paths)
        case_steps = step_by_case.setdefault(s.execution_case_id, [])
        case_steps.append(
            {
                "id": s.id,
                "step_order": s.step_order,
                "action": s.action,
                "phase": snapshot_phases.get((s.execution_case_id, s.step_order), "main"),
                "parameters": mask_sensitive_parameters(
                    s.parameters or snapshot_parameters.get((s.execution_case_id, s.step_order), {}),
                    step_paths,
                ),
                "status": s.status,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "duration": s.duration,
                "actual_value": REDACTED if step_sensitive else s.actual_value,
                "error_message": REDACTED if step_sensitive else s.error_message,
                "screenshot_path": s.screenshot_path,
            }
        )
    assertion_by_step: dict[int, list[dict]] = {}
    step_case_order = {s.id: (s.execution_case_id, s.step_order) for s in steps}
    for a in assertion_rows:
        case_id, step_order = step_case_order[a.execution_step_id]
        assertion_paths = a.sensitive_parameter_paths or []
        assertion_sensitive = sensitive_result(assertion_paths)
        snapshot_info = snapshot_assertion_info.get((case_id, step_order, a.assertion_order), {})
        if isinstance(snapshot_info.get("params"), dict):
            snapshot_info = {
                **snapshot_info,
                "params": mask_sensitive_parameters(snapshot_info["params"], assertion_paths),
            }
        assertion_by_step.setdefault(a.execution_step_id, []).append(
            {
                "id": a.id,
                "assertion_order": a.assertion_order,
                "assertion_type": a.assertion_type,
                "expected_value": REDACTED if assertion_sensitive else a.expected_value,
                "actual_value": REDACTED if assertion_sensitive else a.actual_value,
                "status": a.status,
                "error_message": REDACTED if assertion_paths else a.error_message,
                **snapshot_info,
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
                "nodes": nodes_by_case.get(c.id, []),
            }
        )
    return result


async def load_suite_tree(db: AsyncSession, execution_id: int) -> list[dict]:
    """返回 execution 的套件树（S1-7 §2：执行详情嵌套 suites 聚合）。

    每个 suite 含 setup_steps / teardown_steps（统一 ExecutionNode 投影）
    与 cases（steps/assertions）。旧 ExecutionStep 行仅用于读取旧数据。
    """
    suite_rows, cases, suite_step_rows, case_step_rows, assertion_rows = await execution_tree.load_suite_tree_rows(db, execution_id)
    if not suite_rows:
        return []
    node_rows = await execution_tree.load_suite_nodes(
        db,
        [suite.id for suite in suite_rows],
        [case.id for case in cases],
    )
    nodes_by_case: dict[int, list[dict]] = {}
    nodes_by_suite: dict[int, list[dict]] = {}
    for node in node_rows:
        sensitive_paths = node.sensitive_parameter_paths or []
        result_sensitive = bool(sensitive_paths)
        item = {
            "id": node.id, "kind": node.kind, "node_order": node.node_order, "phase": node.phase,
            "node_key": node.node_key, "action": node.action, "assertion_type": node.assertion_type,
            "description": node.description,
            "element_id": node.element_id, "parameters": mask_sensitive_parameters(node.parameters or {}, sensitive_paths),
            "max_wait_seconds": node.max_wait_seconds, "continue_on_failure": node.continue_on_failure,
            "status": node.status, "started_at": node.started_at, "finished_at": node.finished_at,
            "duration": node.duration, "attempt_count": node.attempt_count,
            "actual_value": REDACTED if result_sensitive else node.actual_value,
            "expected_value": REDACTED if result_sensitive else node.expected_value,
            "error_message": REDACTED if result_sensitive else node.error_message,
            "screenshot_path": node.screenshot_path,
            "artifact_id": f"node:{node.id}" if node.screenshot_path else None,
        }
        if node.execution_case_id is not None:
            nodes_by_case.setdefault(node.execution_case_id, []).append(item)
        elif node.execution_suite_id is not None:
            nodes_by_suite.setdefault(node.execution_suite_id, []).append(item)

    # 同 load_case_tree：键中的 case_id 来自可空列，运行时已过滤但类型系统无法感知
    snapshot_parameters: dict[tuple[int | None, int], dict] = {}
    snapshot_sensitive_paths: dict[tuple[int | None, int], list] = {}
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
            if isinstance(order, int) and isinstance(item.get("sensitive_parameter_paths"), list):
                snapshot_sensitive_paths[(c.id, order)] = item["sensitive_parameter_paths"]
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
    suite_snapshot_sensitive_paths: dict[tuple[int | None, str, int], list] = {}
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
                if isinstance(order, int) and isinstance(item.get("sensitive_parameter_paths"), list):
                    suite_snapshot_sensitive_paths[(s.id, phase, order)] = item["sensitive_parameter_paths"]

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
            step_paths = s.sensitive_parameter_paths or snapshot_sensitive_paths.get(
                (c.id, s.step_order), []
            )
            step_sensitive = bool(step_paths)
            steps_out.append(
                {
                    "id": s.id,
                    "step_order": s.step_order,
                    "action": s.action,
                    "phase": snapshot_phases.get((c.id, s.step_order), s.phase),
                    "parameters": mask_sensitive_parameters(
                        s.parameters or snapshot_parameters.get((c.id, s.step_order), {}), step_paths
                    ),
                    "status": s.status,
                    "started_at": s.started_at,
                    "finished_at": s.finished_at,
                    "duration": s.duration,
                    "actual_value": REDACTED if step_sensitive else s.actual_value,
                    "error_message": REDACTED if step_sensitive else s.error_message,
                    "screenshot_path": s.screenshot_path,
                    "assertions": [
                        {
                            "id": a.id,
                            "assertion_order": a.assertion_order,
                            "assertion_type": a.assertion_type,
                            "expected_value": REDACTED if sensitive_result(a.sensitive_parameter_paths or []) else a.expected_value,
                            "actual_value": REDACTED if sensitive_result(a.sensitive_parameter_paths or []) else a.actual_value,
                            "status": a.status,
                            "error_message": REDACTED if a.sensitive_parameter_paths else a.error_message,
                            **{
                                key: value
                                for key, value in snapshot_assertion_info.get((c.id, s.step_order, a.assertion_order), {}).items()
                                if key != "sensitive_parameter_paths" and key != "params"
                            },
                            "params": mask_sensitive_parameters(
                                snapshot_assertion_info.get((c.id, s.step_order, a.assertion_order), {}).get("params"),
                                a.sensitive_parameter_paths or [],
                            )
                            if isinstance(snapshot_assertion_info.get((c.id, s.step_order, a.assertion_order), {}).get("params"), dict)
                            else None,
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
            "nodes": nodes_by_case.get(c.id, []),
        }

    def _mk_suite_step(s: ExecutionStep) -> dict:
        phase = s.phase
        paths = s.sensitive_parameter_paths or suite_snapshot_sensitive_paths.get(
            (s.execution_suite_id, phase, s.step_order), []
        )
        result_sensitive = bool(paths)
        return {
            "id": s.id,
            "step_order": s.step_order,
            "action": s.action,
            "phase": phase,
            "parameters": mask_sensitive_parameters(
                s.parameters or suite_snapshot_parameters.get((s.execution_suite_id, phase, s.step_order), {}), paths
            ),
            "status": s.status,
            "started_at": s.started_at,
            "finished_at": s.finished_at,
            "duration": s.duration,
            "actual_value": REDACTED if result_sensitive else s.actual_value,
            "error_message": REDACTED if result_sensitive else s.error_message,
            "screenshot_path": s.screenshot_path,
            "artifact_id": f"step:{s.id}" if s.screenshot_path else None,
        }

    def _mk_suite_node_step(node: dict) -> dict:
        """将套件动作节点投影为旧详情字段，供报告/详情统一展示。"""
        return {
            "id": node["id"],
            "step_order": node["node_order"],
            "action": node.get("action") or "unknown",
            "phase": node.get("phase"),
            "parameters": node.get("parameters") or {},
            "status": node.get("status", "pending"),
            "started_at": node.get("started_at"),
            "finished_at": node.get("finished_at"),
            "duration": node.get("duration"),
            "actual_value": node.get("actual_value"),
            "error_message": node.get("error_message"),
            "screenshot_path": node.get("screenshot_path"),
            "artifact_id": node.get("artifact_id"),
        }

    result: list[dict] = []
    for suite in suite_rows:
        suite_steps = suite_steps_by_suite.get(suite.id, [])
        suite_nodes = nodes_by_suite.get(suite.id, [])
        setup_steps = [
            _mk_suite_step(s) for s in suite_steps if s.phase == "suite_setup"
        ]
        teardown_steps = [
            _mk_suite_step(s) for s in suite_steps if s.phase == "suite_teardown"
        ]
        if not suite_steps:
            setup_steps = [
                _mk_suite_node_step(node)
                for node in suite_nodes
                if node.get("phase") == "suite_setup"
            ]
            teardown_steps = [
                _mk_suite_node_step(node)
                for node in suite_nodes
                if node.get("phase") == "suite_teardown"
            ]
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
                "setup_steps": setup_steps,
                "teardown_steps": teardown_steps,
                "cases": [_mk_case(c) for c in cases_by_suite.get(suite.id, [])],
                "nodes": nodes_by_suite.get(suite.id, []),
            }
        )
    return result
