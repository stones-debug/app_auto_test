import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

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


async def test_current_screen_mode_only_allows_single_case(client: AsyncClient):
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    suite_id = await _create_suite(client, token, project_id, case_id)
    device_id = await _create_agent_device(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    case_run = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": device_id, "parameters": {"attach_to_current_app": True}},
    )
    assert case_run.status_code == 201
    assert case_run.json()["parameters"]["attach_to_current_app"] is True

    suite_run = await client.post(
        f"/api/executions/suites/{suite_id}",
        headers=headers,
        json={"device_id": device_id, "parameters": {"attach_to_current_app": True}},
    )
    assert suite_run.status_code == 400
    assert "仅支持执行单个用例" in suite_run.json()["detail"]

    invalid = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": device_id, "parameters": {"use_pre_steps": "false"}},
    )
    assert invalid.status_code == 422


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

    # RQ-03/Step 5：retry 必须携带 body（{device_id, timeout_seconds?}），否则 400
    missing = await client.post(f"/api/executions/{execution_id}/retry", headers=headers)
    assert missing.status_code == 400

    retried = await client.post(
        f"/api/executions/{execution_id}/retry",
        headers=headers,
        json={"device_id": device_id},
    )
    assert retried.status_code == 201
    assert retried.json()["retry_of"] == execution_id
    assert retried.json()["status"] == "queued"


# ---------- Step 5：case/suite/batch 重试契约（retry_of / parameters / device / timeout） ----------


async def test_retry_case_copies_contract(client: AsyncClient):
    """Step 5：case 重试复制 retry_of、原 parameters、显式 device 与可选 timeout。"""
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    device_id = await _create_agent_device(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": device_id, "parameters": {"variables": {"btn_id": "btn_login"}}, "timeout_seconds": 900},
    )
    exec_id = created.json()["id"]

    retried = await client.post(
        f"/api/executions/{exec_id}/retry",
        headers=headers,
        json={"device_id": device_id, "timeout_seconds": 600},
    )
    assert retried.status_code == 201
    body = retried.json()
    assert body["retry_of"] == exec_id
    assert body["type"] == "case"
    assert body["case_id"] == case_id
    assert body["device_id"] == device_id
    assert body["timeout_seconds"] == 600
    assert body["parameters"]["variables"]["btn_id"] == "btn_login"


async def test_retry_suite_and_batch_copies_contract(client: AsyncClient):
    """Step 5：suite/batch 重试复制 retry_of、原 parameters（含 suite_ids）、显式 device。"""
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    suite_id = await _create_suite(client, token, project_id, case_id)
    device_id = await _create_agent_device(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    suite_run = await client.post(
        f"/api/executions/suites/{suite_id}",
        headers=headers,
        json={"device_id": device_id, "parameters": {"variables": {"env": "staging"}}},
    )
    suite_exec_id = suite_run.json()["id"]
    suite_retry = await client.post(
        f"/api/executions/{suite_exec_id}/retry",
        headers=headers,
        json={"device_id": device_id, "timeout_seconds": 720},
    )
    assert suite_retry.status_code == 201
    assert suite_retry.json()["retry_of"] == suite_exec_id
    assert suite_retry.json()["type"] == "suite"
    assert suite_retry.json()["suite_id"] == suite_id
    assert suite_retry.json()["timeout_seconds"] == 720
    assert suite_retry.json()["parameters"]["variables"]["env"] == "staging"

    batch = await client.post(
        "/api/executions/suites/batch",
        headers=headers,
        json={"suite_ids": [suite_id], "device_id": device_id, "parameters": {"marker": "batch-1"}},
    )
    batch_exec_id = batch.json()["id"]
    batch_retry = await client.post(
        f"/api/executions/{batch_exec_id}/retry",
        headers=headers,
        json={"device_id": device_id},
    )
    assert batch_retry.status_code == 201
    assert batch_retry.json()["retry_of"] == batch_exec_id
    assert batch_retry.json()["type"] == "batch"
    assert batch_retry.json()["parameters"]["suite_ids"] == [suite_id]
    assert batch_retry.json()["parameters"]["marker"] == "batch-1"
    # 未传 timeout 时沿用原 timeout_seconds
    assert batch_retry.json()["timeout_seconds"] == batch.json()["timeout_seconds"]


async def test_retry_timeout_falls_back_to_original(client: AsyncClient):
    """Step 5：retry 不传 timeout_seconds 时沿用原执行超时。"""
    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    device_id = await _create_agent_device(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": device_id, "timeout_seconds": 1500},
    )
    exec_id = created.json()["id"]
    retried = await client.post(
        f"/api/executions/{exec_id}/retry",
        headers=headers,
        json={"device_id": device_id},
    )
    assert retried.status_code == 201
    assert retried.json()["timeout_seconds"] == 1500


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


async def test_list_extended_filters_and_names(client: AsyncClient):
    """B4：执行列表 keyword/device_id 过滤；列表项含 project_name/device_name/created_by_name。"""
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
    execution_id = created.json()["id"]

    listed = (await client.get(f"/api/executions?project_id={project_id}", headers=headers)).json()
    item = listed["items"][0]
    assert item["project_name"] == "执行测试项目"
    assert item["device_name"] is not None
    assert item["created_by_name"] == "pytest_exec_user"

    # keyword=数字 → 匹配 execution ID
    by_id = await client.get(f"/api/executions?project_id={project_id}&keyword={execution_id}", headers=headers)
    assert by_id.json()["total"] == 1
    assert by_id.json()["items"][0]["id"] == execution_id

    # keyword=用例名 → 匹配
    by_name = await client.get(f"/api/executions?project_id={project_id}&keyword=登录用例", headers=headers)
    assert by_name.json()["total"] == 1

    # keyword 无匹配
    none = await client.get(f"/api/executions?project_id={project_id}&keyword=不存在对象", headers=headers)
    assert none.json()["total"] == 0

    # device_id 过滤
    by_device = await client.get(f"/api/executions?project_id={project_id}&device_id={device_id}", headers=headers)
    assert by_device.json()["total"] == 1
    wrong_device = await client.get(f"/api/executions?project_id={project_id}&device_id=999999", headers=headers)
    assert wrong_device.json()["total"] == 0


async def test_execution_detail_aggregates_steps_assertions(client: AsyncClient):
    """B4：执行详情聚合 steps/assertions，并填充 project_name/device_name/created_by_name。"""
    from app.core.database import SessionLocal
    from app.models import Device, Execution
    from app.services import worker_service
    from app.ws import handlers

    token = await _register_and_login(client)
    project_id = await _create_project(client, token)
    element_id = await _create_element(client, token, project_id)
    case_id = await _create_case(client, token, project_id, element_id)
    device_id = await _create_agent_device(client, token)
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        f"/api/executions/cases/{case_id}", headers=headers,
        json={"device_id": device_id, "parameters": {"variables": {"btn_id": "btn"}}},
    )
    execution_id = created.json()["id"]

    # 造 execution_cases 快照 + 置 running + 绑定设备，生成 steps/assertions 行
    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        execution.status = "running"
        await db.commit()
        agent_id = (await db.execute(select(Device.agent_id).where(Device.id == device_id))).scalar_one()
        await handlers.handle_step_result(
            db, agent_id,
            {"execution_id": execution_id, "case_id": case_id, "step_order": 1, "action": "click", "status": "passed", "duration": 100},
        )
        await handlers.handle_assertion_result(
            db, agent_id,
            {"execution_id": execution_id, "case_id": case_id, "assertions": [{"type": "text_equals", "expected": "a", "actual": "a", "status": "pass"}]},
        )

    detail = (await client.get(f"/api/executions/{execution_id}", headers=headers)).json()
    assert detail["project_name"] == "执行测试项目"
    assert detail["device_name"] is not None
    assert detail["created_by_name"] == "pytest_exec_user"
    assert len(detail["cases"]) == 1
    case = detail["cases"][0]
    assert case["steps"][0]["action"] == "click"
    assert case["steps"][0]["status"] == "passed"
    assert case["assertions"][0]["assertion_type"] == "text_equals"
    assert case["assertions"][0]["status"] == "pass"
