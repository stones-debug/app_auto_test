import base64
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import reports_dir
from app.models import (
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionLog,
    ExecutionStep,
    Report,
)
from app.services.screenshot_store import resolve_screenshot_path, validate_object_key

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "reports"


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
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
    }


async def get_report_detail(db: AsyncSession, execution_id: int) -> dict:
    """聚合执行结果：execution + cases(steps/assertions) + logs，供前端渲染与 HTML 生成。"""
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise LookupError(f"执行不存在: {execution_id}")

    cases: list[dict] = []
    case_rows = (
        await db.execute(
            select(ExecutionCase)
            .where(ExecutionCase.execution_id == execution_id)
            .order_by(ExecutionCase.id)
        )
    ).scalars().all()
    for ec in case_rows:
        steps = (
            await db.execute(
                select(ExecutionStep)
                .where(ExecutionStep.execution_case_id == ec.id)
                .order_by(ExecutionStep.step_order)
            )
        ).scalars().all()
        assertions = (
            await db.execute(
                select(ExecutionAssertion)
                .join(ExecutionStep, ExecutionAssertion.execution_step_id == ExecutionStep.id)
                .where(ExecutionStep.execution_case_id == ec.id)
                .order_by(ExecutionAssertion.id)
            )
        ).scalars().all()
        cases.append(
            {
                "id": ec.id,
                "case_id": ec.case_id,
                "case_name": ec.case_name,
                "module_name": ec.module_name,
                "status": ec.status,
                "duration": ec.duration,
                "error_message": ec.error_message,
                "elements": ec.elements_snapshot or {},
                "steps": [
                    {
                        "id": s.id,
                        "step_order": s.step_order,
                        "action": s.action,
                        "parameters": s.parameters or {},
                        "status": s.status,
                        "duration": s.duration,
                        "actual_value": s.actual_value,
                        "error_message": s.error_message,
                        "screenshot": _rel_screenshot(execution_id, s.screenshot_path),
                    }
                    for s in steps
                ],
                "assertions": [
                    {
                        "id": a.id,
                        "assertion_type": a.assertion_type,
                        "expected_value": a.expected_value,
                        "actual_value": a.actual_value,
                        "status": a.status,
                        "error_message": a.error_message,
                    }
                    for a in assertions
                ],
            }
        )

    logs = (
        await db.execute(
            select(ExecutionLog).where(ExecutionLog.execution_id == execution_id).order_by(ExecutionLog.id)
        )
    ).scalars().all()

    report = (
        await db.execute(select(Report).where(Report.execution_id == execution_id))
    ).scalar_one_or_none()

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
        },
        "cases": cases,
        "logs": [
            {
                "id": log.id,
                "level": log.level,
                "message": log.message,
                "source": log.source,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }


def _rel_screenshot(execution_id: int, path: str | None) -> str | None:
    if not path or not validate_object_key(execution_id, path):
        return None
    # DB 中为 execution_{id}/screenshots/xxx.png，剥掉 execution_{id}/ 前缀
    return path.split("/", 1)[1]


def _embed_screenshots(detail: dict) -> None:
    execution_id = detail["execution"]["id"]
    for case in detail["cases"]:
        for step in case["steps"]:
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


async def render_report_html(db: AsyncSession, execution_id: int) -> Path:
    """按需生成自包含 HTML 报告；已生成则直接复用（幂等）。"""
    target_dir = reports_dir() / f"execution_{execution_id}"
    html_path = target_dir / "report.html"
    if html_path.exists():
        # CR-26：命中缓存时也幂等同步 DB report_path，避免崩溃后列表长期显示“按需”
        report = (
            await db.execute(select(Report).where(Report.execution_id == execution_id))
        ).scalar_one_or_none()
        if report is not None and report.report_path != str(html_path):
            report.report_path = str(html_path)
            await db.commit()
        return html_path

    detail = await get_report_detail(db, execution_id)
    _embed_screenshots(detail)
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report.html")
    html = template.render(
        execution=detail["execution"],
        report=detail["report"],
        cases=detail["cases"],
        logs=detail["logs"],
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    report = (
        await db.execute(select(Report).where(Report.execution_id == execution_id))
    ).scalar_one_or_none()
    if report is not None:
        report.report_path = str(html_path)
        await db.commit()
    return html_path
