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


async def test_element_list_page_name_and_locator_filters(client: AsyncClient):
    """F8：元素列表支持 page_name（含未分组）与 locator_type 过滤。"""
    headers, project_id = await _setup(client)
    for name, page, locator in [
        ("元素1", "登录页", "id"),
        ("元素2", "登录页", "xpath"),
        ("元素3", None, "id"),
    ]:
        await client.post(
            f"/api/projects/{project_id}/elements",
            json={"name": name, "page_name": page, "locator_type": locator, "locator_value": name},
            headers=headers,
        )

    by_page = (await client.get(
        f"/api/projects/{project_id}/elements?page_name=登录页", headers=headers
    )).json()
    assert by_page["total"] == 2

    by_unset = (await client.get(
        f"/api/projects/{project_id}/elements?page_name=未分组", headers=headers
    )).json()
    assert by_unset["total"] == 1

    by_locator = (await client.get(
        f"/api/projects/{project_id}/elements?locator_type=xpath", headers=headers
    )).json()
    assert by_locator["total"] == 1
    assert by_locator["items"][0]["name"] == "元素2"


# ---------- V3：元素库全局化 / 仅创建者可改 / 复制 / 自定义分组 ----------


async def _register_user(client: AsyncClient, username: str, email: str) -> dict:
    token = (await client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": "test123"},
    )).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_element_global_list_and_project_filter(client: AsyncClient):
    """V3：全库列表可见两个项目的元素且带项目名；可按 project_id 筛选。"""
    h1, p1 = await _setup(client)
    p2 = (await client.post("/api/projects", json={"name": "元素项目2"}, headers=h1)).json()["id"]

    e1 = (await client.post(
        "/api/elements",
        headers=h1,
        json={"project_id": p1, "name": "全库元素A", "locator_type": "id", "locator_value": "a"},
    )).json()
    e2 = (await client.post(
        "/api/elements",
        headers=h1,
        json={"project_id": p2, "name": "全库元素B", "locator_type": "id", "locator_value": "b"},
    )).json()
    assert e1["project_name"] == "元素项目"
    assert e2["project_name"] == "元素项目2"

    listing = (await client.get("/api/elements", headers=h1)).json()
    names = {i["name"]: i for i in listing["items"]}
    assert "全库元素A" in names and "全库元素B" in names
    assert names["全库元素A"]["project_name"] == "元素项目"
    assert names["全库元素A"]["created_by_name"] == "pytest_owner"

    filtered = (await client.get(f"/api/elements?project_id={p2}", headers=h1)).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["name"] == "全库元素B"


async def test_element_creator_only_edit_delete(client: AsyncClient):
    """V3：非创建者（管理员也不行）不能编辑/删除元素；创建者可以。"""
    h_owner, p1 = await _setup(client)
    h_admin = await _register_user(client, "pytest_admin", "admin2@tl-tek.com")

    # admin 加入项目（owner 邀请）
    me = (await client.get("/api/auth/me", headers=h_admin)).json()
    await client.post(f"/api/projects/{p1}/members", headers=h_owner, json={"user_id": me["id"], "role": "admin"})

    el = (await client.post(
        "/api/elements",
        headers=h_owner,
        json={"project_id": p1, "name": "创建者元素", "locator_type": "id", "locator_value": "c"},
    )).json()
    el_id = el["id"]

    # 非创建者 admin 更新 → 403
    denied = await client.put(
        f"/api/elements/{el_id}", json={"locator_value": "hack"}, headers=h_admin
    )
    assert denied.status_code == 403
    assert "创建者" in denied.json()["detail"]
    # 非创建者删除 → 403
    assert (await client.delete(f"/api/elements/{el_id}", headers=h_admin)).status_code == 403

    # 创建者更新 → 200
    ok = (await client.put(
        f"/api/elements/{el_id}", json={"locator_value": "creator_update"}, headers=h_owner
    )).json()
    assert ok["locator_value"] == "creator_update"
    # 创建者删除 → 204
    assert (await client.delete(f"/api/elements/{el_id}", headers=h_owner)).status_code == 204


async def test_element_copy_creates_for_current_user(client: AsyncClient):
    """V3：复制按钮按当前用户新建相同元素（归属原项目）。"""
    h_owner, p1 = await _setup(client)
    h_other = await _register_user(client, "pytest_other", "other@tl-tek.com")
    el = (await client.post(
        "/api/elements",
        headers=h_owner,
        json={"project_id": p1, "name": "被复制", "page_name": "登录页", "locator_type": "id", "locator_value": "c1"},
    )).json()

    copied = (await client.post(f"/api/elements/{el['id']}/copy", headers=h_other)).json()
    assert copied["id"] != el["id"]
    assert copied["name"] == "被复制"
    assert copied["page_name"] == "登录页"
    assert copied["locator_value"] == "c1"
    assert copied["created_by_name"] == "pytest_other"
    assert copied["project_id"] == p1


async def test_element_group_custom(client: AsyncClient):
    """V3：自定义分组创建/重名 409/删除仅创建者；空分组出现在分组统计。"""
    h1, _ = await _setup(client)
    g = (await client.post("/api/elements/groups", headers=h1, json={"name": "我的新分组"})).json()
    group_id = g["id"]
    assert g["name"] == "我的新分组"

    conflict = await client.post("/api/elements/groups", headers=h1, json={"name": "我的新分组"})
    assert conflict.status_code == 409

    pages = (await client.get("/api/elements/pages", headers=h1)).json()
    names = {p["page_name"]: p["count"] for p in pages}
    assert names["我的新分组"] == 0

    h_other = await _register_user(client, "pytest_group2", "group2@tl-tek.com")
    assert (await client.delete(f"/api/elements/groups/{group_id}", headers=h_other)).status_code == 403
    assert (await client.delete(f"/api/elements/groups/{group_id}", headers=h1)).status_code == 204
    pages_after = (await client.get("/api/elements/pages", headers=h1)).json()
    assert "我的新分组" not in {p["page_name"] for p in pages_after}
