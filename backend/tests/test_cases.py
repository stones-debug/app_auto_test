import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

OWNER = {"username": "pytest_caseowner", "email": "caseowner@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup(client: AsyncClient) -> tuple[dict, int]:
    reg = await client.post("/api/auth/register", json=OWNER)
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post("/api/projects", json={"name": "用例项目"}, headers=headers)
    return headers, created.json()["id"]


async def _create_element(client: AsyncClient, headers: dict, project_id: int) -> int:
    resp = await client.post(
        f"/api/projects/{project_id}/elements",
        json={"name": "登录按钮", "locator_type": "id", "locator_value": "btn_login"},
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_case_crud(client: AsyncClient):
    headers, project_id = await _setup(client)
    element_id = await _create_element(client, headers, project_id)
    steps = [
        {"order": 1, "action": "launch_app", "params": {"package": "com.demo.app"}, "description": "启动应用"},
        {"order": 2, "action": "click", "element_id": element_id, "params": {"wait_timeout": 10}},
    ]
    assertions = [
        {"order": 1, "type": "element_exists", "element_id": element_id, "params": {"expected": "exists"}},
    ]

    created = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "登录用例",
            "description": "测试登录",
            "status": "active",
            "steps": steps,
            "assertions": assertions,
            "variables": {"username": "u1"},
        },
        headers=headers,
    )
    assert created.status_code == 201
    case_id = created.json()["id"]
    assert len(created.json()["steps"]) == 2

    # 列表 + 关键字筛选
    listing = await client.get(
        f"/api/projects/{project_id}/cases?keyword=登录", headers=headers
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    # 详情
    detail = await client.get(f"/api/cases/{case_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["variables"]["username"] == "u1"

    # 更新
    updated = await client.put(
        f"/api/cases/{case_id}",
        json={"name": "登录用例V2", "steps": steps + [{"order": 3, "action": "sleep", "params": {"duration": 1}}]},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "登录用例V2"
    assert len(updated.json()["steps"]) == 3

    # 克隆
    cloned = await client.post(f"/api/cases/{case_id}/clone", headers=headers)
    assert cloned.status_code == 201
    assert "副本" in cloned.json()["name"]
    assert cloned.json()["status"] == "draft"
    assert len(cloned.json()["steps"]) == 3

    # 删除
    deleted = await client.delete(f"/api/cases/{case_id}", headers=headers)
    assert deleted.status_code == 204
    gone = await client.get(f"/api/cases/{case_id}", headers=headers)
    assert gone.status_code == 404


async def test_case_jsonb_validation(client: AsyncClient):
    """用例可按 JSONB 结构校验 steps。"""
    headers, project_id = await _setup(client)
    element_id = await _create_element(client, headers, project_id)
    created = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "断言用例",
            "assertions": [{"order": 1, "type": "text_equals", "element_id": element_id, "params": {"expected": "成功"}}],
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["assertions"][0]["type"] == "text_equals"


# ---------- CR-09：严格 schema ----------


async def test_unknown_action_rejected(client: AsyncClient):
    """CR-09：未知动作返回 422。"""
    headers, project_id = await _setup(client)
    resp = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "坏用例",
            "steps": [{"order": 1, "action": "hack_device", "params": {}}],
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_unknown_assertion_rejected(client: AsyncClient):
    headers, project_id = await _setup(client)
    resp = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "坏断言用例",
            "assertions": [{"order": 1, "type": "hack_assert", "params": {}}],
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_bad_param_type_rejected(client: AsyncClient):
    """CR-09：参数类型错误返回 422（tap_coordinate 需要整数 x/y）。"""
    headers, project_id = await _setup(client)
    resp = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "坐标用例",
            "steps": [{"order": 1, "action": "tap_coordinate", "params": {"x": "abc", "y": 10}}],
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_regex_match_uses_pattern(client: AsyncClient):
    """CR-09：regex_match 使用 pattern 字段（前端曾误用 expected）。"""
    headers, project_id = await _setup(client)
    element_id = await _create_element(client, headers, project_id)
    created = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "正则用例",
            "assertions": [
                {"order": 1, "type": "regex_match", "element_id": element_id, "params": {"pattern": r"^\d{4}$"}}
            ],
        },
        headers=headers,
    )
    assert created.status_code == 201
    params = created.json()["assertions"][0]["params"]
    assert params.get("pattern") == r"^\d{4}$"


async def test_cross_project_element_rejected(client: AsyncClient):
    """CR-09：步骤引用其他项目的元素返回 400。"""
    headers, project_id = await _setup(client)
    element_id = await _create_element(client, headers, project_id)

    # 第二个项目
    p2 = await client.post("/api/projects", json={"name": "另一个项目"}, headers=headers)
    project2_id = p2.json()["id"]

    resp = await client.post(
        f"/api/projects/{project2_id}/cases",
        json={
            "name": "越权元素用例",
            "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "元素不属于该项目" in resp.json()["detail"]


async def test_nonexistent_element_rejected(client: AsyncClient):
    headers, project_id = await _setup(client)
    resp = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "幽灵元素用例",
            "steps": [{"order": 1, "action": "click", "element_id": 999999, "params": {}}],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "元素不存在" in resp.json()["detail"]


async def test_list_status_filter_contract(client: AsyncClient):
    """CR-15：用例列表 status 过滤生效（前端发送 status）。"""
    headers, project_id = await _setup(client)
    await client.post(
        f"/api/projects/{project_id}/cases",
        json={"name": "启用用例", "status": "active", "steps": [], "assertions": []},
        headers=headers,
    )
    await client.post(
        f"/api/projects/{project_id}/cases",
        json={"name": "草稿用例", "status": "draft", "steps": [], "assertions": []},
        headers=headers,
    )

    active = await client.get(f"/api/projects/{project_id}/cases?status=active", headers=headers)
    assert active.status_code == 200
    assert active.json()["total"] == 1
    assert active.json()["items"][0]["name"] == "启用用例"

    all_cases = await client.get(f"/api/projects/{project_id}/cases", headers=headers)
    assert all_cases.json()["total"] == 2


async def test_pagination_page_size_cap(client: AsyncClient):
    """分页：page_size=200 合法（前端 ElementSelector/Suite/日志均用 200），超上限 422。"""
    headers, project_id = await _setup(client)
    for i in range(3):
        await client.post(
            f"/api/projects/{project_id}/cases",
            json={"name": f"用例{i}", "steps": [], "assertions": []},
            headers=headers,
        )

    # 200 在允许范围内
    big = await client.get(f"/api/projects/{project_id}/cases?page=1&page_size=200", headers=headers)
    assert big.status_code == 200
    assert big.json()["total"] == 3
    assert len(big.json()["items"]) == 3

    # 超过上限 → 422
    too_big = await client.get(f"/api/projects/{project_id}/cases?page=1&page_size=201", headers=headers)
    assert too_big.status_code == 422
