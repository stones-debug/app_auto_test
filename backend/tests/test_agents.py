import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_psk
from app.main import app
from app.models import Agent, Device, Execution, User

REG = {"username": "pytest_agent_user", "email": "ag@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _token(client: AsyncClient) -> str:
    await client.post("/api/auth/register", json=REG)
    # 设备/Agent 管理为平台级资源，测试用户提升为平台管理员
    async with SessionLocal() as db:
        user = (await db.execute(
            select(User).where(User.username == REG["username"])
        )).scalar_one()
        user.is_admin = True
        await db.commit()
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    return login.json()["access_token"]


async def _create_agent_device() -> tuple[int, int]:
    async with SessionLocal() as db:
        agent = Agent(agent_key=hash_psk("sk-t"), agent_id="pytest_agent_mgmt", status="online")
        db.add(agent)
        await db.flush()
        device = Device(agent_id=agent.id, name="测试设备", platform="android", udid="u-mgmt", status="idle")
        db.add(device)
        await db.commit()
        return agent.id, device.id


async def test_create_and_list_agents(client: AsyncClient):
    token = await _token(client)
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post("/api/agents", headers=headers, json={"hostname": "pc-1", "platform": "windows"})
    assert created.status_code == 201
    body = created.json()
    assert body["agent_id"].startswith("agent-")
    assert body["agent_key"].startswith("sk-")
    assert body["status"] == "offline"

    listed = await client.get("/api/agents", headers=headers)
    assert listed.status_code == 200
    items = listed.json()
    assert any(a["agent_id"] == body["agent_id"] for a in items)
    # CR-03：列表不得暴露 Agent PSK
    for item in items:
        assert "agent_key" not in item


async def test_agent_device_mgmt_requires_platform_admin(client: AsyncClient):
    """CR-03：非平台管理员不能管理 Agent/设备或读取 PSK；列表仅返回其绑定 Agent（此处为空）。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Windows 方案 §3.3：普通用户可查列表，但只看到自己绑定的 Agent（未绑定 → 空）
    listed = await client.get("/api/agents", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == []
    assert (await client.post("/api/agents", headers=headers, json={"hostname": "x"})).status_code == 403

    agent_id, device_id = await _create_agent_device()
    assert (await client.delete(f"/api/agents/{agent_id}", headers=headers)).status_code == 403
    assert (await client.post(f"/api/devices/{device_id}/release", headers=headers)).status_code == 403


async def test_device_list_and_release(client: AsyncClient):
    token = await _token(client)
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post(
        "/api/projects", headers=headers, json={"name": "Agent管理测试项目", "visibility": "private"}
    )
    project_id = project.json()["id"]
    agent_id, device_id = await _create_agent_device()

    # 建执行并锁定设备
    async with SessionLocal() as db:
        execution = Execution(project_id=project_id, type="case", status="running")
        db.add(execution)
        await db.flush()
        device = await db.get(Device, device_id)
        device.status = "busy"
        device.locked_by_execution = execution.id
        await db.commit()

    listed = await client.get("/api/devices", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1
    item = next(d for d in listed.json()["items"] if d["id"] == device_id)
    assert item["status"] == "busy"
    assert item["agent_name"] == "pytest_agent_mgmt"

    released = await client.post(f"/api/devices/{device_id}/release", headers=headers)
    assert released.status_code == 200
    assert released.json()["status"] == "idle"
    assert released.json()["locked_by_execution"] is None

    async with SessionLocal() as db:
        device = await db.get(Device, device_id)
        assert device.status == "idle"
        assert device.locked_by_execution is None

    agents = await client.get(f"/api/agents/{agent_id}/devices", headers=headers)
    assert agents.status_code == 200
    assert len(agents.json()) == 1

    deleted = await client.delete(f"/api/agents/{agent_id}", headers=headers)
    assert deleted.status_code == 204


async def test_device_not_found(client: AsyncClient):
    token = await _token(client)
    resp = await client.get("/api/devices/999999", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


async def test_device_list_status_filter(client: AsyncClient):
    """CR-15：设备列表 status 过滤生效（前端发送 status）。"""
    token = await _token(client)
    headers = {"Authorization": f"Bearer {token}"}
    _agent_id, device_id = await _create_agent_device()  # idle 设备

    idle = await client.get("/api/devices?status=idle", headers=headers)
    assert idle.status_code == 200
    assert any(d["id"] == device_id for d in idle.json()["items"])

    busy = await client.get("/api/devices?status=busy", headers=headers)
    assert busy.status_code == 200
    assert all(d["status"] == "busy" for d in busy.json()["items"])
