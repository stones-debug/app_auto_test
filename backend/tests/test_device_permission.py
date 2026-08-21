"""Windows 方案 §3.3/§4.2：设备权限过滤、默认设备与快照锁保护测试。"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.core.database import SessionLocal
from app.main import app
from app.models import Agent, Device, Execution, Project, User
from app.ws import handlers
from tests.helpers import create_bound_agent_device

ALICE = {"username": "pytest_perm_alice", "email": "p-a@tl-tek.com", "password": "test123"}
BOB = {"username": "pytest_perm_bob", "email": "p-b@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client: AsyncClient, reg: dict) -> str:
    await client.post("/api/auth/register", json=reg)
    login = await client.post("/api/auth/login", json={"username": reg["username"], "password": reg["password"]})
    return login.json()["access_token"]


async def _make_project(client: AsyncClient, token: str) -> int:
    resp = await client.post(
        "/api/projects", headers={"Authorization": f"Bearer {token}"},
        json={"name": "设备权限项目", "visibility": "private"},
    )
    return resp.json()["id"]


async def test_device_and_agent_visibility_scoped(client: AsyncClient):
    alice_t = await _register(client, ALICE)
    bob_t = await _register(client, BOB)
    _a1, d1 = await create_bound_agent_device(ALICE["username"])
    _a2, d2 = await create_bound_agent_device(ALICE["username"])
    b2, b_device = await create_bound_agent_device(BOB["username"])

    bob_headers = {"Authorization": f"Bearer {bob_t}"}
    # Bob 只看得到自己的 Agent 与设备
    agents = (await client.get("/api/agents", headers=bob_headers)).json()
    assert [a["id"] for a in agents] == [b2]
    devices = (await client.get("/api/devices", headers=bob_headers)).json()
    assert [d["id"] for d in devices["items"]] == [b_device]

    # Bob 访问 Alice 的设备 → 403；不存在 → 404
    denied = await client.get(f"/api/devices/{d2}", headers=bob_headers)
    assert denied.status_code == 403
    missing = await client.get("/api/devices/999999", headers=bob_headers)
    assert missing.status_code == 404

    # Alice 的设备列表含两台
    alice_devices = (await client.get(
        "/api/devices", headers={"Authorization": f"Bearer {alice_t}"}
    )).json()
    assert {d["id"] for d in alice_devices["items"]} == {d1, d2}

    # 管理员可见全部
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == ALICE["username"]))).scalar_one()
        user.is_admin = True
        await db.commit()
    admin_devices = (await client.get(
        "/api/devices", headers={"Authorization": f"Bearer {alice_t}"}
    )).json()
    ids = {d["id"] for d in admin_devices["items"]}
    assert {d1, d2, b_device} <= ids


async def test_default_device_flow(client: AsyncClient):
    alice_t = await _register(client, ALICE)
    bob_t = await _register(client, BOB)
    _a1, d1 = await create_bound_agent_device(ALICE["username"])
    _a2, d2 = await create_bound_agent_device(ALICE["username"])
    _b1, b_d = await create_bound_agent_device(BOB["username"])
    alice_h = {"Authorization": f"Bearer {alice_t}"}

    # 未设置 → 空状态
    empty = await client.get("/api/devices/default", headers=alice_h)
    assert empty.status_code == 200
    assert empty.json()["device_id"] is None
    assert not empty.json()["available"]

    # 设置默认设备 → 可用（Agent 在线 + idle）
    set_ok = await client.put("/api/devices/default", headers=alice_h, json={"device_id": d1})
    assert set_ok.status_code == 200
    assert set_ok.json()["device_id"] == d1

    got = await client.get("/api/devices/default", headers=alice_h)
    assert got.json()["available"] is True
    assert got.json()["device"]["id"] == d1

    # 设备变忙 → available False
    async with SessionLocal() as db:
        await db.execute(
            update(Device).where(Device.id == d1).values(status="busy")
        )
        await db.commit()
    busy = await client.get("/api/devices/default", headers=alice_h)
    assert busy.json()["available"] is False
    assert "忙" in busy.json()["reason"]

    # Agent 离线 → available False
    async with SessionLocal() as db:
        await db.execute(
            update(Device).where(Device.id == d1).values(status="idle")
        )
        await db.execute(update(Agent).where(Agent.id == _a1).values(status="offline"))
        await db.commit()
    offline = await client.get("/api/devices/default", headers=alice_h)
    assert offline.json()["available"] is False
    assert "离线" in offline.json()["reason"]

    # 越权设置：Bob 不能设置 Alice 的设备
    denied = await client.put(
        "/api/devices/default", headers={"Authorization": f"Bearer {bob_t}"}, json={"device_id": d2}
    )
    assert denied.status_code == 403

    # 清除默认设备
    cleared = await client.put("/api/devices/default", headers=alice_h, json={"device_id": None})
    assert cleared.status_code == 200
    assert cleared.json()["device_id"] is None
    assert (await client.get("/api/devices/default", headers=alice_h)).json()["device_id"] is None


async def test_device_snapshot_keeps_busy_lock(client: AsyncClient):
    """Windows 方案 §3.3：被执行锁定的设备不允许普通快照覆盖锁状态。"""
    await _register(client, ALICE)
    agent_id, device_id = await create_bound_agent_device(ALICE["username"])

    async with SessionLocal() as db:
        # 建真实执行用于设备锁（外键约束）
        user = (await db.execute(select(User).where(User.username == ALICE["username"]))).scalar_one()
        project = Project(name="快照锁测试项目", visibility="private", owner_id=user.id)
        db.add(project)
        await db.flush()
        execution = Execution(project_id=project.id, type="case", status="running")
        db.add(execution)
        await db.flush()
        device = await db.get(Device, device_id)
        device.status = "busy"
        device.locked_by_execution = execution.id
        await db.commit()
        udid = device.udid
        locked_by = execution.id

    # 普通快照上报 idle → busy 锁状态必须保持
    async with SessionLocal() as db:
        await handlers.handle_device_list(
            db,
            agent_id,
            {"devices": [{"udid": udid, "name": "覆盖尝试", "status": "idle"}]},
        )
        device = await db.get(Device, device_id)
        assert device.status == "busy"
        assert device.locked_by_execution == locked_by

    # 解锁后快照可正常更新，且 connection_type/address 落库
    async with SessionLocal() as db:
        await db.execute(
            update(Device).where(Device.id == device_id)
            .values(status="idle", locked_by_execution=None)
        )
        await db.commit()
    async with SessionLocal() as db:
        await handlers.handle_device_list(
            db,
            agent_id,
            {"devices": [{
                "udid": udid, "name": "wifi-phone",
                "status": "idle", "connection_type": "wifi", "address": "192.168.1.10:5555",
            }]},
        )
        device = await db.get(Device, device_id)
        assert device.status == "idle"
        assert device.connection_type == "wifi"
        assert device.address == "192.168.1.10:5555"
        # name 仅在设备创建时写入（快照只更新状态/连接信息）
        assert device.name == "pytest设备"
