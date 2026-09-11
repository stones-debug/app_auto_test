import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import reports_dir, settings
from app.models import (
    Execution,
)
from app.repositories import executions as executions_repo
from app.repositories import reports as reports_repo
from app.services.execution_detail_service import load_case_tree, load_suite_tree
from app.services.screenshot_store import resolve_screenshot_path, validate_object_key
from app.services.sensitive_snapshot import mask_execution_parameters

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "reports"
# Step 8：HTML 缓存版本标记——修改模板/数据规则后旧缓存不再复用
_REPORT_HTML_VERSION = "true-lazy-report-html-v12-sensitive-snapshots"


def _json_safe(value: object) -> Any:
    """Convert database numeric values (for example Decimal durations) for JSON payloads."""
    return json.loads(json.dumps(value, default=str))


def _case_payload(case: dict) -> dict:
    """Build only the detail branch rendered by the offline HTML report.

    New execution rows can expose both the legacy ``steps`` branch and the
    node-based branch.  The template renders nodes when present, so keeping
    both branches would duplicate every screenshot in the downloaded file.
    """
    payload = {"error_message": case.get("error_message")}
    if case.get("nodes"):
        payload["nodes"] = case["nodes"]
    else:
        payload["steps"] = case.get("steps") or []
    return payload


def _report_html_payloads(detail: dict) -> tuple[dict, dict[str, dict]]:
    """Return a lightweight report index and independently parseable details."""
    payloads: dict[str, dict] = {}
    index: dict = {
        "suites": [],
        "cases": [],
        "logs_key": "logs",
        "logs_total": detail["logs_total"],
        "logs_truncated": detail["logs_truncated"],
    }

    def case_summary(case: dict, key: str) -> dict:
        return {
            "case_name": case["case_name"],
            "module_name": case["module_name"],
            "status": case["status"],
            "payload_key": key,
        }

    for suite_index, suite in enumerate(detail["suites"]):
        suite_index_data = {
            "error_message": suite["error_message"],
            "cases": [],
            "setup_key": None,
            "setup_count": len(suite["setup_steps"]),
            "teardown_key": None,
            "teardown_count": len(suite["teardown_steps"]),
        }
        if suite["setup_steps"]:
            key = f"s{suite_index}-setup"
            suite_index_data["setup_key"] = key
            payloads[key] = {"steps": suite["setup_steps"]}
        if suite["teardown_steps"]:
            key = f"s{suite_index}-teardown"
            suite_index_data["teardown_key"] = key
            payloads[key] = {"steps": suite["teardown_steps"]}
        for case_index, case in enumerate(suite["cases"]):
            key = f"s{suite_index}-c{case_index}"
            suite_index_data["cases"].append(case_summary(case, key))
            payloads[key] = _case_payload(case)
        index["suites"].append(suite_index_data)

    if not detail["suites"]:
        for case_index, case in enumerate(detail["cases"]):
            key = f"c{case_index}"
            index["cases"].append(case_summary(case, key))
            payloads[key] = _case_payload(case)

    payloads["logs"] = {"logs": detail["logs"]}
    return _json_safe(index), _json_safe(payloads)


def _execution_dict(execution: Execution) -> dict:
    return {
        "id": execution.id,
        "project_id": execution.project_id,
        "type": execution.type,
        "suite_id": execution.suite_id,
        "case_id": execution.case_id,
        "device_id": execution.device_id,
        "status": execution.status,
        "parameters": mask_execution_parameters(
            execution.parameters or {}, execution.sensitive_variable_names or []
        ),
        "timeout_seconds": execution.timeout_seconds,
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
        "duration": execution.duration,
        "retry_of": execution.retry_of,
        "app_profile_id": execution.app_profile_id,
        "app_profile_name": execution.app_profile_name_snapshot,
        "app_release_id": execution.app_release_id,
        "app_release_version": execution.app_release_version_snapshot,
        "profile_revision": execution.profile_revision,
        "test_asset_revision": execution.test_asset_revision,
        "profile_resolution_summary": execution.profile_resolution_summary or {},
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
    }


def _report_step(execution_id: int, step: dict) -> dict:
    return {
        "id": step["id"],
        "step_order": step["step_order"],
        "action": step["action"],
        "phase": step["phase"],
        "parameters": step["parameters"],
        "status": step["status"],
        "duration": step["duration"],
        "actual_value": step["actual_value"],
        "error_message": step["error_message"],
        "screenshot": _rel_screenshot(execution_id, step["screenshot_path"]),
        "assertions": [
            {
                "id": assertion["id"],
                "assertion_order": assertion.get("assertion_order"),
                "assertion_type": assertion["assertion_type"],
                "expected_value": assertion["expected_value"],
                "actual_value": assertion["actual_value"],
                "status": assertion["status"],
                "error_message": assertion["error_message"],
                "params": assertion.get("params"),
                "description": assertion.get("description"),
            }
            for assertion in step.get("assertions") or []
        ],
    }


def _report_node(execution_id: int, node: dict) -> dict:
    return {
        "id": node["id"],
        "kind": node["kind"],
        "node_order": node["node_order"],
        "phase": node.get("phase"),
        "node_key": node.get("node_key"),
        "action": node.get("action"),
        "assertion_type": node.get("assertion_type"),
        "description": node.get("description"),
        "element_id": node.get("element_id"),
        "parameters": node.get("parameters") or {},
        "max_wait_seconds": node.get("max_wait_seconds"),
        "status": node.get("status", "pending"),
        "duration": node.get("duration"),
        "attempt_count": node.get("attempt_count", 0),
        "actual_value": node.get("actual_value"),
        "expected_value": node.get("expected_value"),
        "error_message": node.get("error_message"),
        "screenshot": _rel_screenshot(execution_id, node.get("screenshot_path")),
        "artifact_id": node.get("artifact_id"),
    }


def _report_case(execution_id: int, case: dict) -> dict:
    return {
        "id": case["id"],
        "case_id": case["case_id"],
        "case_name": case["case_name"],
        "module_name": case["module_name"],
        "status": case["status"],
        "duration": case["duration"],
        "error_message": case["error_message"],
        "elements": case["elements"],
        "steps": [_report_step(execution_id, step) for step in case["steps"]],
        "nodes": [_report_node(execution_id, node) for node in case.get("nodes") or []],
    }


async def get_report_detail(db: AsyncSession, execution_id: int) -> dict:
    """聚合执行结果：execution + cases(steps/assertions) + logs，供前端渲染与 HTML 生成。

    优先只加载一次 suite tree，并从其中复用用例数据。只有不存在套件树时才
    回退 load_case_tree，避免套件执行重复查询、组装和序列化全部用例。
    日志先 count，超限时只返回最后 report_max_logs 条并保持正序。
    """
    execution = await executions_repo.get_by_id(db, execution_id)
    if execution is None:
        raise LookupError(f"执行不存在: {execution_id}")

    # 方案 §2：套件树（套件→用例→步骤/前后置），供报告分层展示
    suites: list[dict] = []
    for s in await load_suite_tree(db, execution_id):
        suites.append(
            {
                "id": s["id"],
                "suite_id": s["suite_id"],
                "suite_name": s["suite_name"],
                "suite_order": s["suite_order"],
                "status": s["status"],
                "duration": s["duration"],
                "error_message": s["error_message"],
                "setup_steps": [_report_step(execution_id, step) for step in s["setup_steps"]],
                "cases": [_report_case(execution_id, case) for case in s["cases"]],
                "teardown_steps": [
                    _report_step(execution_id, step) for step in s["teardown_steps"]
                ],
                "nodes": [_report_node(execution_id, node) for node in s.get("nodes") or []],
            }
        )

    if suites:
        # 内部 HTML 生成仍保留扁平 cases 入口，但直接复用 suite tree 中的对象。
        cases = [case for suite in suites for case in suite["cases"]]
    else:
        cases = [
            _report_case(execution_id, case)
            for case in await load_case_tree(db, execution_id)
        ]

    # Step 8：报告日志上限——先 count，超限取最后 N 条保持正序
    logs_total, report, exclusion_rows = await reports_repo.load_detail_rows(db, execution_id)
    logs_truncated = logs_total > settings.report_max_logs
    log_rows = await reports_repo.list_logs(db, execution_id, limit=settings.report_max_logs if logs_truncated else None, reverse=logs_truncated)

    return {
        "execution": _execution_dict(execution),
        "report": {
            "id": report.id if report else None,
            "total": report.total if report else len(cases),
            "passed": report.passed if report else 0,
            "failed": report.failed if report else 0,
            "error_count": report.error_count if report else 0,
            "skipped": report.skipped if report else 0,
            "success_rate": report.success_rate if report else 0,
            "not_applicable": report.not_applicable if report else 0,
            "exclusion_summary": report.exclusion_summary if report else {},
            # 方案 §2：套件/步骤三层统计 + N/A 套件
            "suite_total": report.suite_total if report else 0,
            "suite_passed": report.suite_passed if report else 0,
            "suite_failed": report.suite_failed if report else 0,
            "suite_error_count": report.suite_error_count if report else 0,
            "suite_skipped": report.suite_skipped if report else 0,
            "suite_success_rate": report.suite_success_rate if report else 0,
            "step_total": report.step_total if report else 0,
            "step_passed": report.step_passed if report else 0,
            "step_failed": report.step_failed if report else 0,
            "step_error_count": report.step_error_count if report else 0,
            "step_skipped": report.step_skipped if report else 0,
            "step_success_rate": report.step_success_rate if report else 0,
            "not_applicable_suites": report.not_applicable_suites if report else 0,
            "assertion_total": report.assertion_total if report else 0,
            "assertion_passed": report.assertion_passed if report else 0,
            "assertion_failed": report.assertion_failed if report else 0,
            "assertion_error_count": report.assertion_error_count if report else 0,
            "assertion_skipped": report.assertion_skipped if report else 0,
            "assertion_success_rate": report.assertion_success_rate if report else 0,
        },
        "suites": suites,
        "cases": cases,
        "exclusions": [
            {
                "target_type": row.target_type,
                "suite_id": row.suite_id_snapshot,
                "suite_case_id": row.suite_case_id_snapshot,
                "case_id": row.case_id_snapshot,
                "occurrence_order": row.occurrence_order,
                "phase": (row.details or {}).get("phase"),
                "path": "/".join(
                    part
                    for part in (
                        row.suite_name_snapshot,
                        row.case_name_snapshot,
                        row.node_name_snapshot,
                    )
                    if part
                ),
                "reason_code": row.reason_code,
                "reason_note": row.reason_note,
                "source_type": row.source_type,
                "node_key": str(row.node_key) if row.node_key else None,
            }
            for row in exclusion_rows
        ],
        "logs": [
            {
                "id": log.id,
                "level": log.level,
                "message": log.message,
                "source": log.source,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in log_rows
        ],
        "logs_total": logs_total,
        "logs_truncated": logs_truncated,
    }


def _rel_screenshot(execution_id: int, path: str | None) -> str | None:
    if not path or not validate_object_key(execution_id, path):
        return None
    # DB 中为 execution_{id}/screenshots/xxx.png，剥掉 execution_{id}/ 前缀
    return path.split("/", 1)[1]


def _embed_screenshots(detail: dict) -> None:
    execution_id = detail["execution"]["id"]

    def _embed(items: list[dict]) -> None:
        for item in items:
            if not item.get("screenshot"):
                continue
            # screenshot 字段为 screenshots/xxx.png（已剥掉 execution_{id}/ 前缀）
            object_key = f"execution_{execution_id}/{item['screenshot']}"
            file_path = resolve_screenshot_path(execution_id, object_key)
            if file_path is None or not file_path.is_file():
                item["screenshot_base64"] = None
                continue
            try:
                data = file_path.read_bytes()
                item["screenshot_base64"] = base64.b64encode(data).decode()
            except OSError:
                item["screenshot_base64"] = None

    suites = detail.get("suites", [])
    for suite in suites:
        _embed(suite["setup_steps"])
        _embed(suite["teardown_steps"])
        for case in suite["cases"]:
            _embed(case["steps"])
            _embed(case.get("nodes") or [])
    if not suites:
        for case in detail["cases"]:
            _embed(case["steps"])
            _embed(case.get("nodes") or [])


async def render_report_html(db: AsyncSession, execution_id: int) -> Path:
    """按需生成自包含 HTML 报告；已生成且版本一致则直接复用（幂等）。

    Step 8：缓存带模板/数据版本标记（_REPORT_HTML_VERSION），版本变化时强制重生成。
    """
    target_dir = reports_dir() / f"execution_{execution_id}"
    html_path = target_dir / "report.html"
    if html_path.exists():
        # CR-26：命中缓存时也幂等同步 DB report_path，避免崩溃后列表长期显示“按需”
        if _cache_version_ok(html_path):
            report = await reports_repo.get_by_execution(db, execution_id)
            if report is not None and report.report_path != str(html_path):
                report.report_path = str(html_path)
                await db.commit()
            return html_path
        # 版本过期：重生成，覆盖旧缓存
        html_path.unlink(missing_ok=True)

    detail = await get_report_detail(db, execution_id)
    _embed_screenshots(detail)
    report_index, report_payloads = _report_html_payloads(detail)
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report.html")
    html = template.render(
        execution=detail["execution"],
        report=detail["report"],
        suites=detail["suites"],
        cases=detail["cases"],
        logs=detail["logs"],
        exclusions=detail["exclusions"],
        logs_total=detail["logs_total"],
        logs_truncated=detail["logs_truncated"],
        report_index=report_index,
        report_payloads=report_payloads,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )
    html = f"<!-- version: {_REPORT_HTML_VERSION} -->\n" + html
    target_dir.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    report = await reports_repo.get_by_execution(db, execution_id)
    if report is not None:
        report.report_path = str(html_path)
        await db.commit()
    return html_path


def _cache_version_ok(html_path: Path) -> bool:
    try:
        with html_path.open("r", encoding="utf-8") as fh:
            head = fh.read(200)
    except OSError:
        return False
    return f"version: {_REPORT_HTML_VERSION}" in head
