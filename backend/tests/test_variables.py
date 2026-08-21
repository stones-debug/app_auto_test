import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import User

ALICE = {"username": "pytest_var_alice", "email": "var-a@tl-tek.com", "password": "test123"}
BOB = {"username": "pytest_var_bob", "email": "var-b@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client: AsyncClient, reg: dict) -> tuple[str, int]:
    await client.post("/api/auth/register", json=reg)
    login = await client.post("/api/auth/login", json={"username": reg["username"], "password": reg["password"]})
    assert login.status_code == 200
    return login.json()["access_token"], login.json()["user"]["id"]


async def _set_admin(username: str) -> None:
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalar_one()
        user.is_admin = True
        await db.commit()


async def _create_project(client: AsyncClient, token: str, visibility: str = "private") -> int:
    resp = await client.post(
        "/api/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "变量测试项目", "visibility": visibility},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_private_project_variable_forbidden(client: AsyncClient):
    """CR-02：无项目访问权限的用户不能读取/创建/删除变量。"""
    alice_token, _ = await _register(client, ALICE)
    bob_token, _ = await _register(client, BOB)
    project_id = await _create_project(client, alice_token)

    bob_h = {"Authorization": f"Bearer {bob_token}"}
    assert (await client.get(f"/api/variables?scope=project&project_id={project_id}", headers=bob_h)).status_code == 403
    assert (
        await client.post(
            "/api/variables",
            headers=bob_h,
            json={"scope": "project", "project_id": project_id, "name": "x", "value": "1"},
        )
    ).status_code == 403


async def test_public_project_viewer_read_only(client: AsyncClient):
    """CR-02：公开项目 viewer 只能读变量，不能创建/修改/删除。"""
    alice_token, _ = await _register(client, ALICE)
    bob_token, _ = await _register(client, BOB)
    project_id = await _create_project(client, alice_token, visibility="public")

    alice_h = {"Authorization": f"Bearer {alice_token}"}
    bob_h = {"Authorization": f"Bearer {bob_token}"}

    created = await client.post(
        "/api/variables",
        headers=alice_h,
        json={"scope": "project", "project_id": project_id, "name": "env", "value": "dev"},
    )
    assert created.status_code == 201
    var_id = created.json()["id"]

    # viewer 可读
    listed = await client.get(f"/api/variables?scope=project&project_id={project_id}", headers=bob_h)
    assert listed.status_code == 200
    assert any(v["name"] == "env" for v in listed.json())

    # viewer 不可写
    assert (
        await client.post(
            "/api/variables",
            headers=bob_h,
            json={"scope": "project", "project_id": project_id, "name": "evil", "value": "1"},
        )
    ).status_code == 403
    assert (
        await client.put(f"/api/variables/{var_id}", headers=bob_h, json={"value": "hacked"})
    ).status_code == 403
    assert (await client.delete(f"/api/variables/{var_id}", headers=bob_h)).status_code == 403


async def test_member_can_write(client: AsyncClient):
    """CR-02：member 角色可创建/更新变量。"""
    alice_token, alice_id = await _register(client, ALICE)
    bob_token, bob_id = await _register(client, BOB)
    project_id = await _create_project(client, alice_token)

    add = await client.post(
        f"/api/projects/{project_id}/members",
        headers={"Authorization": f"Bearer {alice_token}"},
        json={"user_id": bob_id, "role": "member"},
    )
    assert add.status_code == 201

    bob_h = {"Authorization": f"Bearer {bob_token}"}
    created = await client.post(
        "/api/variables",
        headers=bob_h,
        json={"scope": "project", "project_id": project_id, "name": "btn", "value": "go"},
    )
    assert created.status_code == 201
    assert created.json()["value"] == "go"


async def test_global_variable_admin_only(client: AsyncClient):
    """CR-02：global 变量仅平台管理员可写；普通用户 403。"""
    alice_token, _ = await _register(client, ALICE)
    bob_token, _ = await _register(client, BOB)
    await _set_admin(ALICE["username"])

    # 普通用户创建全局变量 → 403（即使携带 project_id 也一样）
    resp = await client.post(
        "/api/variables",
        headers={"Authorization": f"Bearer {bob_token}"},
        json={"scope": "global", "project_id": 12345, "name": "g", "value": "1"},
    )
    assert resp.status_code == 403

    # 平台管理员创建成功，且 project_id 被清空
    created = await client.post(
        "/api/variables",
        headers={"Authorization": f"Bearer {alice_token}"},
        json={"scope": "global", "project_id": 12345, "name": "g", "value": "1"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["project_id"] is None
    assert body["suite_id"] is None
    assert body["case_id"] is None

    # 普通用户不能改/删全局变量
    assert (
        await client.put(f"/api/variables/{body['id']}", headers={"Authorization": f"Bearer {bob_token}"}, json={"value": "x"})
    ).status_code == 403
    assert (
        await client.delete(f"/api/variables/{body['id']}", headers={"Authorization": f"Bearer {bob_token}"})
    ).status_code == 403

    # 普通用户可读全局变量（产品规则：登录用户可读）
    listed = await client.get("/api/variables?scope=global", headers={"Authorization": f"Bearer {bob_token}"})
    assert listed.status_code == 200
    assert any(v["name"] == "g" for v in listed.json())


async def test_duplicate_name_conflict(client: AsyncClient):
    """CR-02：同作用域同名变量返回 409。"""
    token, _ = await _register(client, ALICE)
    project_id = await _create_project(client, token)
    h = {"Authorization": f"Bearer {token}"}
    body = {"scope": "project", "project_id": project_id, "name": "dup", "value": "1"}
    assert (await client.post("/api/variables", headers=h, json=body)).status_code == 201
    assert (await client.post("/api/variables", headers=h, json=body)).status_code == 409
    # 其他项目同名不冲突
    project2 = await _create_project(client, token)
    assert (
        await client.post(
            "/api/variables", headers=h, json={"scope": "project", "project_id": project2, "name": "dup", "value": "1"}
        )
    ).status_code == 201


async def test_suite_scope_resolves_project_and_checks_permission(client: AsyncClient):
    """CR-02：suite scope 反查所属项目做权限校验，project_id 落库为套件所属项目。"""
    alice_token, _ = await _register(client, ALICE)
    bob_token, _ = await _register(client, BOB)
    project_id = await _create_project(client, alice_token, visibility="public")

    alice_h = {"Authorization": f"Bearer {alice_token}"}
    suite = await client.post(
        f"/api/projects/{project_id}/suites",
        headers=alice_h,
        json={"name": "变量套件"},
    )
    assert suite.status_code == 201
    suite_id = suite.json()["id"]

    # viewer 用他人 suite_id 创建 → 403
    bob_h = {"Authorization": f"Bearer {bob_token}"}
    assert (
        await client.post(
            "/api/variables",
            headers=bob_h,
            json={"scope": "suite", "suite_id": suite_id, "name": "s", "value": "1"},
        )
    ).status_code == 403

    # owner 创建成功，project_id = 套件所属项目
    created = await client.post(
        "/api/variables",
        headers=alice_h,
        json={"scope": "suite", "suite_id": suite_id, "name": "s", "value": "1"},
    )
    assert created.status_code == 201
    assert created.json()["project_id"] == project_id
    assert created.json()["suite_id"] == suite_id


async def test_scope_fk_validation(client: AsyncClient):
    """CR-02：scope 缺少必需外键返回 422。"""
    token, _ = await _register(client, ALICE)
    h = {"Authorization": f"Bearer {token}"}
    assert (
        await client.post("/api/variables", headers=h, json={"scope": "project", "name": "x", "value": "1"})
    ).status_code == 422
    assert (
        await client.post("/api/variables", headers=h, json={"scope": "suite", "name": "x", "value": "1"})
    ).status_code == 422
    assert (
        await client.post("/api/variables", headers=h, json={"scope": "case", "name": "x", "value": "1"})
    ).status_code == 422
