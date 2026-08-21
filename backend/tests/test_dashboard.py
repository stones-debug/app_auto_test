"""B5：Dashboard overview 接口。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.helpers import create_bound_agent_device

USER = {"username": "pytest_dash_user", "email": "dash@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_and_login(client: AsyncClient) -> str:
    await client.post("/api/auth/register", json=USER)
    login = await client.post(
        "/api/auth/login", json={"username": USER["username"], "password": USER["password"]}
    )
    return login.json()["access_token"]


async def test_dashboard_empty_for_new_user(client: AsyncClient):
    token = await _register_and_login(client)
    resp = await client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"] == "7d"
    assert body["stats"]["project_count"] == 0
    assert body["stats"]["period_execution_count"] == 0
    assert body["recent_executions"] == []
    assert body["trend"] == []


async def test_dashboard_counts_and_isolation(client: AsyncClient):
    """B5：项目创建后 project_count 生效；其他用户不可见该项目的执行。"""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post("/api/projects", headers=headers, json={"name": "Dashboard项目", "visibility": "private"})
    project_id = project.json()["id"]

    # 绑定设备，创建一次执行（无需起跑）
    _agent_id, device_id = await create_bound_agent_device(USER["username"])
    element = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "按钮", "locator_type": "id", "locator_value": "btn"},
    )
    element_id = element.json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        headers=headers,
        json={"name": "用例", "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}], "assertions": []},
    )
    case_id = case.json()["id"]
    exec_resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": device_id, "parameters": {}},
    )
    assert exec_resp.status_code == 201
    execution_id = exec_resp.json()["id"]

    # 把执行置为 passed 计入成功率
    from datetime import UTC, datetime

    from app.core.database import SessionLocal
    from app.models import Execution

    async with SessionLocal() as db:
        e = await db.get(Execution, execution_id)
        e.status = "passed"
        e.finished_at = datetime.now(UTC)
        await db.commit()

    body = (await client.get("/api/dashboard/overview", headers=headers)).json()
    assert body["stats"]["project_count"] == 1
    assert body["stats"]["period_execution_count"] == 1
    assert body["stats"]["success_rate"] == 100.0
    assert body["status_counts"]["passed"] == 1
    assert body["status_counts"]["running"] == 0
    assert body["recent_executions"][0]["id"] == execution_id

    # 设备统计（绑定 Agent 下的设备）
    assert body["stats"]["device_count"] == 1
    assert body["stats"]["available_device_count"] == 1

    # 隔离：另一个用户看不到该项目执行
    other = {"username": "pytest_dash_other", "email": "dasho@t.com", "password": "test123"}
    await client.post("/api/auth/register", json=other)
    other_login = await client.post(
        "/api/auth/login", json={"username": other["username"], "password": other["password"]}
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
    other_body = (await client.get("/api/dashboard/overview", headers=other_headers)).json()
    assert other_body["stats"]["project_count"] == 0
    assert other_body["status_counts"]["passed"] == 0

    # 指定 project_id 时校验 viewer+ 权限
    forbidden = await client.get(
        f"/api/dashboard/overview?project_id={project_id}", headers=other_headers
    )
    assert forbidden.status_code == 403


async def test_dashboard_range_validation(client: AsyncClient):
    token = await _register_and_login(client)
    resp = await client.get(
        "/api/dashboard/overview?range=30d", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["range"] == "30d"

    # 非法 range 回退 7d
    resp2 = await client.get(
        "/api/dashboard/overview?range=xyz", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp2.status_code == 200
    assert resp2.json()["range"] == "7d"
