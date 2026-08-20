import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import SessionLocal
from app.main import app
from app.models import Agent, Device, Execution

REG = {"username": "pytest_agent_user", "email": "ag@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _token(client: AsyncClient) -> str:
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    return login.json()["access_token"]


async def _create_agent_device() -> tuple[int, int]:
    async with SessionLocal() as db:
        agent = Agent(agent_key="sk-t", agent_id="pytest_agent_mgmt", status="online")
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
    assert any(a["agent_id"] == body["agent_id"] for a in listed.json())


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
