"""跨用户权限矩阵（发布门槛：任意普通用户无法访问他人私有项目；公开项目 viewer 只读）。"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.helpers import create_bound_agent_device

ALICE = {"username": "pytest_matrix_alice", "email": "m-a@tl-tek.com", "password": "test123"}
BOB = {"username": "pytest_matrix_bob", "email": "m-b@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client: AsyncClient, reg: dict) -> dict:
    await client.post("/api/auth/register", json=reg)
    login = await client.post("/api/auth/login", json={"username": reg["username"], "password": reg["password"]})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _setup_project(client: AsyncClient, headers: dict, visibility: str) -> dict:
    project = await client.post(
        "/api/projects", headers=headers, json={"name": "矩阵项目", "visibility": visibility}
    )
    assert project.status_code == 201
    project_id = project.json()["id"]
    element = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "按钮", "locator_type": "id", "locator_value": "btn"},
    )
    element_id = element.json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        headers=headers,
        json={
            "name": "用例",
            "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}],
            "assertions": [],
        },
    )
    case_id = case.json()["id"]
    suite = await client.post(f"/api/projects/{project_id}/suites", headers=headers, json={"name": "套件"})
    suite_id = suite.json()["id"]
    # Windows 方案 §3.3：执行创建必须指定已授权设备
    _agent_id, device_id = await create_bound_agent_device(ALICE["username"])
    execution = await client.post(
        f"/api/executions/cases/{case_id}", headers=headers,
        json={"device_id": device_id, "parameters": {}},
    )
    assert execution.status_code == 201, execution.text
    execution_id = execution.json()["id"]
    return {"project_id": project_id, "case_id": case_id, "suite_id": suite_id, "execution_id": execution_id}


async def test_private_project_full_denial(client: AsyncClient):
    """私有项目：非成员对所有读/写入口均 403。"""
    alice_h = await _register(client, ALICE)
    bob_h = await _register(client, BOB)
    ids = await _setup_project(client, alice_h, "private")
    pid = ids["project_id"]

    # 读
    for url in [
        f"/api/projects/{pid}",
        f"/api/projects/{pid}/elements",
        f"/api/projects/{pid}/cases",
        f"/api/projects/{pid}/suites",
        f"/api/variables?scope=project&project_id={pid}",
        f"/api/executions?project_id={pid}",
        f"/api/reports?project_id={pid}",
        f"/api/executions/{ids['execution_id']}",
    ]:
        assert (await client.get(url, headers=bob_h)).status_code == 403, url

    # 写
    assert (
        await client.post(
            f"/api/projects/{pid}/elements", headers=bob_h,
            json={"name": "x", "locator_type": "id", "locator_value": "y"},
        )
    ).status_code == 403
    assert (
        await client.post(f"/api/projects/{pid}/cases", headers=bob_h, json={"name": "x"})
    ).status_code == 403
    assert (
        await client.post(f"/api/projects/{pid}/suites", headers=bob_h, json={"name": "x"})
    ).status_code == 403
    assert (
        await client.post(
            "/api/variables", headers=bob_h,
            json={"scope": "project", "project_id": pid, "name": "x", "value": "1"},
        )
    ).status_code == 403
    assert (
        await client.post(f"/api/executions/cases/{ids['case_id']}", headers=bob_h, json={"parameters": {}})
    ).status_code == 403
    assert (
        await client.post(f"/api/executions/{ids['execution_id']}/stop", headers=bob_h)
    ).status_code == 403


async def test_public_project_viewer_readonly_matrix(client: AsyncClient):
    """公开项目：viewer 可读全部资源，写入口全部 403。"""
    alice_h = await _register(client, ALICE)
    bob_h = await _register(client, BOB)
    ids = await _setup_project(client, alice_h, "public")
    pid = ids["project_id"]

    # 读
    for url in [
        f"/api/projects/{pid}",
        f"/api/projects/{pid}/elements",
        f"/api/projects/{pid}/cases",
        f"/api/projects/{pid}/suites",
        f"/api/variables?scope=project&project_id={pid}",
        f"/api/executions?project_id={pid}",
        f"/api/executions/{ids['execution_id']}",
    ]:
        assert (await client.get(url, headers=bob_h)).status_code == 200, url

    # 写
    assert (
        await client.post(
            f"/api/projects/{pid}/elements", headers=bob_h,
            json={"name": "x", "locator_type": "id", "locator_value": "y"},
        )
    ).status_code == 403
    assert (
        await client.post(f"/api/projects/{pid}/cases", headers=bob_h, json={"name": "x"})
    ).status_code == 403
    assert (
        await client.post(
            "/api/variables", headers=bob_h,
            json={"scope": "project", "project_id": pid, "name": "x", "value": "1"},
        )
    ).status_code == 403
    assert (
        await client.post(f"/api/executions/cases/{ids['case_id']}", headers=bob_h, json={"parameters": {}})
    ).status_code == 403
    assert (
        await client.post(f"/api/executions/{ids['execution_id']}/stop", headers=bob_h)
    ).status_code == 403
