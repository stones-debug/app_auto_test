import base64
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import reports_dir, settings
from app.models import (
    Execution,
    ExecutionExclusion,
    ExecutionLog,
    Report,
)
from app.services.execution_detail_service import load_case_tree, load_suite_tree
from app.services.screenshot_store import resolve_screenshot_path, validate_object_key

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "reports"
# Step 8：HTML 缓存版本标记——修改模板/数据规则后旧缓存不再复用
_REPORT_HTML_VERSION = "suite-html-v6"


def _execution_dict(execution: Execution) -> dict:
    return {
        "id": execution.id,
        "project_id": execution.project_id,
        "type": execution.type,
        "suite_id": execution.suite_id,
        "case_id": execution.case_id,
        "device_id": execution.device_id,
        "status": execution.status,
        "parameters": execution.parameters or {},
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


async def get_report_detail(db: AsyncSession, execution_id: int) -> dict:
    """聚合执行结果：execution + cases(steps/assertions) + logs，供前端渲染与 HTML 生成。

    Step 8：case tree 复用 load_case_tree（常数级查询）；日志先 count，
    超限时只返回最后 report_max_logs 条并保持正序。
    """
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise LookupError(f"执行不存在: {execution_id}")

    cases: list[dict] = []
    for c in await load_case_tree(db, execution_id):
        cases.append(
            {
                "id": c["id"],
                "case_id": c["case_id"],
                "case_name": c["case_name"],
                "module_name": c["module_name"],
                "status": c["status"],
                "duration": c["duration"],
                "error_message": c["error_message"],
                "elements": c["elements"],
                "steps": [
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
                        "screenshot": _rel_screenshot(execution_id, s["screenshot_path"]),
                    }
                    for s in c["steps"]
                ],
                "assertions": [
                    {
                        "id": a["id"],
                        "assertion_order": a.get("assertion_order"),
                        "assertion_type": a["assertion_type"],
                        "expected_value": a["expected_value"],
                        "actual_value": a["actual_value"],
                        "status": a["status"],
                        "error_message": a["error_message"],
                        "params": a.get("params"),
                        "description": a.get("description"),
                    }
                    for a in c["assertions"]
                ],
            }
        )

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
                "setup_steps": [
                    {
                        "id": st["id"],
                        "step_order": st["step_order"],
                        "action": st["action"],
                        "phase": st["phase"],
                        "parameters": st["parameters"],
                        "status": st["status"],
                        "duration": st["duration"],
                        "actual_value": st["actual_value"],
                        "error_message": st["error_message"],
                        "screenshot": _rel_screenshot(execution_id, st["screenshot_path"]),
                    }
                    for st in s["setup_steps"]
                ],
                "cases": [
                    {
                        "id": c["id"],
                        "case_id": c["case_id"],
                        "case_name": c["case_name"],
                        "module_name": c["module_name"],
                        "status": c["status"],
                        "duration": c["duration"],
                        "error_message": c["error_message"],
                        "elements": c["elements"],
                        "steps": [
                            {
                                "id": st["id"],
                                "step_order": st["step_order"],
                                "action": st["action"],
                                "phase": st["phase"],
                                "parameters": st["parameters"],
                                "status": st["status"],
                                "duration": st["duration"],
                                "actual_value": st["actual_value"],
                                "error_message": st["error_message"],
                                "screenshot": _rel_screenshot(execution_id, st["screenshot_path"]),
                            }
                            for st in c["steps"]
                        ],
                        "assertions": [
                            {
                                "id": a["id"],
                                "assertion_order": a.get("assertion_order"),
                                "assertion_type": a["assertion_type"],
                                "expected_value": a["expected_value"],
                                "actual_value": a["actual_value"],
                                "status": a["status"],
                                "error_message": a["error_message"],
                                "params": a.get("params"),
                                "description": a.get("description"),
                            }
                            for a in c["assertions"]
                        ],
                    }
                    for c in s["cases"]
                ],
                "teardown_steps": [
                    {
                        "id": st["id"],
                        "step_order": st["step_order"],
                        "action": st["action"],
                        "phase": st["phase"],
                        "parameters": st["parameters"],
                        "status": st["status"],
                        "duration": st["duration"],
                        "actual_value": st["actual_value"],
                        "error_message": st["error_message"],
                        "screenshot": _rel_screenshot(execution_id, st["screenshot_path"]),
                    }
                    for st in s["teardown_steps"]
                ],
            }
        )

    # Step 8：报告日志上限——先 count，超限取最后 N 条保持正序
    logs_total = (
        await db.scalar(
            select(func.count()).select_from(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
        )
    ) or 0
    logs_truncated = logs_total > settings.report_max_logs
    log_query = (
        select(ExecutionLog)
        .where(ExecutionLog.execution_id == execution_id)
        .order_by(ExecutionLog.id.desc() if logs_truncated else ExecutionLog.id)
    )
    if logs_truncated:
        log_query = log_query.limit(settings.report_max_logs)
        log_rows = (await db.execute(log_query)).scalars().all()[::-1]
    else:
        log_rows = (await db.execute(log_query)).scalars().all()

    report = (
        await db.execute(select(Report).where(Report.execution_id == execution_id))
    ).scalar_one_or_none()
    exclusion_rows = (
        await db.execute(
            select(ExecutionExclusion)
            .where(ExecutionExclusion.execution_id == execution_id)
            .order_by(ExecutionExclusion.id)
        )
    ).scalars().all()

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
        },
        "suites": suites,
        "cases": cases,
        "exclusions": [
            {
                "target_type": row.target_type,
                "suite_id": row.suite_id_snapshot,
                "case_id": row.case_id_snapshot,
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

    def _embed(steps: list[dict]) -> None:
        for step in steps:
            if not step["screenshot"]:
                continue
            # screenshot 字段为 screenshots/xxx.png（已剥掉 execution_{id}/ 前缀）
            object_key = f"execution_{execution_id}/{step['screenshot']}"
            file_path = resolve_screenshot_path(execution_id, object_key)
            if file_path is None or not file_path.is_file():
                step["screenshot_base64"] = None
                continue
            try:
                data = file_path.read_bytes()
                step["screenshot_base64"] = base64.b64encode(data).decode()
            except OSError:
                step["screenshot_base64"] = None

    for case in detail["cases"]:
        _embed(case["steps"])
    for suite in detail.get("suites", []):
        _embed(suite["setup_steps"])
        _embed(suite["teardown_steps"])
        for case in suite["cases"]:
            _embed(case["steps"])


async def render_report_html(db: AsyncSession, execution_id: int) -> Path:
    """按需生成自包含 HTML 报告；已生成且版本一致则直接复用（幂等）。

    Step 8：缓存带模板/数据版本标记（_REPORT_HTML_VERSION），版本变化时强制重生成。
    """
    target_dir = reports_dir() / f"execution_{execution_id}"
    html_path = target_dir / "report.html"
    if html_path.exists():
        # CR-26：命中缓存时也幂等同步 DB report_path，避免崩溃后列表长期显示“按需”
        if _cache_version_ok(html_path):
            report = (
                await db.execute(select(Report).where(Report.execution_id == execution_id))
            ).scalar_one_or_none()
            if report is not None and report.report_path != str(html_path):
                report.report_path = str(html_path)
                await db.commit()
            return html_path
        # 版本过期：重生成，覆盖旧缓存
        html_path.unlink(missing_ok=True)

    detail = await get_report_detail(db, execution_id)
    _embed_screenshots(detail)
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
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )
    html = f"<!-- version: {_REPORT_HTML_VERSION} -->\n" + html
    target_dir.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    report = (
        await db.execute(select(Report).where(Report.execution_id == execution_id))
    ).scalar_one_or_none()
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
