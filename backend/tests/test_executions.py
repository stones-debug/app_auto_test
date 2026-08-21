import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.helpers import create_bound_agent_device

REG = {"username": "pytest_exec_user", "email": "pytest_exec@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_and_login(client: AsyncClient) -> str:
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login",
        json={"username": REG["username"], "password": REG["password"]},
    )
    return login.json()["access_token"]


async def _create_project(client: AsyncClient, token: str, name: str = "执行测试项目") -> int:
    resp = await client.post(
        "/api/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "visibility": "private"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_element(client: AsyncClient, token: str, project_id: int) -> int:
    resp = await client.post(
        f"/api/projects/{project_id}/elements",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "登录按钮", "locator_type": "id", "locator_value": "${btn_id}"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_case(client: AsyncClient, token: str, project_id: int, element_id: int) -> int:
    resp = await client.post(
        f"/api/projects/{project_id}/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "登录用例",
            "steps": [
                {"order": 1, "action": "click", "element_id": element_id, "params": {"wait_timeout": 5}},
                {"order": 2, "action": "sleep", "params": {"duration": 1}},
            ],
            "assertions": [
                {"order": 1, "type": "element_exists", "element_id": element_id, "params": {}}
            ],
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_suite(client: AsyncClient, token: str, project_id: int, case_id: int) -> int:
    resp = await client.post(
        f"/api/projects/{project_id}/suites",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "执行测试套件"},
    )
    assert resp.status_code == 201
    suite_id = resp.json()["id"]
    add = await client.post(
        f"/api/suites/{suite_id}/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"case_id": case_id},
    )
    assert add.status_code in (200, 201)
    return suite_id


async def _create_agent_device(client: AsyncClient, token: str) -> int:
    """创建绑定当前用户的在线 Agent + idle 设备，返回 device_id（Windows 方案 §3.3）。"""
    _agent_id, device_id = await create_bound_agent_device(REG["username"])
    return device_id


async def test_create_case_execution(client: AsyncClient):
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    device_id = await _create_agent_device(client, token)

    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_id": device_id, "parameters": {"variables": {"btn_id": "btn_login"}}, "timeout_seconds": 300},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["type"] == "case"
    assert body["case_id"] == case_id
    assert body["status"] == "queued"
    assert body["timeout_seconds"] == 300
    assert body["parameters"]["variables"]["btn_id"] == "btn_login"


async def test_create_execution_requires_device(client: AsyncClient):
    """Windows 方案 §3.3：缺失 device_id → 400 DEVICE_REQUIRED（不再随机选机）。"""
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    suite_id = await _create_suite(client, token, project_id, case_id)

    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_id": None, "parameters": {}},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "DEVICE_REQUIRED"

    batch = await client.post(
        "/api/executions/suites/batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"suite_ids": [suite_id], "device_id": None, "parameters": {}},
    )
    assert batch.status_code == 400
    assert batch.json()["detail"]["code"] == "DEVICE_REQUIRED"


async def test_create_execution_rejects_busy_or_unbound_device(client: AsyncClient):
    """Windows 方案 §3.3：busy 设备 409；未绑定 Agent 的设备 403。"""
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    headers = {"Authorization": f"Bearer {token}"}

    # busy 设备
    _a, busy_device = await create_bound_agent_device(REG["username"], device_status="busy")
    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": busy_device, "parameters": {}},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "DEVICE_BUSY"

    # 未绑定 Agent 的设备（其他用户）
    await client.post(
        "/api/auth/register", json={"username": "pytest_other", "email": "o@t.com", "password": "x12345678"}
    )
    _o_agent, other_device = await create_bound_agent_device("pytest_other")
    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": other_device, "parameters": {}},
    )
    assert resp.status_code == 403


async def test_create_suite_and_batch_execution(client: AsyncClient):
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    suite_id = await _create_suite(client, token, project_id, case_id)
    device_id = await _create_agent_device(client, token)

    suite_run = await client.post(
        f"/api/executions/suites/{suite_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_id": device_id, "parameters": {}},
    )
    assert suite_run.status_code == 201
    assert suite_run.json()["type"] == "suite"

    batch = await client.post(
        "/api/executions/suites/batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"suite_ids": [suite_id], "device_id": device_id, "parameters": {}},
    )
    assert batch.status_code == 201
    assert batch.json()["type"] == "batch"
    assert batch.json()["parameters"]["suite_ids"] == [suite_id]


async def test_batch_requires_same_project(client: AsyncClient):
    token = await _register_and_login(client)
    p1 = await _create_project(client, token, "批处理项目A")
    p2 = await _create_project(client, token, "批处理项目B")
    s1 = (await client.post(
        f"/api/projects/{p1}/suites",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "套件A"},
    )).json()["id"]
    s2 = (await client.post(
        f"/api/projects/{p2}/suites",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "套件B"},
    )).json()["id"]
    resp = await client.post(
        "/api/executions/suites/batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"suite_ids": [s1, s2], "parameters": {}},
    )
    assert resp.status_code == 400


async def test_list_get_logs_stop_retry(client: AsyncClient):
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    device_id = await _create_agent_device(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": device_id, "parameters": {}},
    )
    execution_id = created.json()["id"]

    listed = await client.get(f"/api/executions?project_id={project_id}", headers=headers)
    assert listed.status_code == 200
    assert any(i["id"] == execution_id for i in listed.json()["items"])
    assert listed.json()["items"][0]["case_name"] == "登录用例"

    detail = await client.get(f"/api/executions/{execution_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "queued"

    logs = await client.get(f"/api/executions/{execution_id}/logs", headers=headers)
    assert logs.status_code == 200
    assert logs.json()["total"] == 0

    stopped = await client.post(f"/api/executions/{execution_id}/stop", headers=headers)
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"

    # Windows 方案 §2：取消即终态，stop_requested_at/finalized_at 必须落库
    detail_after = await client.get(f"/api/executions/{execution_id}", headers=headers)
    assert detail_after.json()["stop_requested_at"] is not None
    assert detail_after.json()["finalized_at"] is not None

    again = await client.post(f"/api/executions/{execution_id}/stop", headers=headers)
    assert again.status_code == 409

    retried = await client.post(f"/api/executions/{execution_id}/retry", headers=headers)
    assert retried.status_code == 201
    assert retried.json()["retry_of"] == execution_id
    assert retried.json()["status"] == "queued"


async def test_execution_permission_denied(client: AsyncClient):
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)

    await client.post("/api/auth/register", json={"username": "pytest_other", "email": "o@t.com", "password": "x12345678"})
    other_login = await client.post(
        "/api/auth/login", json={"username": "pytest_other", "password": "x12345678"}
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    resp = await client.post(
        f"/api/executions/cases/{case_id}", headers=other_headers, json={"parameters": {}}
    )
    assert resp.status_code == 403


async def test_public_viewer_cannot_create_stop_retry(client: AsyncClient):
    """CR-04：公共项目 viewer 不能创建/停止/重试执行，但可读详情。"""
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    # 设为公共项目
    await client.put(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"visibility": "public"},
    )
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)

    await client.post("/api/auth/register", json={"username": "pytest_viewer", "email": "v@t.com", "password": "x12345678"})
    viewer_login = await client.post(
        "/api/auth/login", json={"username": "pytest_viewer", "password": "x12345678"}
    )
    viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}

    # viewer 不能创建
    resp = await client.post(
        f"/api/executions/cases/{case_id}", headers=viewer_headers, json={"parameters": {}}
    )
    assert resp.status_code == 403

    # owner 创建执行后，viewer 不能停止/重试，但可读详情
    device_id = await _create_agent_device(client, token)
    created = await client.post(
        f"/api/executions/cases/{case_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_id": device_id, "parameters": {}},
    )
    execution_id = created.json()["id"]

    detail = await client.get(f"/api/executions/{execution_id}", headers=viewer_headers)
    assert detail.status_code == 200

    stopped = await client.post(f"/api/executions/{execution_id}/stop", headers=viewer_headers)
    assert stopped.status_code == 403

    retried = await client.post(f"/api/executions/{execution_id}/retry", headers=viewer_headers)
    assert retried.status_code == 403

    # viewer 也不能用套件/批量入口创建
    suite = await client.post(
        f"/api/projects/{project_id}/suites",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "套件V"},
    )
    suite_id = suite.json()["id"]
    suite_run = await client.post(
        f"/api/executions/suites/{suite_id}", headers=viewer_headers, json={"parameters": {}}
    )
    assert suite_run.status_code == 403
    batch = await client.post(
        "/api/executions/suites/batch", headers=viewer_headers, json={"suite_ids": [suite_id], "parameters": {}}
    )
    assert batch.status_code == 403


async def test_list_filters_status_and_type_with_consistent_total(client: AsyncClient):
    """CR-15：执行列表 status/type 过滤生效，total 与 items 同口径。"""
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    device_id = await _create_agent_device(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        f"/api/executions/cases/{case_id}", headers=headers,
        json={"device_id": device_id, "parameters": {}},
    )
    assert created.status_code == 201
    execution_id = created.json()["id"]

    all_items = await client.get(f"/api/executions?project_id={project_id}", headers=headers)
    assert all_items.json()["total"] == 1

    by_type = await client.get(f"/api/executions?project_id={project_id}&type=case", headers=headers)
    assert by_type.json()["total"] == 1
    assert by_type.json()["items"][0]["id"] == execution_id

    by_status = await client.get(f"/api/executions?project_id={project_id}&status=queued", headers=headers)
    assert by_status.json()["total"] == 1

    no_match = await client.get(f"/api/executions?project_id={project_id}&status=passed", headers=headers)
    assert no_match.json()["total"] == 0
    assert no_match.json()["items"] == []
