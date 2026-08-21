import shutil
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select as sa_select

from app.core.config import reports_dir
from app.core.database import SessionLocal
from app.core.security import hash_psk
from app.main import app
from app.models import Agent, Device, Execution, Report
from app.services import report_service, worker_service
from app.services.screenshot_store import resolve_screenshot_path, validate_object_key
from app.ws import handlers

REG = {"username": "pytest_report_user", "email": "rp@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _make_agent() -> int:
    async with SessionLocal() as db:
        agent = Agent(agent_key=hash_psk("sk-rp"), agent_id=f"pytest_rp_agent_{uuid.uuid4().hex[:6]}", status="offline")
        db.add(agent)
        await db.commit()
        return agent.id


async def _setup(client: AsyncClient) -> tuple[str, int]:
    """返回 (token, execution_id)，并造好 steps/assertions/报告数据。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post("/api/auth/login", json={"username": REG["username"], "password": REG["password"]})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post("/api/projects", headers=headers, json={"name": "报告测试项目", "visibility": "private"})
    project_id = project.json()["id"]
    element = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "用户名", "locator_type": "id", "locator_value": "username"},
    )
    element_id = element.json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        headers=headers,
        json={
            "name": "报告用例",
            "steps": [
                {"order": 1, "action": "input", "element_id": element_id, "params": {"value": "admin"}},
                {"order": 2, "action": "click", "element_id": element_id, "params": {}},
            ],
            "assertions": [
                {"order": 1, "type": "text_equals", "element_id": element_id, "params": {"expected": "admin"}}
            ],
        },
    )
    case_id = case.json()["id"]
    execution = await client.post(
        f"/api/executions/cases/{case_id}", headers=headers, json={"parameters": {}}
    )
    execution_id = execution.json()["id"]
    agent_id = await _make_agent()

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        device = Device(agent_id=agent_id, name="rp-device", platform="android", udid=f"u-{uuid.uuid4().hex[:8]}", status="busy")
        db.add(device)
        await db.flush()
        execution.device_id = device.id
        execution.status = "running"
        execution.session_token = "rp-token"
        execution.started_at = datetime.now(UTC)
        await db.commit()
        await handlers.handle_step_result(
            db,
            agent_id,
            {"execution_id": execution_id, "session_token": "rp-token", "case_id": case_id, "step_order": 1, "action": "input", "status": "passed", "actual_value": "admin"},
        )
        await handlers.handle_step_result(
            db,
            agent_id,
            {"execution_id": execution_id, "session_token": "rp-token", "case_id": case_id, "step_order": 2, "action": "click", "status": "passed"},
        )
        await handlers.handle_assertion_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "rp-token",
                "case_id": case_id,
                "assertions": [{"type": "text_equals", "expected": "admin", "actual": "admin", "status": "pass"}],
            },
        )
        execution = await db.get(Execution, execution_id)
        await worker_service._mark_terminal(db, execution, "passed")
    return token, execution_id


async def _report_id(execution_id: int) -> int:
    async with SessionLocal() as db:
        report = (await db.execute(sa_select(Report).where(Report.execution_id == execution_id))).scalar_one()
        return report.id


def _cleanup(execution_id: int) -> None:
    target = reports_dir() / f"execution_{execution_id}"
    if target.exists():
        shutil.rmtree(target)


async def test_report_list_and_detail(client: AsyncClient):
    token, execution_id = await _setup(client)
    headers = {"Authorization": f"Bearer {token}"}
    report_id = await _report_id(execution_id)

    listed = await client.get("/api/reports", headers=headers)
    assert listed.status_code == 200
    item = next(i for i in listed.json()["items"] if i["id"] == report_id)
    assert item["execution_status"] == "passed"
    assert item["case_name"] == "报告用例"
    assert item["passed"] == 1
    assert item["total"] == 1

    detail = await client.get(f"/api/reports/{report_id}/detail", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["execution"]["status"] == "passed"
    assert body["report"]["passed"] == 1
    assert body["cases"][0]["case_name"] == "报告用例"
    assert [s["action"] for s in body["cases"][0]["steps"]] == ["input", "click"]
    assert body["cases"][0]["steps"][0]["actual_value"] == "admin"
    assert body["cases"][0]["assertions"][0]["assertion_type"] == "text_equals"
    assert body["cases"][0]["assertions"][0]["status"] == "pass"

    _cleanup(execution_id)


async def test_report_download_generates_and_caches(client: AsyncClient):
    token, execution_id = await _setup(client)
    headers = {"Authorization": f"Bearer {token}"}
    report_id = await _report_id(execution_id)

    resp1 = await client.get(f"/api/reports/{report_id}/download", headers=headers)
    assert resp1.status_code == 200
    assert "text/html" in resp1.headers.get("content-type", "")
    assert "执行报告" in resp1.text

    html_path = reports_dir() / f"execution_{execution_id}" / "report.html"
    assert html_path.exists()

    async with SessionLocal() as db:
        report = await db.get(Report, report_id)
        assert report.report_path is not None
        assert str(html_path) == report.report_path

    resp2 = await client.get(f"/api/reports/{report_id}/download", headers=headers)
    assert resp2.status_code == 200
    assert resp2.text == resp1.text

    _cleanup(execution_id)


async def test_report_file_traversal_blocked(client: AsyncClient):
    token, execution_id = await _setup(client)
    headers = {"Authorization": f"Bearer {token}"}
    report_id = await _report_id(execution_id)

    resp = await client.get(f"/api/reports/{report_id}/files/..%2f..%2fsecret.txt", headers=headers)
    assert resp.status_code == 404
    resp2 = await client.get(f"/api/reports/{report_id}/files/screenshots/nope.png", headers=headers)
    assert resp2.status_code == 404
    _cleanup(execution_id)


async def test_report_permission_denied(client: AsyncClient):
    _token, execution_id = await _setup(client)
    report_id = await _report_id(execution_id)

    await client.post("/api/auth/register", json={"username": "pytest_rp_other", "email": "rpo@t.com", "password": "x12345678"})
    other_login = await client.post("/api/auth/login", json={"username": "pytest_rp_other", "password": "x12345678"})
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    resp = await client.get(f"/api/reports/{report_id}/detail", headers=other_headers)
    assert resp.status_code == 403
    _cleanup(execution_id)


async def test_report_screenshot_resolution_is_contained():
    """CR-01：报告生成器只能读取 execution_{id}/screenshots/ 内文件。"""
    exec_id = 424242
    bad = [
        r"C:\Windows\win.ini",
        r"\\server\share\x",
        "../x.png",
        f"execution_{exec_id}/../../etc/passwd",
        f"execution_{exec_id}/screenshots/..\\..\\win.ini",
        f"execution_{exec_id}/win.ini",
        f"execution_{exec_id}/screenshots/a.exe",
        f"execution_{exec_id}/screenshots/sub/a.png",
    ]
    for key in bad:
        assert validate_object_key(exec_id, key) is False
        assert resolve_screenshot_path(exec_id, key) is None

    good = f"execution_{exec_id}/screenshots/ab12.png"
    assert validate_object_key(exec_id, good) is True
    path = resolve_screenshot_path(exec_id, good)
    assert path is not None
    assert path.is_relative_to((reports_dir() / f"execution_{exec_id}").resolve())

    # 报告详情聚合时，越界路径被置空，不产生 base64
    detail = {
        "execution": {"id": exec_id},
        "cases": [
            {"steps": [{"screenshot": "screenshots/../../win.ini"}, {"screenshot": "screenshots/a.png"}]}
        ],
    }
    report_service._embed_screenshots(detail)
    assert detail["cases"][0]["steps"][0]["screenshot_base64"] is None

    _cleanup(exec_id)
