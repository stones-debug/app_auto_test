import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import reports_dir
from app.core.database import SessionLocal
from app.main import app
from app.models import (
    Agent,
    Device,
    Execution,
    ExecutionCase,
    ExecutionLog,
    ExecutionStep,
)
from app.services import worker_service
from app.ws import handlers
from app.ws.managers import agent_manager, execution_manager

REG = {"username": "pytest_ws_user", "email": "ws@tl-tek.com", "password": "test123"}


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_agent(agent_key: str = "sk-ws-test") -> int:
    async with SessionLocal() as db:
        agent = Agent(
            agent_key=agent_key,
            agent_id=f"pytest_ws_agent_{uuid.uuid4().hex[:6]}",
            hostname="ws-host",
            status="offline",
        )
        db.add(agent)
        await db.commit()
        return agent.id


async def _setup_case_execution(client: AsyncClient) -> tuple[str, int, int]:
    """返回 (token, case_id, execution_id)。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post(
        "/api/projects", headers=headers, json={"name": "WS测试项目", "visibility": "private"}
    )
    project_id = project.json()["id"]
    element = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "按钮", "locator_type": "id", "locator_value": "btn"},
    )
    element_id = element.json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        headers=headers,
        json={
            "name": "WS用例",
            "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}],
            "assertions": [],
        },
    )
    case_id = case.json()["id"]
    execution = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"parameters": {}},
    )
    return token, case_id, execution.json()["id"]


# ---------- managers ----------


async def test_agent_manager_send_and_online():
    fake = FakeWebSocket()
    await agent_manager.connect(1, fake)
    assert agent_manager.is_online(1)
    ok = await agent_manager.send(1, {"type": "hello"})
    assert ok
    assert fake.sent == [{"type": "hello"}]
    assert not await agent_manager.send(999, {"type": "x"})
    await agent_manager.disconnect(1)
    assert not agent_manager.is_online(1)


async def test_execution_manager_broadcast():
    f1, f2 = FakeWebSocket(), FakeWebSocket()
    await execution_manager.connect(10, f1)
    await execution_manager.connect(10, f2)
    await execution_manager.broadcast(10, {"type": "status", "status": "running"})
    assert len(f1.sent) == 1 and len(f2.sent) == 1
    await execution_manager.broadcast(999, {"type": "x"})
    await execution_manager.disconnect(10, f1)
    await execution_manager.disconnect(10, f2)


# ---------- handlers ----------


async def test_handle_register_heartbeat_device_list():
    agent_id = await _create_agent("sk-ws-test")
    fake = FakeWebSocket()

    async with SessionLocal() as db:
        reply = await handlers.handle_register(
            db, fake, {"type": "register", "agent_id": (await db.get(Agent, agent_id)).agent_id, "agent_key": "sk-ws-test"}
        )
        assert reply is not None
        assert reply["status"] == "ok"
        agent = await db.get(Agent, agent_id)
        assert agent.status == "online"
        assert agent.last_heartbeat is not None

        await handlers.handle_heartbeat(db, agent_id, {"type": "heartbeat"})
        await handlers.handle_device_list(
            db,
            agent_id,
            {"type": "device_list", "devices": [{"udid": "u-1", "name": "d1", "platform": "android"}]},
        )
        device = (await db.execute(select(Device).where(Device.agent_id == agent_id))).scalar_one()
        assert device.udid == "u-1"
        assert device.status == "idle"

    await agent_manager.disconnect(agent_id)


async def test_handle_register_wrong_key_closes():
    agent_id = await _create_agent("sk-ws-test")
    fake = FakeWebSocket()
    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        reply = await handlers.handle_register(
            db, fake, {"type": "register", "agent_id": agent.agent_id, "agent_key": "wrong-key"}
        )
        assert reply is None
        assert fake.closed is not None and fake.closed[0] == 1008


async def test_handle_log_step_result_execution_result(client: AsyncClient):
    token, case_id, execution_id = await _setup_case_execution(client)

    # 造 execution_cases 快照（复用 worker 快照逻辑）
    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        execution.status = "running"
        execution.started_at = datetime.now(UTC)
        await db.commit()

    # 前端订阅，捕获广播
    front = FakeWebSocket()
    await execution_manager.connect(execution_id, front)

    async with SessionLocal() as db:
        agent_id = await _create_agent()
        await handlers.handle_log(db, agent_id, {"execution_id": execution_id, "level": "INFO", "message": "开始点击", "step_order": 1})
        log = (await db.execute(select(ExecutionLog).where(ExecutionLog.execution_id == execution_id))).scalar_one()
        assert log.message == "开始点击"
        assert log.source == "agent"

        await handlers.handle_step_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "case_id": case_id,
                "step_order": 1,
                "action": "click",
                "status": "passed",
                "duration": 120,
                "actual_value": "OK",
            },
        )
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        assert ec.status == "running"
        step = (await db.execute(
            select(ExecutionStep).where(ExecutionStep.execution_case_id == ec.id)
        )).scalar_one()
        assert step.status == "passed"
        assert step.actual_value == "OK"

        await handlers.handle_execution_result(db, agent_id, {"execution_id": execution_id, "status": "PASSED"})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "passed"
        assert execution.finished_at is not None

    types = {m["type"] for m in front.sent}
    assert {"log", "step_result", "completed"} <= types
    await execution_manager.disconnect(execution_id, front)


# ---------- 内部转发 ----------


async def test_internal_forward_offline_409(client: AsyncClient):
    agent_id = await _create_agent("sk-ws-test")
    resp = await client.post(
        f"/internal/ws/agents/{agent_id}/send",
        json={"type": "start_test", "execution_id": 1},
        headers={"X-Internal-Token": "dev-internal-token-change-me"},
    )
    assert resp.status_code == 409


async def test_internal_forward_online_200(client: AsyncClient):
    agent_id = await _create_agent("sk-ws-test")
    fake = FakeWebSocket()
    await agent_manager.connect(agent_id, fake)
    resp = await client.post(
        f"/internal/ws/agents/{agent_id}/send",
        json={"type": "start_test", "execution_id": 1, "device": {"udid": "u1"}},
        headers={"X-Internal-Token": "dev-internal-token-change-me"},
    )
    assert resp.status_code == 200
    assert fake.sent[0]["type"] == "start_test"
    assert fake.sent[0]["device"] == {"udid": "u1"}
    await agent_manager.disconnect(agent_id)


async def test_internal_forward_bad_token(client: AsyncClient):
    agent_id = await _create_agent("sk-ws-test")
    resp = await client.post(
        f"/internal/ws/agents/{agent_id}/send",
        json={"type": "start_test"},
        headers={"X-Internal-Token": "wrong"},
    )
    assert resp.status_code == 401


# ---------- 文件上传 ----------


async def test_agent_upload_ok_and_cleanup():
    agent_id = await _create_agent("sk-ws-test")
    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        key = agent.agent_key
    execution_id = 987654
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/upload",
            headers={"X-Agent-Key": key},
            data={"execution_id": str(execution_id), "file_type": "screenshot"},
            files={"file": ("step_001.png", b"\x89PNG\r\n\x1a\n fake-png", "image/png")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == f"execution_{execution_id}/screenshots/step_001.png"

        bad_key = await client.post(
            "/api/agent/upload",
            headers={"X-Agent-Key": "wrong"},
            data={"execution_id": str(execution_id)},
            files={"file": ("a.png", b"x", "image/png")},
        )
        assert bad_key.status_code == 401

        bad_ext = await client.post(
            "/api/agent/upload",
            headers={"X-Agent-Key": key},
            data={"execution_id": str(execution_id)},
            files={"file": ("a.exe", b"x", "application/octet-stream")},
        )
        assert bad_ext.status_code == 400

    # 清理落盘文件
    target = reports_dir() / f"execution_{execution_id}"
    if target.exists():
        import shutil

        shutil.rmtree(target)
