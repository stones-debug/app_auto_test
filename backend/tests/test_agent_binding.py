"""Windows 方案 §3.2：用户 Key 生命周期与 Agent 多用户绑定测试。"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import Agent, AgentUser

ALICE = {"username": "pytest_bind_alice", "email": "b-a@tl-tek.com", "password": "test123"}
BOB = {"username": "pytest_bind_bob", "email": "b-b@tl-tek.com", "password": "test123"}
CAROL = {"username": "pytest_bind_carol", "email": "b-c@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client: AsyncClient, reg: dict) -> str:
    await client.post("/api/auth/register", json=reg)
    login = await client.post("/api/auth/login", json={"username": reg["username"], "password": reg["password"]})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _make_key(client: AsyncClient, token: str) -> str:
    resp = await client.post("/api/me/agent-key", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


async def _bind(client: AsyncClient, user_key: str, install_id: str) -> dict:
    resp = await client.post(
        "/api/agent/bind", json={"user_key": user_key, "install_id": install_id}
    )
    assert resp.status_code == 201, f"bind 失败: {resp.status_code} {resp.text}"
    return resp.json()


# ---------- Key 生命周期 ----------


async def test_key_create_get_regenerate(client: AsyncClient):
    token = await _register(client, ALICE)
    headers = {"Authorization": f"Bearer {token}"}

    empty = await client.get("/api/me/agent-key", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["exists"] is False

    created = await client.post("/api/me/agent-key", headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("uak_")
    assert body["public_id"]

    again = await client.post("/api/me/agent-key", headers=headers)
    assert again.status_code == 409

    got = await client.get("/api/me/agent-key", headers=headers)
    assert got.json()["exists"] is True
    assert got.json()["key"] == body["key"]  # 可长期查看（解密返回）

    regen = await client.post("/api/me/agent-key/regenerate", headers=headers)
    assert regen.status_code == 200
    assert regen.json()["key"] != body["key"]

    got2 = await client.get("/api/me/agent-key", headers=headers)
    assert got2.json()["key"] == regen.json()["key"]


# ---------- 首次绑定 / 追加绑定 / 可见性 ----------


async def test_first_and_additional_binding(client: AsyncClient):
    alice_t = await _register(client, ALICE)
    bob_t = await _register(client, BOB)
    carol_t = await _register(client, CAROL)
    alice_key = await _make_key(client, alice_t)
    bob_key = await _make_key(client, bob_t)
    install_id = "install-win-001"

    # 首次绑定：创建 Agent 与机器 PSK
    first = await _bind(client, alice_key, install_id)
    assert first["agent_id"] == install_id
    assert first["machine_psk"]
    assert first["revoke_credential"]
    first_psk = first["machine_psk"]

    # 旧版 Agent 即使夹带错误 machine_psk，服务端也完全忽略，只校验用户 Key。
    rebound_resp = await client.post(
        "/api/agent/bind",
        json={
            "user_key": alice_key,
            "install_id": install_id,
            "machine_psk": "sk-stale-from-old-agent",
        },
    )
    assert rebound_resp.status_code == 201
    rebound = rebound_resp.json()
    assert rebound["machine_psk"] != first_psk

    # 追加绑定 Bob 同样只需要有效用户 Key，再次刷新机器 PSK。
    second = await _bind(client, bob_key, install_id)
    assert second["machine_psk"] != rebound["machine_psk"]
    assert second["revoke_credential"]
    machine_psk = second["machine_psk"]

    # 机器 PSK 认证查询绑定列表
    listed = await client.get(
        f"/api/agent/bindings?agent_id={install_id}",
        headers={"X-Agent-Key": machine_psk},
    )
    assert listed.status_code == 200
    usernames = {item["username"] for item in listed.json()}
    assert usernames == {ALICE["username"], BOB["username"]}

    # 每次绑定都会旋转凭据，旧机器 PSK 立即失效。
    stale = await client.get(
        f"/api/agent/bindings?agent_id={install_id}",
        headers={"X-Agent-Key": first_psk},
    )
    assert stale.status_code == 401

    # 错误机器 PSK → 401
    bad = await client.get(
        f"/api/agent/bindings?agent_id={install_id}",
        headers={"X-Agent-Key": "sk-wrong"},
    )
    assert bad.status_code == 401

    # 错误用户 Key → 401
    bad_key = await client.post("/api/agent/bind", json={"user_key": "uak_bad_bad", "install_id": install_id})
    assert bad_key.status_code == 401

    # 第三用户（Carol）看不到该 Agent
    carol_headers = {"Authorization": f"Bearer {carol_t}"}
    agents = (await client.get("/api/agents", headers=carol_headers)).json()
    assert all(a["agent_id"] != install_id for a in agents)

    # Alice 与 Bob 可见；管理员可见全部
    alice_agents = (await client.get("/api/agents", headers={"Authorization": f"Bearer {alice_t}"})).json()
    assert any(a["agent_id"] == install_id for a in alice_agents)
    bob_agents = (await client.get("/api/agents", headers={"Authorization": f"Bearer {bob_t}"})).json()
    assert any(a["agent_id"] == install_id for a in bob_agents)


# ---------- 解绑 ----------


async def test_unbind_machine_and_user_sides(client: AsyncClient):
    alice_t = await _register(client, ALICE)
    bob_t = await _register(client, BOB)
    alice_key = await _make_key(client, alice_t)
    bob_key = await _make_key(client, bob_t)
    install_id = "install-win-002"

    first = await _bind(client, alice_key, install_id)
    assert first["machine_psk"]
    second = await _bind(client, bob_key, install_id)
    machine_psk = second["machine_psk"]
    bob_revoke = second["revoke_credential"]

    listed = await client.get(
        f"/api/agent/bindings?agent_id={install_id}", headers={"X-Agent-Key": machine_psk}
    )
    bob_binding_id = next(i["id"] for i in listed.json() if i["username"] == BOB["username"])

    # 机器侧解绑 Bob（凭据错误 → 401；正确 → 204）
    wrong = await client.delete(
        f"/api/agent/bindings/{bob_binding_id}?agent_id={install_id}",
        headers={"X-Agent-Key": machine_psk, "X-Revoke-Credential": "wrong"},
    )
    assert wrong.status_code == 401
    ok = await client.delete(
        f"/api/agent/bindings/{bob_binding_id}?agent_id={install_id}",
        headers={"X-Agent-Key": machine_psk, "X-Revoke-Credential": bob_revoke},
    )
    assert ok.status_code == 204

    listed2 = await client.get(
        f"/api/agent/bindings?agent_id={install_id}", headers={"X-Agent-Key": machine_psk}
    )
    assert {i["username"] for i in listed2.json()} == {ALICE["username"]}

    # 用户侧撤销自己（Alice 撤销自己 → 列表不再出现）
    async with SessionLocal() as db:
        agent_row = (
            await db.execute(select(Agent).where(Agent.agent_id == install_id))
        ).scalar_one()
        agent_db_id = agent_row.id
    revoke_me = await client.delete(
        f"/api/agents/{agent_db_id}/bindings/me",
        headers={"Authorization": f"Bearer {alice_t}"},
    )
    assert revoke_me.status_code == 204
    alice_agents = (await client.get("/api/agents", headers={"Authorization": f"Bearer {alice_t}"})).json()
    assert all(a["agent_id"] != install_id for a in alice_agents)
    async with SessionLocal() as db:
        remaining = (
            await db.execute(select(AgentUser).where(AgentUser.agent_id == agent_db_id))
        ).scalars().all()
        assert len(remaining) == 0


# ---------- 重置 Key 后旧 Key 失效 ----------


async def test_regenerated_key_cannot_bind_new_agent(client: AsyncClient):
    alice_t = await _register(client, ALICE)
    old_key = await _make_key(client, alice_t)

    # 旧 Key 绑定 Agent1 成功
    first = await _bind(client, old_key, "install-win-003")
    assert first["machine_psk"]

    # 重置 Key
    regen = await client.post(
        "/api/me/agent-key/regenerate", headers={"Authorization": f"Bearer {alice_t}"}
    )
    new_key = regen.json()["key"]

    # 旧 Key 不能再新增绑定（新 Agent）→ 401
    old_fail = await client.post("/api/agent/bind", json={"user_key": old_key, "install_id": "install-win-004"})
    assert old_fail.status_code == 401

    # 新 Key 可绑定新 Agent
    ok = await _bind(client, new_key, "install-win-004")
    assert ok["machine_psk"]

    # 已有绑定（Agent1）继续有效：机器 PSK 仍可查询
    listed = await client.get(
        "/api/agent/bindings?agent_id=install-win-003",
        headers={"X-Agent-Key": first["machine_psk"]},
    )
    assert listed.status_code == 200
    assert any(i["username"] == ALICE["username"] for i in listed.json())


# ---------- 限流 ----------


async def test_bind_rate_limit_by_public_id(client: AsyncClient):
    token = await _register(client, ALICE)
    key = await _make_key(client, token)
    install = "install-win-005"
    # 同一 Key 的 public_id 每分钟限 5 次：前 5 次进入校验（secret 错误 → 401），第 6 次 429
    public_id = key.split("_")[1]
    for _ in range(5):
        resp = await client.post(
            "/api/agent/bind",
            json={"user_key": f"uak_{public_id}_wrongsecret", "install_id": install},
        )
        assert resp.status_code == 401
    resp6 = await client.post(
        "/api/agent/bind",
        json={"user_key": f"uak_{public_id}_wrongsecret", "install_id": install},
    )
    assert resp6.status_code == 429
