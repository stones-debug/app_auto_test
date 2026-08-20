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
