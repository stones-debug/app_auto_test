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
