import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

OWNER = {"username": "pytest_owner", "email": "owner@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup(client: AsyncClient) -> tuple[dict, int]:
    """注册 owner 并创建项目，返回 (headers, project_id)。"""
    reg = await client.post("/api/auth/register", json=OWNER)
    assert reg.status_code == 201
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/api/projects", json={"name": "元素项目"}, headers=headers
    )
    assert created.status_code == 201
    return headers, created.json()["id"]


async def test_module_crud(client: AsyncClient):
    headers, project_id = await _setup(client)

    created = await client.post(
        f"/api/projects/{project_id}/modules",
        json={"name": "登录模块"},
        headers=headers,
    )
    assert created.status_code == 201
    module_id = created.json()["id"]

    # 子模块
    child = await client.post(
        f"/api/projects/{project_id}/modules",
        json={"name": "子模块", "parent_id": module_id},
        headers=headers,
    )
    assert child.status_code == 201

    listing = await client.get(f"/api/projects/{project_id}/modules", headers=headers)
    assert listing.status_code == 200
    names = [m["name"] for m in listing.json()]
    assert "登录模块" in names

    # 子模块通过 parent_id 过滤
    child_list = await client.get(
        f"/api/projects/{project_id}/modules?parent_id={module_id}", headers=headers
    )
    assert len(child_list.json()) == 1
    assert child_list.json()[0]["name"] == "子模块"

    updated = await client.put(
        f"/api/modules/{module_id}", json={"name": "改名模块"}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "改名模块"

    deleted = await client.delete(f"/api/modules/{module_id}", headers=headers)
    assert deleted.status_code == 204


async def test_element_crud_and_usage(client: AsyncClient):
    headers, project_id = await _setup(client)

    created = await client.post(
        f"/api/projects/{project_id}/elements",
        json={
            "name": "登录按钮",
            "page_name": "登录页",
            "platform": "both",
            "locator_type": "id",
            "locator_value": "btn_login",
            "description": "登录按钮",
        },
        headers=headers,
    )
    assert created.status_code == 201
    element_id = created.json()["id"]

    listing = await client.get(
        f"/api/projects/{project_id}/elements?keyword=登录", headers=headers
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["locator_value"] == "btn_login"

    detail = await client.get(f"/api/elements/{element_id}", headers=headers)
    assert detail.status_code == 200

    updated = await client.put(
        f"/api/elements/{element_id}",
        json={"locator_value": "btn_login_new"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["locator_value"] == "btn_login_new"

    # usage：无用例引用时为空
    usage = await client.get(f"/api/elements/{element_id}/usage", headers=headers)
    assert usage.status_code == 200
    assert usage.json() == []

    deleted = await client.delete(f"/api/elements/{element_id}", headers=headers)
    assert deleted.status_code == 204


async def test_element_pages_grouping(client: AsyncClient):
    """B3：element-pages 按 page_name 分组统计，NULL 归为"未分组"。"""
    headers, project_id = await _setup(client)
    for name, page in [
        ("元素A", "登录页"),
        ("元素B", "登录页"),
        ("元素C", None),
        ("元素D", "首页"),
    ]:
        resp = await client.post(
            f"/api/projects/{project_id}/elements",
            json={"name": name, "page_name": page, "locator_type": "id", "locator_value": name},
            headers=headers,
        )
        assert resp.status_code == 201

    pages = (
        await client.get(f"/api/projects/{project_id}/element-pages", headers=headers)
    ).json()
    counts = {p["page_name"]: p["count"] for p in pages}
    assert counts["登录页"] == 2
    assert counts["未分组"] == 1
    assert counts["首页"] == 1


async def test_element_usage_step_orders(client: AsyncClient):
    """B3：usage 返回引用该元素的步骤 step_orders（仅 steps）。"""
    headers, project_id = await _setup(client)
    el = await client.post(
        f"/api/projects/{project_id}/elements",
        json={"name": "用户名", "locator_type": "id", "locator_value": "username"},
        headers=headers,
    )
    element_id = el.json()["id"]
    # 两个步骤引用该元素（order 1/2），一个断言也引用（不计入 step_orders）
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "引用用例",
            "steps": [
                {"order": 1, "action": "input", "element_id": element_id, "params": {"value": "admin"}},
                {"order": 2, "action": "click", "element_id": element_id, "params": {}},
            ],
            "assertions": [
                {"order": 1, "type": "text_equals", "element_id": element_id, "params": {"expected": "x"}}
            ],
        },
        headers=headers,
    )
    assert case.status_code == 201

    usage = (await client.get(f"/api/elements/{element_id}/usage", headers=headers)).json()
    assert len(usage) == 1
    assert usage[0]["case_name"] == "引用用例"
    assert usage[0]["step_orders"] == [1, 2]


async def test_element_permission(client: AsyncClient):
    """非成员不能创建元素。"""
    reg = await client.post("/api/auth/register", json=OWNER)
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (
        await client.post("/api/projects", json={"name": "权限项目"}, headers=headers)
    ).json()["id"]

    # 另一用户
    intruder = {"username": "pytest_intruder", "email": "intruder@tl-tek.com", "password": "test123"}
    intruder_token = (await client.post("/api/auth/register", json=intruder)).json()["access_token"]
    intruder_headers = {"Authorization": f"Bearer {intruder_token}"}

    resp = await client.post(
        f"/api/projects/{project_id}/elements",
        json={
            "name": "越权元素",
            "locator_type": "id",
            "locator_value": "x",
        },
        headers=intruder_headers,
    )
    assert resp.status_code == 403


# ---------- CR-18：模块父级归属与循环 ----------


async def test_module_update_rejects_cycle(client: AsyncClient):
    headers, project_id = await _setup(client)
    a = (await client.post(
        f"/api/projects/{project_id}/modules", json={"name": "A"}, headers=headers
    )).json()["id"]
    b = (await client.post(
        f"/api/projects/{project_id}/modules", json={"name": "B", "parent_id": a}, headers=headers
    )).json()["id"]
    c = (await client.post(
        f"/api/projects/{project_id}/modules", json={"name": "C", "parent_id": b}, headers=headers
    )).json()["id"]

    # 自身为父 → 400
    self_parent = await client.put(
        f"/api/modules/{a}", json={"parent_id": a}, headers=headers
    )
    assert self_parent.status_code == 400

    # a 的父设为后代 c → 循环 → 400
    cycle = await client.put(
        f"/api/modules/{a}", json={"parent_id": c}, headers=headers
    )
    assert cycle.status_code == 400
    assert "循环" in cycle.json()["detail"]

    # 跨项目父 → 404（已存在校验）
    p2 = (await client.post("/api/projects", json={"name": "项目2"}, headers=headers)).json()["id"]
    cross = await client.put(
        f"/api/modules/{a}", json={"parent_id": p2}, headers=headers
    )
    assert cross.status_code == 404


async def test_module_update_can_clear_parent(client: AsyncClient):
    """CR-25：显式传 parent_id=0 清空父模块。"""
    headers, project_id = await _setup(client)
    a = (await client.post(
        f"/api/projects/{project_id}/modules", json={"name": "A"}, headers=headers
    )).json()["id"]
    b = (await client.post(
        f"/api/projects/{project_id}/modules", json={"name": "B", "parent_id": a}, headers=headers
    )).json()["id"]

    cleared = await client.put(f"/api/modules/{b}", json={"parent_id": 0}, headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["parent_id"] is None
