import os
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import reports_dir
from app.core.database import SessionLocal
from app.main import app
from app.models import ExecutionLog, Report
from app.services import cleanup_service

REG = {"username": "pytest_cleanup_user", "email": "cu@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _make_project(client: AsyncClient) -> int:
    await client.post("/api/auth/register", json=REG)
    login = await client.post("/api/auth/login", json={"username": REG["username"], "password": REG["password"]})
    token = login.json()["access_token"]
    project = await client.post(
        "/api/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "清理测试项目", "visibility": "private"},
    )
    return project.json()["id"]


async def _seed_report_with_dir(project_id: int) -> tuple[int, int]:
    """创建真实 execution + 以其 id 命名的报告目录（mtime 置旧）+ 一行 Report(report_path)。"""
    from app.models import Execution

    async with SessionLocal() as db:
        execution = Execution(project_id=project_id, type="case", status="passed")
        db.add(execution)
        await db.flush()
        execution_id = execution.id
        report = Report(execution_id=execution_id, total=1, passed=1, success_rate=100)
        report.report_path = str(reports_dir() / f"execution_{execution_id}" / "report.html")
        db.add(report)
        await db.commit()
        report_id = report.id

    target = reports_dir() / f"execution_{execution_id}" / "screenshots"
    target.mkdir(parents=True, exist_ok=True)
    (target / "old.png").write_bytes(b"x")
    old = datetime.now(UTC) - timedelta(days=200)
    for p in [reports_dir() / f"execution_{execution_id}", target, target / "old.png"]:
        os.utime(p, (old.timestamp(), old.timestamp()))
    return execution_id, report_id


async def test_cleanup_old_reports(client: AsyncClient):
    project_id = await _make_project(client)
    execution_id, report_id = await _seed_report_with_dir(project_id)

    async with SessionLocal() as db:
        result = await cleanup_service.cleanup_old_reports(db)
    assert result["report_dirs_removed"] >= 1
    assert result["report_paths_cleared"] >= 1

    assert not (reports_dir() / f"execution_{execution_id}").exists()

    async with SessionLocal() as db:
        report = await db.get(Report, report_id)
        assert report.report_path is None


async def test_cleanup_old_reports_dry_run(client: AsyncClient):
    project_id = await _make_project(client)
    execution_id, _report_id = await _seed_report_with_dir(project_id)

    async with SessionLocal() as db:
        result = await cleanup_service.cleanup_old_reports(db, dry_run=True)
    assert result["report_dirs_removed"] == 1
    # dry-run 不删除
    assert (reports_dir() / f"execution_{execution_id}").exists()


async def test_cleanup_old_logs(client: AsyncClient):
    await client.post("/api/auth/register", json=REG)
    login = await client.post("/api/auth/login", json={"username": REG["username"], "password": REG["password"]})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post("/api/projects", headers=headers, json={"name": "清理日志项目", "visibility": "private"})
    project_id = project.json()["id"]

    from app.models import Execution

    async with SessionLocal() as db:
        execution = Execution(project_id=project_id, type="case", status="queued")
        db.add(execution)
        await db.flush()
        execution_id = execution.id
        old = datetime.now(UTC) - timedelta(days=40)
        db.add(ExecutionLog(execution_id=execution_id, level="INFO", message="old", source="worker", created_at=old))
        db.add(ExecutionLog(execution_id=execution_id, level="INFO", message="new", source="worker"))
        await db.commit()

    async with SessionLocal() as db:
        result = await cleanup_service.cleanup_old_logs(db)
    assert result["logs_deleted"] == 1
