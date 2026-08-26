"""Step 8：常数级查询与报告日志上限。"""

import secrets

import pytest
from sqlalchemy import event, insert

from app.core.database import SessionLocal, engine
from app.models import (
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionLog,
    ExecutionStep,
    ExecutionSuite,
    Project,
    User,
)
from app.services import report_service
from app.services.execution_service import get_execution_logs

pytestmark = pytest.mark.asyncio


async def _project_and_execution() -> int:
    async with SessionLocal() as db:
        user = User(
            username=f"pytest_perf_{secrets.token_hex(4)}",
            email=f"{secrets.token_hex(4)}@perf.t",
            password_hash="x",
            status="active",
        )
        db.add(user)
        await db.flush()
        project = Project(name="perf项目", owner_id=user.id, visibility="private")
        db.add(project)
        await db.commit()
        await db.refresh(project)
        execution = Execution(project_id=project.id, type="case")
        db.add(execution)
        await db.commit()
        return execution.id


async def _seed_tree(execution_id: int, case_count: int = 100) -> None:
    """按 case_count 建 suite/case/step/assertion（保持 tree 结构，query 计数不受 N 影响）。"""
    async with SessionLocal() as db:
        suite = ExecutionSuite(
            execution_id=execution_id,
            suite_id=None,
            suite_name="perf虚拟套件",
            suite_order=1,
            is_virtual=True,
            status="passed",
            setup_steps_snapshot=[],
            teardown_steps_snapshot=[],
            elements_snapshot={},
        )
        db.add(suite)
        await db.flush()
        cases = [
            ExecutionCase(
                execution_id=execution_id,
                execution_suite_id=suite.id,
                case_id=i,
                case_name=f"C{i}",
                case_order=i + 1,
                status="passed",
                steps_snapshot=[],
                assertions_snapshot=[],
            )
            for i in range(case_count)
        ]
        db.add_all(cases)
        await db.flush()
        steps = []
        assertions = []
        for ec in cases:
            for order in (1, 2):
                steps.append(
                    ExecutionStep(execution_case_id=ec.id, step_order=order, action="click", status="passed")
                )
        db.add_all(steps)
        await db.flush()
        for ec in cases:
            assertions.append(
                ExecutionAssertion(execution_case_id=ec.id, assertion_order=1, assertion_type="text_equals", status="pass")
            )
        db.add_all(assertions)
        await db.commit()


async def test_case_tree_query_count_is_constant():
    """100 cases 时 case tree 查询数保持 3，不随 N 增长。"""
    from app.services.execution_detail_service import load_case_tree

    calls: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        calls.append(statement)

    execution_id = await _project_and_execution()
    await _seed_tree(execution_id, 100)

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    try:
        async with SessionLocal() as db:
            tree = await load_case_tree(db, execution_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before)

    assert len(tree) == 100
    assert len(tree[0]["steps"]) == 2
    tree_statements = [
        s for s in calls if any(t in s for t in ("execution_cases", "execution_steps", "execution_assertions"))
    ]
    assert len(tree_statements) == 3, f"case tree 查询数应为 3，实际 {len(tree_statements)}"


async def test_get_execution_logs_total_uses_db_count():
    """10 万日志分页时 total 使用数据库 count，不创建 10 万元素 list。"""
    execution_id = await _project_and_execution()
    rows_count = 100_000
    async with SessionLocal() as db:
        await db.execute(
            insert(ExecutionLog),
            [
                {
                    "execution_id": execution_id,
                    "level": "INFO",
                    "message": f"log-{i}",
                    "source": "worker",
                }
                for i in range(rows_count)
            ],
        )
        await db.commit()

    async with SessionLocal() as db:
        rows, total = await get_execution_logs(db, execution_id, None, 0, 200)
        assert total == rows_count  # total 来自 count 查询
        assert len(rows) == 200  # 只物化当页行，不物化全部 ID

        # 同毫秒 created_at（bulk insert 同一事务）时按 (created_at, id) 稳定分页：
        # 第二页首条 id 严格衔接第一页末条
        second, total2 = await get_execution_logs(db, execution_id, None, 200, 200)
        assert total2 == rows_count
        assert second[0].id == rows[-1].id + 1


async def test_report_logs_truncated_returns_last_n():
    """报告超限时只返回最后 report_max_logs 条，顺序正确，truncated=true。"""
    from app.core.config import settings

    execution_id = await _project_and_execution()
    total_logs = settings.report_max_logs + 7  # 20007 条
    async with SessionLocal() as db:
        await db.execute(
            insert(ExecutionLog),
            [
                {
                    "execution_id": execution_id,
                    "level": "INFO",
                    "message": f"msg-{i}",
                    "source": "worker",
                }
                for i in range(total_logs)
            ],
        )
        await db.commit()

    async with SessionLocal() as db:
        detail = await report_service.get_report_detail(db, execution_id)
        assert detail["logs_total"] == total_logs
        assert detail["logs_truncated"] is True
        assert len(detail["logs"]) == settings.report_max_logs
        # 只保留最后 N 条，且保持正序（首条为 total-N 的日志）
        assert detail["logs"][0]["message"] == f"msg-{total_logs - settings.report_max_logs}"
        assert detail["logs"][-1]["message"] == f"msg-{total_logs - 1}"


async def test_small_report_keeps_compatible_shape():
    """小报告响应字段与现有格式兼容（logs_total/logs_truncated 追加，不破坏原字段）。"""
    execution_id = await _project_and_execution()
    await _seed_tree(execution_id, 3)
    async with SessionLocal() as db:
        db.add(ExecutionLog(execution_id=execution_id, level="INFO", message="hi", source="worker"))
        await db.commit()

    async with SessionLocal() as db:
        detail = await report_service.get_report_detail(db, execution_id)
    assert detail["logs_total"] == 1
    assert detail["logs_truncated"] is False
    assert set(detail) >= {"execution", "report", "cases", "logs", "logs_total", "logs_truncated"}
    assert len(detail["cases"]) == 3
    case0 = detail["cases"][0]
    assert set(case0) >= {"id", "case_id", "case_name", "status", "steps", "assertions"}
    step0 = case0["steps"][0]
    assert set(step0) >= {"id", "step_order", "action", "status", "screenshot"}
    assert "screenshot_base64" not in step0  # 仅 HTML 内嵌时填充
