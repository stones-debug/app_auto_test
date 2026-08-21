import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

ALICE = {"username": "pytest_alice", "email": "alice@tl-tek.com", "password": "test123"}
BOB = {"username": "pytest_bob", "email": "bob@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client: AsyncClient, user: dict) -> tuple[str, int]:
    resp = await client.post("/api/auth/register", json=user)
    assert resp.status_code == 201
    return resp.json()["access_token"], resp.json()["user"]["id"]


async def test_project_crud(client: AsyncClient):
    token, _ = await _register(client, ALICE)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/projects",
        json={"name": "测试项目", "description": "desc", "visibility": "private"},
        headers=headers,
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert created.json()["role"] == "owner"

    listing = await client.get("/api/projects", headers=headers)
    assert listing.status_code == 200
    assert project_id in [p["id"] for p in listing.json()["items"]]
    # B1：PageResult 统一携带 page/page_size
    assert listing.json()["page"] == 1
    assert listing.json()["page_size"] == 20
    assert listing.json()["total"] >= 1

    detail = await client.get(f"/api/projects/{project_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "测试项目"

    updated = await client.put(
        f"/api/projects/{project_id}",
        json={"name": "改名项目"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "改名项目"

    deleted = await client.delete(f"/api/projects/{project_id}", headers=headers)
    assert deleted.status_code == 204
    gone = await client.get(f"/api/projects/{project_id}", headers=headers)
    assert gone.status_code == 404


async def test_project_permission(client: AsyncClient):
    alice_token, alice_id = await _register(client, ALICE)
    bob_token, bob_id = await _register(client, BOB)
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    created = await client.post(
        "/api/projects",
        json={"name": "私有项目"},
        headers=alice_headers,
    )
    project_id = created.json()["id"]

    forbidden = await client.get(f"/api/projects/{project_id}", headers=bob_headers)
    assert forbidden.status_code == 403

    put_forbidden = await client.put(
        f"/api/projects/{project_id}",
        json={"name": "hack"},
        headers=bob_headers,
    )
    assert put_forbidden.status_code == 403

    # Alice 添加 Bob 为 viewer
    add = await client.post(
        f"/api/projects/{project_id}/members",
        json={"user_id": bob_id, "role": "viewer"},
        headers=alice_headers,
    )
    assert add.status_code == 201

    # Bob 现在能查看
    view = await client.get(f"/api/projects/{project_id}", headers=bob_headers)
    assert view.status_code == 200
    assert view.json()["role"] == "viewer"

    # 但 viewer 不能修改
    edit = await client.put(
        f"/api/projects/{project_id}",
        json={"name": "hack"},
        headers=bob_headers,
    )
    assert edit.status_code == 403

    # viewer 也不能删除
    rm = await client.delete(f"/api/projects/{project_id}", headers=bob_headers)
    assert rm.status_code == 403

    # 成员列表
    members = await client.get(f"/api/projects/{project_id}/members", headers=alice_headers)
    assert members.status_code == 200
    usernames = [m["username"] for m in members.json()]
    assert BOB["username"] in usernames


async def test_project_counts(client: AsyncClient):
    token, _ = await _register(client, ALICE)
    headers = {"Authorization": f"Bearer {token}"}

    project = await client.post("/api/projects", json={"name": "计数项目", "visibility": "private"}, headers=headers)
    project_id = project.json()["id"]
    # 建 1 元素 + 1 用例 + 1 套件
    element = await client.post(
        f"/api/projects/{project_id}/elements",
        json={"name": "按钮", "locator_type": "id", "locator_value": "btn"},
        headers=headers,
    )
    element_id = element.json()["id"]
    await client.post(
        f"/api/projects/{project_id}/cases",
        json={"name": "用例", "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}], "assertions": []},
        headers=headers,
    )
    await client.post(
        f"/api/projects/{project_id}/suites", json={"name": "套件"}, headers=headers
    )

    listing = await client.get("/api/projects", headers=headers)
    item = next(p for p in listing.json()["items"] if p["id"] == project_id)
    assert item["case_count"] == 1
    assert item["element_count"] == 1
    assert item["suite_count"] == 1

    detail = await client.get(f"/api/projects/{project_id}", headers=headers)
    assert detail.json()["case_count"] == 1
    assert detail.json()["element_count"] == 1
    assert detail.json()["suite_count"] == 1


# ---------- B2 项目成员管理 ----------


async def _setup_project_with_member(client: AsyncClient) -> tuple[dict, dict, int, int]:
    """注册 ALICE(owner) 与 BOB，建项目并把 BOB 加为 viewer。返回 (alice, bob, project_id, bob_id)。"""
    alice_token, alice_id = await _register(client, ALICE)
    bob_token, bob_id = await _register(client, BOB)
    alice = {"token": alice_token, "id": alice_id, "headers": {"Authorization": f"Bearer {alice_token}"}}
    bob = {"token": bob_token, "id": bob_id, "headers": {"Authorization": f"Bearer {bob_token}"}}
    project = await client.post("/api/projects", json={"name": "成员项目", "visibility": "private"}, headers=alice["headers"])
    project_id = project.json()["id"]
    add = await client.post(
        f"/api/projects/{project_id}/members",
        json={"user_id": bob_id, "role": "viewer"},
        headers=alice["headers"],
    )
    assert add.status_code == 201
    return alice, bob, project_id, bob_id


async def test_members_list_includes_owner_virtual_row(client: AsyncClient):
    """B2：成员列表含 owner 虚拟行（membership_id=null），随后是真实成员。"""
    alice, bob, project_id, _ = await _setup_project_with_member(client)
    members = (await client.get(f"/api/projects/{project_id}/members", headers=alice["headers"])).json()
    assert members[0]["role"] == "owner"
    assert members[0]["membership_id"] is None
    assert members[0]["user_id"] == alice["id"]
    assert members[0]["username"] == ALICE["username"]
    assert any(m["role"] == "viewer" and m["username"] == BOB["username"] for m in members)
    assert all("membership_id" in m for m in members)


async def test_member_candidates_excludes_owner_and_existing(client: AsyncClient):
    """B2：候选人排除 owner 与已有成员；模糊搜索用户名。"""
    alice, bob, project_id, bob_id = await _setup_project_with_member(client)
    candidates = (
        await client.get(f"/api/projects/{project_id}/member-candidates", headers=alice["headers"])
    ).json()
    ids = [c["id"] for c in candidates]
    assert alice["id"] not in ids
    assert bob_id not in ids

    searched = (
        await client.get(
            f"/api/projects/{project_id}/member-candidates", params={"keyword": ALICE["username"]}, headers=alice["headers"]
        )
    ).json()
    assert searched == []


async def test_member_candidates_requires_admin(client: AsyncClient):
    """B2：viewer/member 不能调用候选人接口（仅 owner/admin）。"""
    alice, bob, project_id, _ = await _setup_project_with_member(client)
    # Bob 是 viewer → 403
    resp = await client.get(f"/api/projects/{project_id}/member-candidates", headers=bob["headers"])
    assert resp.status_code == 403


async def test_update_member_role_by_owner(client: AsyncClient):
    """B2：owner 可修改成员角色；PATCH 返回新角色。"""
    alice, bob, project_id, bob_id = await _setup_project_with_member(client)
    updated = await client.patch(
        f"/api/projects/{project_id}/members/{bob_id}",
        json={"role": "member"},
        headers=alice["headers"],
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "member"

    # Bob 现在可写（member）
    view = await client.get(f"/api/projects/{project_id}", headers=bob["headers"])
    assert view.status_code == 200
    assert view.json()["role"] == "member"


async def test_admin_cannot_promote_or_manage_admin(client: AsyncClient):
    """B2：admin 只能管理 member/viewer；不能修改自己的角色。"""
    alice, bob, project_id, bob_id = await _setup_project_with_member(client)
    # 注册第三个用户 charlie 作为 admin
    charlie = {"username": "pytest_charlie", "email": "charlie@tl-tek.com", "password": "test123"}
    charlie_token, charlie_id = await _register(client, charlie)

    # 先把 Bob 提升为 admin（owner 操作）
    await client.patch(
        f"/api/projects/{project_id}/members/{bob_id}", json={"role": "admin"}, headers=alice["headers"]
    )
    # 把 charlie 加为 member（owner 操作）
    add = await client.post(
        f"/api/projects/{project_id}/members",
        json={"user_id": charlie_id, "role": "member"},
        headers=alice["headers"],
    )
    assert add.status_code == 201

    # admin 不能给 member 授予 admin
    promote = await client.patch(
        f"/api/projects/{project_id}/members/{charlie_id}", json={"role": "admin"}, headers=bob["headers"]
    )
    assert promote.status_code == 403

    # admin 不能修改自己的角色
    self_patch = await client.patch(
        f"/api/projects/{project_id}/members/{bob_id}", json={"role": "member"}, headers=bob["headers"]
    )
    assert self_patch.status_code == 409

    # admin 可以把 member 降为 viewer
    demote = await client.patch(
        f"/api/projects/{project_id}/members/{charlie_id}", json={"role": "viewer"}, headers=bob["headers"]
    )
    assert demote.status_code == 200
    assert demote.json()["role"] == "viewer"


async def test_remove_member(client: AsyncClient):
    """B2：owner 可移除成员；移除后用户失去项目访问。"""
    alice, bob, project_id, bob_id = await _setup_project_with_member(client)
    rm = await client.delete(f"/api/projects/{project_id}/members/{bob_id}", headers=alice["headers"])
    assert rm.status_code == 204

    view = await client.get(f"/api/projects/{project_id}", headers=bob["headers"])
    assert view.status_code == 403


async def test_member_role_boundaries_403_409(client: AsyncClient):
    """B2：viewer 不能增删改成员；owner 不可被添加/修改/移除。"""
    alice, bob, project_id, bob_id = await _setup_project_with_member(client)
    charlie = {"username": "pytest_dave", "email": "dave@tl-tek.com", "password": "test123"}
    charlie_token, charlie_id = await _register(client, charlie)

    # viewer 尝试添加/修改/删除成员 → 403
    assert (
        await client.post(
            f"/api/projects/{project_id}/members",
            json={"user_id": charlie_id, "role": "viewer"},
            headers=bob["headers"],
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"/api/projects/{project_id}/members/{charlie_id}", json={"role": "viewer"}, headers=bob["headers"]
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/projects/{project_id}/members/{charlie_id}", headers=bob["headers"])
    ).status_code == 403

    # owner 不可被添加为成员 → 409
    add_owner = await client.post(
        f"/api/projects/{project_id}/members",
        json={"user_id": alice["id"], "role": "viewer"},
        headers=alice["headers"],
    )
    assert add_owner.status_code == 409

    # owner 虚拟行不可修改/移除 → 409
    assert (
        await client.patch(
            f"/api/projects/{project_id}/members/{alice['id']}", json={"role": "member"}, headers=alice["headers"]
        )
    ).status_code == 409
    assert (
        await client.delete(f"/api/projects/{project_id}/members/{alice['id']}", headers=alice["headers"])
    ).status_code == 409

    # 重复添加同一成员 → 409 MEMBER_ALREADY_EXISTS
    duplicate = await client.post(
        f"/api/projects/{project_id}/members",
        json={"user_id": bob_id, "role": "viewer"},
        headers=alice["headers"],
    )
    assert duplicate.status_code == 409
