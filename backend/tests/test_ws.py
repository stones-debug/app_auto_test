import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fastapi import WebSocket, WebSocketDisconnect
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import reports_dir, settings
from app.core.database import SessionLocal
from app.core.security import hash_psk
from app.main import app
from app.models import (
    Agent,
    Device,
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionLog,
    ExecutionNode,
    ExecutionStep,
    ExecutionSuite,
    Project,
    User,
)
from app.repositories.executions import parse_artifact_reference
from app.services import worker_service, ws_ingest_service
from app.ws import handlers
from app.ws.managers import (
    AgentConnectionManager,
    agent_manager,
    execution_manager,
    profile_config_manager,
)
from app.ws.routes import agent_ws, config_ws, execution_ws
from tests.helpers import create_bound_agent_device

REG = {"username": "pytest_ws_user", "email": "ws@tl-tek.com", "password": "test123"}


def test_parse_typed_artifact_reference():
    assert parse_artifact_reference("step:12") == ("step", 12)
    assert parse_artifact_reference("node:34") == ("node", 34)
    assert parse_artifact_reference("12") is None
    assert parse_artifact_reference("node:0") is None
    assert parse_artifact_reference("step:not-a-number") is None


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
            agent_key=hash_psk(agent_key),
            agent_id=f"pytest_ws_agent_{uuid.uuid4().hex[:6]}",
            hostname="ws-host",
            status="offline",
        )
        db.add(agent)
        await db.commit()
        return agent.id


async def _bind_execution_to_agent(
    db, execution_id: int, agent_id: int, *, status: str = "running", session_token: str = "sess-token"
) -> int:
    """为 execution 绑定一台属于 agent 的设备并置为 running，返回 device_id。"""
    device = Device(agent_id=agent_id, name="ws-device", platform="android", udid=f"u-{uuid.uuid4().hex[:8]}", status="busy")
    db.add(device)
    await db.flush()
    execution = await db.get(Execution, execution_id)
    execution.device_id = device.id
    execution.status = status
    execution.session_token = session_token
    await db.commit()
    return device.id


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
        # 断言下沉到步骤内（StepCreate.assertions），用例级 assertions 字段已不存在
        json={
            "name": "WS用例",
            "steps": [
                {
                    "order": 1,
                    "action": "click",
                    "element_id": element_id,
                    "params": {},
                    "assertions": [
                        {
                            "order": 1,
                            "type": "text_equals",
                            "element_id": element_id,
                            "params": {"expected": "admin"},
                        }
                    ],
                }
            ],
        },
    )
    case_id = case.json()["id"]
    # Windows 方案 §3.3：执行创建必须指定已授权设备
    _agent_id, device_id = await create_bound_agent_device(REG["username"])
    execution = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": device_id, "parameters": {}},
    )
    assert execution.status_code == 201, execution.text
    return token, case_id, execution.json()["id"]


async def _snapshot_ids(
    db, execution_id: int
) -> tuple[ExecutionCase, list[ExecutionStep], list[ExecutionAssertion]]:
    execution_case = await db.scalar(
        select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
    )
    steps = list(
        (
            await db.execute(
                select(ExecutionStep)
                .where(ExecutionStep.execution_case_id == execution_case.id)
                .order_by(ExecutionStep.step_order)
            )
        ).scalars()
    )
    # 断言下沉到步骤：经 ExecutionStep 关联回用例
    assertions = list(
        (
            await db.execute(
                select(ExecutionAssertion)
                .join(ExecutionStep, ExecutionAssertion.execution_step_id == ExecutionStep.id)
                .where(ExecutionStep.execution_case_id == execution_case.id)
                .order_by(ExecutionAssertion.assertion_order)
            )
        ).scalars()
    )
    return execution_case, steps, assertions


async def test_node_started_updates_case_and_suite_runtime_state(client: AsyncClient):
    _token, _case_id, execution_id = await _setup_case_execution(client)

    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        execution_case = await db.scalar(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )
        node = await db.scalar(
            select(ExecutionNode).where(ExecutionNode.execution_case_id == execution_case.id)
        )
        await _bind_execution_to_agent(db, execution_id, agent_id)
        assert execution_case.status == "pending"
        suite = await db.get(ExecutionSuite, execution_case.execution_suite_id)

        message = await handlers.handle_node_started(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "execution_suite_id": suite.id,
                "execution_node_id": node.id,
                "kind": node.kind,
            },
        )
        assert message["type"] == "node_started"
        assert node.status == "running"
        assert execution_case.status == "running"
        assert suite.status == "running"
        assert execution_case.started_at is not None
        assert suite.started_at is not None


async def test_node_result_keeps_error_over_later_failed_status(client: AsyncClient):
    _token, _case_id, execution_id = await _setup_case_execution(client)

    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        execution_case = await db.scalar(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )
        nodes = list(
            (
                await db.execute(
                    select(ExecutionNode)
                    .where(ExecutionNode.execution_case_id == execution_case.id)
                    .order_by(ExecutionNode.node_order)
                )
            ).scalars()
        )
        assert len(nodes) >= 2
        await _bind_execution_to_agent(db, execution_id, agent_id)

        base = {
            "execution_id": execution_id,
            "session_token": "sess-token",
            "execution_case_id": execution_case.id,
        }
        await handlers.handle_node_result(
            db,
            agent_id,
            {**base, "execution_node_id": nodes[0].id, "status": "error", "error_message": "会话异常"},
        )
        assert execution_case.status == "error"

        await handlers.handle_node_result(
            db,
            agent_id,
            {**base, "execution_node_id": nodes[1].id, "status": "failed"},
        )
        assert execution_case.status == "error"


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


async def test_agent_devices_includes_agent_name(client: AsyncClient):
    """Agent 上报的设备通过 GET /agents/{id}/devices 获取时应填充所属 Agent 标识。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    agent_id, device_id = await create_bound_agent_device(REG["username"])

    resp = await client.get(f"/api/agents/{agent_id}/devices", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    item = resp.json()[0]
    assert item["id"] == device_id
    assert item["agent_id"] == agent_id
    # 修复：原实现漏填充 agent_name，设备中心页面该列一直为 null
    assert item["agent_name"] is not None
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

    # 造 execution_cases 快照 + 绑定设备并置 running（CR-05）
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        execution_case, execution_steps, _assertions = await _snapshot_ids(db, execution_id)
        execution_step = execution_steps[0]
        execution = await db.get(Execution, execution_id)
        execution.started_at = datetime.now(UTC)
        await db.commit()

    # 前端订阅，捕获广播
    front = FakeWebSocket()
    await execution_manager.connect(execution_id, front)

    async with SessionLocal() as db:
        payload_log = {"execution_id": execution_id, "session_token": "sess-token", "level": "INFO", "message": "开始点击", "step_order": 1}
        await handlers.handle_log(db, agent_id, payload_log)
        log = (await db.execute(select(ExecutionLog).where(ExecutionLog.execution_id == execution_id))).scalar_one()
        assert log.message == "开始点击"
        assert log.source == "agent"

        await handlers.handle_step_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "execution_step_id": execution_step.id,
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
        assert step.parameters["wait_timeout"] == 10

        acknowledged = await handlers.handle_execution_result(
            db,
            agent_id,
            {"execution_id": execution_id, "session_token": "sess-token", "status": "PASSED"},
        )
        assert acknowledged is True
        # 服务端提交后若 ACK 丢失，Agent 会在重连后重放；同会话终态必须幂等确认。
        replay_acknowledged = await handlers.handle_execution_result(
            db,
            agent_id,
            {"execution_id": execution_id, "session_token": "sess-token", "status": "PASSED"},
        )
        assert replay_acknowledged is True

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "passed"
        assert execution.finished_at is not None

    types = {m["type"] for m in front.sent}
    assert {"log", "step_result", "completed"} <= types
    completed = next(m for m in front.sent if m["type"] == "completed")
    # B5：completed 携带 report_id（未生成报告时为 None）
    assert "report_id" in completed
    assert completed["report_id"] is None
    # B5：step_result 带 artifact_id（有截图时为 step.id）
    step_msg = next(m for m in front.sent if m["type"] == "step_result")
    assert "artifact_id" in step_msg
    assert step_msg["case_status"] == "running"
    assert step_msg["actual_value"] == "OK"
    assert step_msg["error_message"] is None
    async with SessionLocal() as db:
        settled_case = (
            await db.execute(
                select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
            )
        ).scalar_one()
        assert settled_case.status == "passed"
    await execution_manager.disconnect(execution_id, front)


async def test_step_result_rejects_unsafe_screenshot_path(client: AsyncClient):
    """CR-01：Agent 回传的恶意 screenshot_path 不得入库。"""
    token, case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        execution_case, execution_steps, _assertions = await _snapshot_ids(db, execution_id)
        execution_step = execution_steps[0]

    async with SessionLocal() as db:
        for bad in [
            r"C:\Windows\win.ini",
            r"\\server\share\x",
            "../x.png",
            "execution_999/screenshots/a.png",
            f"execution_{execution_id}/../../etc/passwd",
            f"execution_{execution_id}/screenshots/..\\..\\win.ini",
        ]:
            await handlers.handle_step_result(
                db,
                agent_id,
                {
                    "execution_id": execution_id,
                    "session_token": "sess-token",
                    "execution_case_id": execution_case.id,
                    "execution_step_id": execution_step.id,
                    "step_order": 1,
                    "action": "screenshot",
                    "status": "passed",
                    "screenshot_path": bad,
                },
            )
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        step = (await db.execute(
            select(ExecutionStep).where(ExecutionStep.execution_case_id == ec.id)
        )).scalar_one()
        assert step.screenshot_path is None


async def test_step_result_replay_cannot_downgrade_or_wipe_evidence(client: AsyncClient):
    """重投的 step_result 既不能回退终态，也不能清空已固化的失败现场。

    Agent 的 ACK 丢失后会重连重放，重放消息通常不带截图/错误信息；
    旧实现用 `step.status = payload["status"]` 无条件赋值，会把已落库的
    failed 翻成 passed，并把截图、错误信息、实际值一并清成 None ——
    等于抹掉「执行失败自动截图」的证据（Review C-1）。
    """
    token, case_id, execution_id = await _setup_case_execution(client)

    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        execution_case, execution_steps, _assertions = await _snapshot_ids(db, execution_id)
        execution_step = execution_steps[0]
        execution = await db.get(Execution, execution_id)
        execution.started_at = datetime.now(UTC)
        await db.commit()

    screenshot_key = f"execution_{execution_id}/screenshots/failure.png"
    first_report = {
        "execution_id": execution_id,
        "session_token": "sess-token",
        "execution_case_id": execution_case.id,
        "execution_step_id": execution_step.id,
        "step_order": 1,
        "action": "click",
        "status": "failed",
        "duration": 321,
        "actual_value": "按钮不可见",
        "error_message": "元素定位超时",
        "screenshot_path": screenshot_key,
    }

    async with SessionLocal() as db:
        await handlers.handle_step_result(db, agent_id, first_report)
        step = await db.get(ExecutionStep, execution_step.id)
        assert step.status == "failed"
        assert step.screenshot_path == screenshot_key
        finished_at = step.finished_at
        assert finished_at is not None

        # 重放：状态「更优」且完全不带现场信息
        await handlers.handle_step_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "execution_step_id": execution_step.id,
                "step_order": 1,
                "action": "click",
                "status": "passed",
            },
        )
        step = await db.get(ExecutionStep, execution_step.id)
        assert step.status == "failed", "终态不得被重投消息回退"
        assert step.screenshot_path == screenshot_key, "失败截图不得被重投消息清空"
        assert step.error_message == "元素定位超时"
        assert step.actual_value == "按钮不可见"
        assert step.duration == 321
        assert step.finished_at == finished_at, "完成时间不得被重投消息改写"

        # 更严重的状态仍然接受（error 高于 failed）
        await handlers.handle_step_result(
            db,
            agent_id,
            {**first_report, "status": "error", "screenshot_path": None, "error_message": None},
        )
        step = await db.get(ExecutionStep, execution_step.id)
        assert step.status == "error"
        assert step.screenshot_path == screenshot_key
        assert step.error_message == "元素定位超时"
        await db.rollback()


async def test_node_result_replay_cannot_downgrade_or_wipe_evidence(client: AsyncClient):
    """节点级与步骤级同款保护：终态不回退、现场不被清空。"""
    token, case_id, execution_id = await _setup_case_execution(client)

    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        execution_case = (
            await db.execute(
                select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
            )
        ).scalar_one()
        nodes = list(
            (
                await db.execute(
                    select(ExecutionNode)
                    .where(ExecutionNode.execution_case_id == execution_case.id)
                    .order_by(ExecutionNode.node_order)
                )
            ).scalars()
        )
        assert nodes
        node = nodes[0]
        await _bind_execution_to_agent(db, execution_id, agent_id)

        base = {
            "execution_id": execution_id,
            "session_token": "sess-token",
            "execution_case_id": execution_case.id,
            "execution_node_id": node.id,
        }
        screenshot_key = f"execution_{execution_id}/screenshots/node-failure.png"
        await handlers.handle_node_result(
            db,
            agent_id,
            {
                **base,
                "status": "failed",
                "duration": 88,
                "actual_value": "未勾选",
                "error_message": "断言失败",
                "screenshot_path": screenshot_key,
            },
        )
        assert node.status == "failed"
        assert node.screenshot_path == screenshot_key

        # 重放：不带现场、状态更优
        await handlers.handle_node_result(db, agent_id, {**base, "status": "passed"})
        assert node.status == "failed"
        assert node.screenshot_path == screenshot_key
        assert node.error_message == "断言失败"
        assert node.actual_value == "未勾选"
        assert node.duration == 88

        # 无 status 的畸形上报保持 fail-closed（归为 error，比 failed 更严重），
        # 但同样不得回退为 passed/skipped 这类"更优"状态
        await handlers.handle_node_result(db, agent_id, dict(base))
        assert node.status == "error"
        assert node.screenshot_path == screenshot_key
        assert node.error_message == "断言失败"
        assert node.actual_value == "未勾选"
        await db.rollback()


async def test_cross_agent_cannot_submit_other_execution(client: AsyncClient):
    """CR-05：Agent A 不能给 Agent B 的执行提交日志/步骤/终态。"""
    token, case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_a = await _create_agent("sk-agent-a")
        await _bind_execution_to_agent(db, execution_id, agent_a)
        agent_b = await _create_agent("sk-agent-b")

    async with SessionLocal() as db:
        # Agent B 冒名提交 log/step/execution_result 均被拒绝
        await handlers.handle_log(db, agent_b, {"execution_id": execution_id, "session_token": "sess-token", "level": "INFO", "message": "冒名日志", "step_order": 1})
        await handlers.handle_step_result(
            db, agent_b,
            {"execution_id": execution_id, "session_token": "sess-token", "execution_step_id": 999999999, "step_order": 1, "action": "click", "status": "passed"},
        )
        await handlers.handle_execution_result(db, agent_b, {"execution_id": execution_id, "session_token": "sess-token", "status": "passed"})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "running"  # 未被冒名终态化
        assert (await db.execute(
            select(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
        )).scalars().all() == []  # 无冒名日志
        assert (await db.execute(
            select(ExecutionStep).where(
                ExecutionStep.execution_case_id.in_(
                    select(ExecutionCase.id).where(ExecutionCase.execution_id == execution_id)
                )
            )
        )).scalars().all() == []  # 无冒名步骤


# ---------- 内部转发 ----------


async def test_internal_forward_offline_409(client: AsyncClient):
    agent_id = await _create_agent("sk-ws-test")
    resp = await client.post(
        f"/internal/ws/agents/{agent_id}/send",
        json={"type": "start_test", "execution_id": 1},
        headers={"X-Internal-Token": settings.internal_token},
    )
    assert resp.status_code == 409


async def test_internal_forward_online_200(client: AsyncClient):
    agent_id = await _create_agent("sk-ws-test")
    fake = FakeWebSocket()
    await agent_manager.connect(agent_id, fake)
    resp = await client.post(
        f"/internal/ws/agents/{agent_id}/send",
        json={"type": "start_test", "execution_id": 1, "device": {"udid": "u1"}},
        headers={"X-Internal-Token": settings.internal_token},
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
        # 注册用户作为项目 owner
        user = User(username="pytest_upload_user", email="up@t.com", password_hash="x")
        db.add(user)
        await db.flush()
        agent = await db.get(Agent, agent_id)
        key = "sk-ws-test"
        agent_identifier = agent.agent_id
        device = Device(agent_id=agent_id, name="up-device", platform="android", udid=f"u-{uuid.uuid4().hex[:8]}", status="busy")
        db.add(device)
        await db.flush()
        project = Project(name="上传测试项目", owner_id=user.id, visibility="private")
        db.add(project)
        await db.flush()
        execution = Execution(
            project_id=project.id, type="case", status="running", session_token="up-token"
        )
        db.add(execution)
        await db.flush()
        execution.device_id = device.id
        await db.commit()
        execution_id = execution.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/upload",
            headers={"X-Agent-Key": key},
            data={"execution_id": str(execution_id), "agent_id": agent_identifier, "session_token": "up-token", "file_type": "screenshot"},
            files={"file": ("step_001.png", b"\x89PNG\r\n\x1a\n fake-png", "image/png")},
        )
        assert resp.status_code == 200
        body = resp.json()
        # 文件名应为服务端生成的 UUID 安全名（忽略用户原始文件名）
        assert body["path"].startswith(f"execution_{execution_id}/screenshots/")
        assert body["path"].endswith(".png")
        stored_name = body["path"].split("/")[-1]
        assert stored_name != "step_001.png"
        assert len(stored_name) == 32 + 4  # uuid4().hex(32) + '.png'

        # 校验上传落盘路径确实为安全路径
        import uuid as _uuid
        _uuid.UUID(stored_name[:-4])

        # 错误 PSK → 401
        bad_key = await client.post(
            "/api/agent/upload",
            headers={"X-Agent-Key": "wrong"},
            data={"execution_id": str(execution_id), "agent_id": agent_identifier, "session_token": "up-token"},
            files={"file": ("a.png", b"x", "image/png")},
        )
        assert bad_key.status_code == 401

        # 错误 session_token → 403（CR-05）
        bad_token = await client.post(
            "/api/agent/upload",
            headers={"X-Agent-Key": key},
            data={"execution_id": str(execution_id), "agent_id": agent_identifier, "session_token": "wrong"},
            files={"file": ("a.png", b"x", "image/png")},
        )
        assert bad_token.status_code == 403

        # 错误扩展名 → 400
        bad_ext = await client.post(
            "/api/agent/upload",
            headers={"X-Agent-Key": key},
            data={"execution_id": str(execution_id), "agent_id": agent_identifier, "session_token": "up-token"},
            files={"file": ("a.exe", b"x", "application/octet-stream")},
        )
        assert bad_ext.status_code == 400

    # 清理落盘文件
    target = reports_dir() / f"execution_{execution_id}"
    if target.exists():
        import shutil

        shutil.rmtree(target)


async def test_handle_assertion_result(client: AsyncClient):
    token, case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        execution_case, execution_steps, execution_assertions = await _snapshot_ids(
            db, execution_id
        )

    front = FakeWebSocket()
    await execution_manager.connect(execution_id, front)

    async with SessionLocal() as db:
        await handlers.handle_step_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "execution_step_id": execution_steps[0].id,
                "step_order": 1,
                "action": "input",
                "status": "passed",
            },
        )
        await handlers.handle_assertion_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                # _locate_step 只按 execution_step_id 定位
                "execution_step_id": execution_steps[0].id,
                "assertions": [
                    {
                        "execution_assertion_id": execution_assertions[0].id,
                        "type": "text_equals",
                        "expected": "admin",
                        "actual": "admin",
                        "status": "pass",
                    }
                ],
            },
        )

    async with SessionLocal() as db:
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        # 断言下沉到步骤：经 ExecutionStep 关联回用例
        rows = (await db.execute(
            select(ExecutionAssertion)
            .join(ExecutionStep, ExecutionAssertion.execution_step_id == ExecutionStep.id)
            .where(ExecutionStep.execution_case_id == ec.id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].assertion_type == "text_equals"
        assert rows[0].status == "pass"
        # edeb068 起断言通过不再结束用例（用例可有多个步骤/后续断言），终态由
        # 独立的 case_status 消息驱动，这里显式补一步以覆盖完整协议链路
        assert ec.status == "running"
        await handlers.handle_case_status(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": ec.id,
                "status": "passed",
            },
        )

    async with SessionLocal() as db:
        ec_after = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        assert ec_after.status == "passed"

    # B5：assertion_result 广播（V2 §8.2）
    assertion_msgs = [m for m in front.sent if m["type"] == "assertion_result"]
    assert len(assertion_msgs) == 1
    assert assertion_msgs[0]["case_id"] == case_id
    assert assertion_msgs[0]["assertions"][0]["status"] == "pass"
    assert assertion_msgs[0]["assertions"][0]["assertion_order"] == 1
    assert assertion_msgs[0]["assertions"][0]["execution_assertion_id"] == execution_assertions[0].id
    await execution_manager.disconnect(execution_id, front)


async def test_case_and_suite_status_broadcast_full_realtime_fields(client: AsyncClient):
    _token, _case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        execution_case, _steps, _assertions = await _snapshot_ids(db, execution_id)
        execution_suite_id = execution_case.execution_suite_id

    front = FakeWebSocket()
    await execution_manager.connect(execution_id, front)
    async with SessionLocal() as db:
        await handlers.handle_case_status(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "status": "failed",
                "error_message": "用例失败",
            },
        )
        await handlers.handle_suite_status(
            db,
            agent_id,
            {
                "session_token": "sess-token",
                "execution_suite_id": execution_suite_id,
                "status": "failed",
                "error_message": "套件失败",
            },
        )

    case_message = next(message for message in front.sent if message["type"] == "case_status")
    assert case_message["execution_case_id"] == execution_case.id
    assert case_message["status"] == "failed"
    assert case_message["error_message"] == "用例失败"
    assert case_message["duration"] is None
    suite_message = next(message for message in front.sent if message["type"] == "suite_status")
    assert suite_message["execution_suite_id"] == execution_suite_id
    assert suite_message["status"] == "failed"
    assert suite_message["error_message"] == "套件失败"
    assert suite_message["duration"] is None
    await execution_manager.disconnect(execution_id, front)


async def test_timeout_confirmation_forces_error_and_late_result_is_ignored(client: AsyncClient):
    """超时 stopping 允许在途结果，但 Agent 确认后只能以 error 汇总。"""
    _token, _case_id, execution_id = await _setup_case_execution(client)
    sent: list[dict] = []

    async def sender(agent_id: int, payload: dict) -> bool:
        sent.append({"agent_id": agent_id, **payload})
        return True

    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        execution = await db.get(Execution, execution_id)
        execution.started_at = datetime.now(UTC) - timedelta(minutes=10)
        execution.timeout_seconds = 60
        device = await db.get(Device, execution.device_id)
        device.locked_by_execution = execution_id
        await db.commit()

        await worker_service.timeout_scan(db, agent_sender=sender)
        await db.refresh(execution)
        assert execution.status == "stopping"
        execution_case, steps, _assertions = await _snapshot_ids(db, execution_id)
        step = steps[0]
        await handlers.handle_step_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "execution_step_id": step.id,
                "step_order": 1,
                "action": "click",
                "status": "passed",
            },
        )
        assert step.status == "passed"
        acknowledged = await handlers.handle_execution_result(
            db,
            agent_id,
            {"execution_id": execution_id, "session_token": "sess-token", "status": "stopped"},
        )
        assert acknowledged is True
        await db.commit()

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "error"
        assert execution.finalized_at is None
        await worker_service._mark_terminal(db, execution, "error", "执行超时（>60s）")

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "error"
        assert execution.finalized_at is not None
        before = (
            await db.execute(select(ExecutionStep).where(ExecutionStep.execution_case_id == execution_case.id))
        ).scalars().all()
        acknowledged = await handlers.handle_execution_result(
            db,
            agent_id,
            {"execution_id": execution_id, "session_token": "sess-token", "status": "passed"},
        )
        after = (
            await db.execute(select(ExecutionStep).where(ExecutionStep.execution_case_id == execution_case.id))
        ).scalars().all()
        assert acknowledged is True
        assert [item.status for item in after] == [item.status for item in before]

    assert sent == [{"agent_id": agent_id, "type": "stop_test", "execution_id": execution_id}]


async def test_user_stop_confirmation_remains_stopped(client: AsyncClient):
    """用户停止与 Agent 终态竞争时仍保持 stopped 语义。"""
    _token, _case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id, status="stopping")
        execution = await db.get(Execution, execution_id)
        execution.stop_requested_at = datetime.now(UTC)
        execution.termination_reason = "user_stop"
        await db.commit()
        acknowledged = await handlers.handle_execution_result(
            db,
            agent_id,
            {"execution_id": execution_id, "session_token": "sess-token", "status": "passed"},
        )
        assert acknowledged is True
        await db.commit()

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "stopped"


async def test_case_and_suite_skipped_status_is_terminal(client: AsyncClient):
    """跳过状态必须按分层终态落库，不能被当作 running。"""
    _token, _case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        execution_case, _steps, _assertions = await _snapshot_ids(db, execution_id)
        suite = await db.get(ExecutionSuite, execution_case.execution_suite_id)

        case_message = await handlers.handle_case_status(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "status": "skipped",
            },
        )
        suite_message = await handlers.handle_suite_status(
            db,
            agent_id,
            {
                "session_token": "sess-token",
                "execution_suite_id": suite.id,
                "status": "skipped",
            },
        )

        assert execution_case.status == "skipped"
        assert suite.status == "skipped"
        assert case_message["status"] == "skipped"
        assert suite_message["status"] == "skipped"


@pytest.mark.parametrize("failure_source", ["suite", "suite_step"])
async def test_execution_result_passed_cannot_override_suite_failure(
    client: AsyncClient, failure_source: str
):
    """Agent 的 passed 不能覆盖已落库的失败套件或失败套件步骤。"""
    _token, _case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        suite = (
            await db.execute(
                select(ExecutionSuite).where(ExecutionSuite.execution_id == execution_id)
            )
        ).scalar_one()
        if failure_source == "suite":
            suite.status = "failed"
        else:
            db.add(
                ExecutionStep(
                    execution_suite_id=suite.id,
                    phase="suite_setup",
                    step_order=1,
                    action="sleep",
                    status="failed",
                )
            )
        await db.commit()
        await handlers.handle_execution_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "status": "passed",
            },
        )

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "failed"
        warning = await db.scalar(
            select(ExecutionLog.message).where(
                ExecutionLog.execution_id == execution_id,
                ExecutionLog.level == "WARN",
            )
        )
        expected_source = "套件步骤" if failure_source == "suite_step" else "套件"
        assert isinstance(warning, str)
        assert expected_source in warning


async def test_assertion_failure_survives_teardown_and_overrides_agent_passed(
    client: AsyncClient,
):
    """回归：后置步骤成功不能覆盖断言失败，执行终态也不能被 Agent passed 覆盖。"""
    _token, _case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        execution_case, execution_steps, execution_assertions = await _snapshot_ids(
            db, execution_id
        )
        teardown_step = ExecutionStep(
            execution_case_id=execution_case.id,
            phase="case_teardown",
            step_order=2,
            action="clear",
            status="pending",
        )
        db.add(teardown_step)
        await db.commit()

    async with SessionLocal() as db:
        await handlers.handle_step_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "execution_step_id": execution_steps[0].id,
                "step_order": 1,
                "action": "input",
                "status": "passed",
            },
        )
        await handlers.handle_assertion_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                # _locate_step 只按 execution_step_id 定位
                "execution_step_id": execution_steps[0].id,
                "assertions": [
                    {
                        "execution_assertion_id": execution_assertions[0].id,
                        "type": "text_equals",
                        "expected": "wrong",
                        "actual": "admin",
                        "status": "failed",
                    }
                ],
            },
        )
        await handlers.handle_step_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "execution_step_id": teardown_step.id,
                "step_order": 2,
                "action": "clear",
                "status": "passed",
            },
        )
        await handlers.handle_execution_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "status": "passed",
            },
        )

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution_case = (
            await db.execute(
                select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
            )
        ).scalar_one()
        # 断言下沉到步骤：经 ExecutionStep 关联回用例
        assertion = (
            await db.execute(
                select(ExecutionAssertion)
                .join(ExecutionStep, ExecutionAssertion.execution_step_id == ExecutionStep.id)
                .where(ExecutionStep.execution_case_id == execution_case.id)
            )
        ).scalar_one()
        assert assertion.status == "fail"
        assert execution_case.status == "failed"
        assert execution.status == "failed"


# ---------- CR-16：Agent 重连身份 / CR-24：广播空组清理 ----------


class FailingWebSocket:
    """只用于广播分组（send_json 即失败）；不可用于 agent 单连接。"""

    async def send_json(self, data: dict) -> None:
        raise ConnectionError("socket 已断开")


async def test_agent_disconnect_identity_protects_new_connection():
    """CR-16：旧连接 disconnect 不得删除新连接映射。"""
    agent_id = 424242
    ws_old = FakeWebSocket()
    ws_new = FakeWebSocket()
    await agent_manager.connect(agent_id, ws_old)
    await agent_manager.connect(agent_id, ws_new)  # 新连接替换旧连接
    assert ws_old.closed is not None  # 旧连接被关闭

    # 旧连接 handler 退出时携带旧 ws 断开 → 不应删除新连接
    removed = await agent_manager.disconnect(agent_id, ws_old)
    assert removed is False
    assert agent_manager.is_online(agent_id)

    # 新连接断开 → 正常移除
    removed = await agent_manager.disconnect(agent_id, ws_new)
    assert removed is True
    assert not agent_manager.is_online(agent_id)
    await agent_manager.disconnect(agent_id, ws_old)  # 幂等


async def test_broadcast_cleans_empty_group():
    """CR-24：广播失败移除最后一个 socket 后清理空组。"""
    execution_id = 424243
    await execution_manager.connect(execution_id, FailingWebSocket())
    await execution_manager.broadcast(execution_id, {"type": "log"})
    assert execution_id not in execution_manager._groups


# ---------- CR-17：设备消失同步 ----------


async def test_device_list_marks_missing_devices_offline(client: AsyncClient):
    """CR-17：设备快照中消失的设备被标记 offline（未锁定）。"""
    agent_id = await _create_agent()
    async with SessionLocal() as db:
        db.add(Device(agent_id=agent_id, name="keep", platform="android", udid="u-keep", status="idle"))
        db.add(Device(agent_id=agent_id, name="gone", platform="android", udid="u-gone", status="idle"))
        await db.commit()

    async with SessionLocal() as db:
        await handlers.handle_device_list(db, agent_id, {"devices": [{"udid": "u-keep"}]})
    async with SessionLocal() as db:
        rows = {d.udid: d for d in (await db.execute(select(Device).where(Device.agent_id == agent_id))).scalars().all()}
        assert rows["u-keep"].status == "idle"
        assert rows["u-gone"].status == "offline"


async def test_device_list_keeps_locked_missing_device(client: AsyncClient):
    """CR-17：被锁定的消失设备不置 offline（避免破坏执行中状态）。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post("/api/auth/login", json={"username": REG["username"], "password": REG["password"]})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post("/api/projects", headers=headers, json={"name": "WS锁设备项目", "visibility": "private"})
    project_id = project.json()["id"]

    agent_id = await _create_agent()
    async with SessionLocal() as db:
        device = Device(agent_id=agent_id, name="locked", platform="android", udid="u-locked", status="busy")
        db.add(device)
        await db.flush()
        execution = Execution(project_id=project_id, type="case", status="running")
        db.add(execution)
        await db.flush()
        device.locked_by_execution = execution.id
        await db.commit()

    async with SessionLocal() as db:
        await handlers.handle_device_list(db, agent_id, {"devices": []})

    async with SessionLocal() as db:
        device = (await db.execute(select(Device).where(Device.agent_id == agent_id))).scalar_one()
        assert device.status == "busy"
        assert device.locked_by_execution is not None


# ---------- Step 5：Agent WS 连接级防护 ----------


class _FakeAgentWS:
    """满足 agent_ws 所需的最小 WebSocket 子集（accept / receive / send_json / close）。

    不走真实 ASGI：这三条限制都发生在任何数据库访问之前，直接驱动协程既能
    避开 wsproto 依赖，也让用例不依赖测试库的可用性。
    """

    def __init__(self, messages: list[dict] | None = None) -> None:
        self._messages = list(messages or [])
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        return None

    async def receive(self) -> dict:
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(3600)  # 无更多消息：挂起，交由注册超时兜底
        return {"type": "websocket.disconnect"}

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


async def test_agent_registration_binds_real_socket_and_allows_send(monkeypatch):
    """注册提交后必须把真实 socket 放入 manager，Worker 才能反向下发消息。"""
    ws = _FakeAgentWS([
        _text_frame(json.dumps({"type": "register", "agent_id": "agent-1", "agent_key": "key"})),
        _text_frame(json.dumps({"type": "heartbeat"})),
        {"type": "websocket.disconnect"},
    ])

    async def register(_websocket, _payload):
        await agent_manager.connect(17, ws)
        return {"type": "registered", "agent_id": 17, "status": "ok"}

    async def handle_message(_agent_id, _payload):
        assert await agent_manager.send(17, {"type": "start_test", "execution_id": 3})
        return None

    monkeypatch.setattr(ws_ingest_service, "register_agent", register)
    monkeypatch.setattr(ws_ingest_service, "handle_agent_message", handle_message)
    await agent_ws(cast(WebSocket, ws))

    assert ws.sent[0]["type"] == "registered"
    assert {item["type"] for item in ws.sent} == {"registered", "start_test"}
    assert not agent_manager.is_online(17)


async def test_agent_ws_rejects_duplicate_register(monkeypatch):
    """已注册连接再次 register 必须被拒：避免 manager 关闭并重新保存当前 socket。"""
    ws = _FakeAgentWS([
        _text_frame(json.dumps({"type": "register", "agent_id": "agent-1", "agent_key": "key"})),
        _text_frame(json.dumps({"type": "register", "agent_id": "agent-9", "agent_key": "other"})),
        _text_frame(json.dumps({"type": "heartbeat"})),
        {"type": "websocket.disconnect"},
    ])
    register_calls: list[str] = []

    async def register(_websocket, payload):
        register_calls.append(payload["agent_id"])
        await agent_manager.connect(19, ws)
        return {"type": "registered", "agent_id": 19, "status": "ok"}

    async def handle_message(_agent_id, _payload):
        await agent_manager.send(19, {"type": "start_test", "execution_id": 5})
        return None

    monkeypatch.setattr(ws_ingest_service, "register_agent", register)
    monkeypatch.setattr(ws_ingest_service, "handle_agent_message", handle_message)
    await agent_ws(cast(WebSocket, ws))

    assert register_calls == ["agent-1"]  # 第二次 register 不进入生产注册逻辑
    errors = [item for item in ws.sent if item["type"] == "error"]
    assert [item["code"] for item in errors] == ["PROTOCOL_ERROR"]
    # 拒绝重复注册不等于断开：连接仍可用于后续消息，且当前 socket 未被误关
    assert ws.closed is None
    assert any(item.get("type") == "start_test" for item in ws.sent)


async def test_agent_registration_binds_socket_through_real_service(monkeypatch):
    """不替换 register_agent：验证"数据库提交 → 真实 manager 绑定 → 断线置离线"完整链路。"""
    raw_key = "sk-ws-real-register"
    agent_id_str = f"pytest_ws_agent_{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as db:
        agent = Agent(
            agent_key=hash_psk(raw_key),
            agent_id=agent_id_str,
            hostname="ws-host",
            status="offline",
        )
        db.add(agent)
        await db.commit()
        db_agent_id = agent.id

    # 只观察心跳，不替换注册：连接存续期间的在线状态由此处采样
    real_heartbeat = ws_ingest_service.HANDLERS["heartbeat"]
    observed: dict[str, object] = {}

    async def observing_heartbeat(db, agent_id: int, payload: dict):
        stored = await db.get(Agent, agent_id)
        observed["manager_online"] = agent_manager.is_online(agent_id)
        observed["db_status"] = stored.status if stored is not None else None
        return await real_heartbeat(db, agent_id, payload)

    monkeypatch.setitem(ws_ingest_service.HANDLERS, "heartbeat", observing_heartbeat)

    ws = _FakeAgentWS([
        _text_frame(json.dumps({
            "type": "register",
            "agent_id": agent_id_str,
            "agent_key": raw_key,
            "hostname": "real-host",
        })),
        _text_frame(json.dumps({"type": "heartbeat"})),
        {"type": "websocket.disconnect"},
    ])
    await agent_ws(cast(WebSocket, ws))

    assert ws.sent[0] == {"type": "registered", "agent_id": db_agent_id, "status": "ok"}
    # 提交成功后真实 socket 必须已在 manager 中（Worker 才能反向下发）
    assert observed == {"manager_online": True, "db_status": "online"}
    assert not agent_manager.is_online(db_agent_id)
    async with SessionLocal() as db:
        stored = await db.get(Agent, db_agent_id)
        assert stored is not None
        assert stored.status == "offline"  # 连接断开后 finally 置离线
        assert stored.hostname == "real-host"


async def test_agent_manager_connect_is_idempotent_for_same_socket():
    """同一 socket 重复 connect 视为幂等：不得把自己关掉再保存已关闭的 socket。"""
    manager = AgentConnectionManager()
    ws = _FakeAgentWS([])
    await manager.connect(23, ws)
    await manager.connect(23, ws)
    assert ws.closed is None
    assert manager.is_online(23)


async def test_agent_manager_connect_replaces_mapping_before_closing_old():
    """重连抢占：先换映射再关旧连接，旧连接的断开清理不得摘掉新绑定。"""
    manager = AgentConnectionManager()
    old = _FakeAgentWS([])
    new = _FakeAgentWS([])
    await manager.connect(24, old)
    await manager.connect(24, new)

    assert old.closed == (4001, "新连接替换旧连接")
    assert new.closed is None
    # 旧连接 finally 触发的 disconnect 因映射已替换而不生效
    assert await manager.disconnect(24, old) is False
    assert manager.is_online(24)
    assert await manager.send(24, {"type": "start_test", "execution_id": 6}) is True
    assert new.sent == [{"type": "start_test", "execution_id": 6}]


async def test_agent_manager_reconnect_race_does_not_report_removed():
    """重连竞态：旧连接在 close() 内立刻执行断开清理时，不得判定"已移除"。

    判定为已移除会让路由 finally 把 Agent 置为 offline，而它此刻其实已通过
    新连接在线——即"映射/DB 状态与实际连接不一致"。
    """
    manager = AgentConnectionManager()

    class _ReconnectingWS(_FakeAgentWS):
        """close() 返回后旧连接协程立即执行 finally，同步调用 disconnect。"""

        def __init__(self) -> None:
            super().__init__()
            self.removed: bool | None = None

        async def close(self, code: int = 1000, reason: str = "") -> None:
            self.closed = (code, reason)
            self.removed = await manager.disconnect(26, self)

    old: _ReconnectingWS = _ReconnectingWS()
    new = _FakeAgentWS([])
    await manager.connect(26, old)
    await manager.connect(26, new)

    assert old.closed == (4001, "新连接替换旧连接")
    assert old.removed is False
    assert manager.is_online(26)
    assert await manager.send(26, {"type": "start_test"}) is True
    assert new.sent == [{"type": "start_test"}]


async def test_agent_ws_continues_after_one_message_failure(monkeypatch):
    """单条落库失败只返回错误包，后续消息仍在同一连接处理。"""
    ws = _FakeAgentWS([
        _text_frame(json.dumps({"type": "register", "agent_id": "agent-2", "agent_key": "key"})),
        _text_frame(json.dumps({"type": "heartbeat"})),
        _text_frame(json.dumps({"type": "heartbeat"})),
        {"type": "websocket.disconnect"},
    ])
    calls = 0

    async def register(_websocket, _payload):
        await agent_manager.connect(18, ws)
        return {"type": "registered", "agent_id": 18, "status": "ok"}

    async def handle_message(_agent_id, _payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient database error")
        await agent_manager.send(18, {"type": "start_test", "execution_id": 4})
        return None

    monkeypatch.setattr(ws_ingest_service, "register_agent", register)
    monkeypatch.setattr(ws_ingest_service, "handle_agent_message", handle_message)
    await agent_ws(cast(WebSocket, ws))

    assert calls == 2
    assert {item["code"] for item in ws.sent if item["type"] == "error"} == {"INGEST_ERROR"}
    assert any(item.get("type") == "start_test" for item in ws.sent)


async def test_agent_message_commit_failure_does_not_poison_next_message(monkeypatch):
    """当前短事务提交失败后，下一条消息必须使用新 Session 继续处理。"""
    sessions = []

    class FakeSession:
        def __init__(self, fail_commit: bool):
            self.fail_commit = fail_commit
            self.rolled_back = False

        async def __aenter__(self):
            sessions.append(self)
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            if self.fail_commit:
                raise RuntimeError("commit failed")

        async def rollback(self):
            self.rolled_back = True

    factory_sessions = iter([FakeSession(True), FakeSession(False)])
    monkeypatch.setattr(ws_ingest_service, "SessionLocal", lambda: next(factory_sessions))
    handled = []

    async def handler(_db, agent_id, payload):
        handled.append((agent_id, payload["type"]))
        return None

    monkeypatch.setitem(ws_ingest_service.HANDLERS, "heartbeat", handler)
    with pytest.raises(RuntimeError, match="commit failed"):
        await ws_ingest_service.handle_agent_message(17, {"type": "heartbeat"})
    await ws_ingest_service.handle_agent_message(17, {"type": "heartbeat"})

    assert sessions[0].rolled_back
    assert len(sessions) == 2
    assert handled == [(17, "heartbeat"), (17, "heartbeat")]


def _text_frame(text: str) -> dict:
    return {"type": "websocket.receive", "text": text}


def _binary_frame(payload: bytes) -> dict:
    return {"type": "websocket.receive", "bytes": payload}


@pytest.mark.parametrize(
    ("kind", "value"),
    [("ASCII", "a"), ("中文", "汉"), ("Emoji", "😀"), ("二进制", b"binary")],
)
async def test_agent_ws_frame_limit_uses_payload_bytes(monkeypatch, kind: str, value: str | bytes):
    """Step 8：文本按 UTF-8 字节、二进制按 payload 字节计算，刚好到上限放行。"""
    if isinstance(value, bytes):
        frame_payload = json.dumps(
            {"type": "ping", "payload": value.decode("ascii")}, separators=(",", ":")
        ).encode("utf-8")
        frame = _binary_frame(frame_payload)
    else:
        frame_text = json.dumps({"type": "ping", "payload": value}, ensure_ascii=False, separators=(",", ":"))
        frame = _text_frame(frame_text)
        frame_payload = frame_text.encode("utf-8")
    monkeypatch.setattr(settings, "agent_ws_max_frame_bytes", len(frame_payload))

    ws = _FakeAgentWS([frame, {"type": "websocket.disconnect"}])
    async with SessionLocal() as db:
        await agent_ws(cast(WebSocket, ws), db)
    assert ws.closed is None, kind


@pytest.mark.parametrize(
    ("kind", "value"),
    [("ASCII", "a"), ("中文", "汉"), ("Emoji", "😀"), ("二进制", b"binary")],
)
async def test_agent_ws_frame_limit_closes_oversized_payload(
    monkeypatch, kind: str, value: str | bytes
):
    """Step 8：超过实际字节上限的文本或二进制帧统一以 1009 关闭。"""
    if isinstance(value, bytes):
        frame_payload = json.dumps(
            {"type": "ping", "payload": value.decode("ascii")}, separators=(",", ":")
        ).encode("utf-8")
        frame = _binary_frame(frame_payload + b"x")
    else:
        frame_text = json.dumps({"type": "ping", "payload": value}, ensure_ascii=False, separators=(",", ":"))
        frame = _text_frame(frame_text + "x")
        frame_payload = frame_text.encode("utf-8")
    monkeypatch.setattr(settings, "agent_ws_max_frame_bytes", len(frame_payload))

    ws = _FakeAgentWS([frame])
    async with SessionLocal() as db:
        await agent_ws(cast(WebSocket, ws), db)
    assert ws.closed == (1009, "消息过大"), kind


async def test_agent_ws_rejects_oversized_frame():
    """Step 5：单帧超过上限 → 1009。先量尺寸再解析，超限帧不会被反序列化。"""
    ws = _FakeAgentWS([_text_frame("x" * (settings.agent_ws_max_frame_bytes + 1))])
    async with SessionLocal() as db:
        await agent_ws(cast(WebSocket, ws), db)
    assert ws.closed == (1009, "消息过大")


async def test_agent_ws_limits_pre_register_messages():
    """Step 5：注册前消息数超限 → 1008（未注册连接不得无限刷协议错误包）。"""
    limit = settings.agent_ws_max_pre_register_messages
    ws = _FakeAgentWS([_text_frame(json.dumps({"type": "ping"})) for _ in range(limit + 1)])
    async with SessionLocal() as db:
        await agent_ws(cast(WebSocket, ws), db)
    assert ws.closed == (1008, "注册前消息数超限")


async def test_agent_ws_closes_on_register_timeout(monkeypatch):
    """Step 5：期限内未完成注册 → 1008。期限对整条连接生效，周期性消息不能续命。"""
    monkeypatch.setattr(settings, "agent_ws_register_timeout_seconds", 0.05)
    ws = _FakeAgentWS([])
    async with SessionLocal() as db:
        await agent_ws(cast(WebSocket, ws), db)
    assert ws.closed == (1008, "注册超时")


# ---------- Step 7.2：统一终态合并与 running 回退防护 ----------


@pytest.mark.parametrize(
    ("stored_suite_status", "agent_status", "expected"),
    [
        ("error", "failed", "error"),
        ("error", "stopped", "error"),
        ("error", "passed", "error"),
        ("failed", "stopped", "failed"),
        ("failed", "passed", "failed"),
    ],
)
async def test_agent_terminal_merged_with_stored_by_priority(
    client: AsyncClient, stored_suite_status: str, agent_status: str, expected: str
):
    """Agent 上报 failed/stopped 也不能覆盖已落库的更高优先级终态。"""
    _token, _case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        suite = (
            await db.execute(
                select(ExecutionSuite).where(ExecutionSuite.execution_id == execution_id)
            )
        ).scalar_one()
        suite.status = stored_suite_status
        await db.commit()
        await handlers.handle_execution_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "status": agent_status,
            },
        )

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == expected


async def test_case_status_late_running_does_not_revert_terminal(client: AsyncClient):
    """终态用例不被迟到的 running 消息回退。"""
    _token, _case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        execution_case, _steps, _assertions = await _snapshot_ids(db, execution_id)
        execution_case.status = "failed"
        execution_case.finished_at = datetime.now(UTC)
        await db.commit()

        await handlers.handle_case_status(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_case_id": execution_case.id,
                "status": "running",
            },
        )

    async with SessionLocal() as db:
        execution_case = await db.get(ExecutionCase, execution_case.id)
        assert execution_case.status == "failed"


async def test_suite_status_late_running_does_not_revert_terminal(client: AsyncClient):
    """终态套件不被迟到的 running 消息回退。"""
    _token, _case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        suite = (
            await db.execute(
                select(ExecutionSuite).where(ExecutionSuite.execution_id == execution_id)
            )
        ).scalar_one()
        suite.status = "stopped"
        suite.finished_at = datetime.now(UTC)
        await db.commit()

        await handlers.handle_suite_status(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "sess-token",
                "execution_suite_id": suite.id,
                "status": "running",
            },
        )

    async with SessionLocal() as db:
        suite = await db.get(ExecutionSuite, suite.id)
        assert suite.status == "stopped"


# ---------- 前端 WS 路由（execution / config）----------
# 这两条路由是前端实时通道的鉴权边界，此前完全没有路由级测试：
# 一旦 token 类型判断或可见性判定回归，没有任何用例会失败。


class FakeFrontendWS:
    """execution_ws / config_ws 的最小替身。

    与 agent_ws 不同，这两条路由用 `query_params` 取 token；帧走 `receive()`，
    鉴权失败时先 close 再 return，所以 close 必须可调用。帧耗尽后抛
    WebSocketDisconnect，用来模拟前端断开。

    frames 里的 dict 会被序列化成 JSON 文本帧；str 原样作为文本帧发出，
    用于构造畸形帧。因为路由改走 `receive()` 以统一畸形帧兜底，这里也必须
    提供 `receive()` 而非 `receive_json()`。
    """

    def __init__(self, frames: list[dict | str] | None = None, token: str | None = None) -> None:
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self.accepted = False
        self.query_params: dict[str, str] = {} if token is None else {"token": token}
        self._frames: list[dict] = [
            {"type": "websocket.receive", "text": frame if isinstance(frame, str) else json.dumps(frame)}
            for frame in (frames or [])
        ]

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive(self) -> dict:
        if not self._frames:
            raise WebSocketDisconnect(1000)
        return self._frames.pop(0)


async def _project_id_of_execution(execution_id: int) -> int:
    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution is not None
        return execution.project_id


async def _register_and_login(client: AsyncClient, username: str) -> str:
    """注册并登录一个额外的 pytest_ 用户，返回 access_token（不建项目）。"""
    payload = {"username": username, "email": f"{username}@tl-tek.com", "password": "test123"}
    await client.post("/api/auth/register", json=payload)
    login = await client.post("/api/auth/login", json={"username": username, "password": "test123"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def test_execution_ws_rejects_invalid_token_before_accepting(client: AsyncClient):
    """鉴权边界：无 token / 空 token / 非法 token 一律 1008，且不得 accept 或进广播组。"""
    _token, _case_id, execution_id = await _setup_case_execution(client)

    for candidate in (None, "", "not-a-jwt"):
        ws = FakeFrontendWS(token=candidate)
        await execution_ws(cast(WebSocket, ws), execution_id)
        assert ws.accepted is False, f"token={candidate!r} 时不应 accept"
        assert ws.closed == (1008, "认证失败或无权访问该执行")
        assert ws.sent == []
        # 未授权连接绝不能进入广播组
        await execution_manager.broadcast(execution_id, {"type": "log"})
        assert ws.sent == []


async def test_execution_ws_denies_non_member_then_allows_public_project(client: AsyncClient):
    """私有项目：非成员被拒；改为 public 后放行（可见性分支必须有覆盖）。"""
    token_owner, _case_id, execution_id = await _setup_case_execution(client)
    project_id = await _project_id_of_execution(execution_id)
    token_other = await _register_and_login(client, "pytest_ws_other")

    denied = FakeFrontendWS(token=token_other)
    await execution_ws(cast(WebSocket, denied), execution_id)
    assert denied.accepted is False
    assert denied.closed == (1008, "认证失败或无权访问该执行")

    updated = await client.put(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {token_owner}"},
        json={"visibility": "public"},
    )
    assert updated.status_code == 200, updated.text

    allowed = FakeFrontendWS(frames=[{"type": "close"}], token=token_other)
    await execution_ws(cast(WebSocket, allowed), execution_id)
    assert allowed.accepted is True
    assert allowed.closed is None
    assert allowed.sent[0]["type"] == "status"
    assert allowed.sent[0]["execution_id"] == execution_id


async def test_execution_ws_happy_path_ping_pong_and_unregister(client: AsyncClient):
    """属主连接：首帧 status、ping→pong、收到 close 后必须注销出广播组。"""
    token, _case_id, execution_id = await _setup_case_execution(client)

    ws = FakeFrontendWS(frames=[{"type": "ping"}, {"type": "close"}], token=token)
    await execution_ws(cast(WebSocket, ws), execution_id)

    assert ws.accepted is True
    assert [frame["type"] for frame in ws.sent] == ["status", "pong"]
    # 路由返回即已断开：后续广播不得再触达这条连接
    await execution_manager.broadcast(execution_id, {"type": "log", "message": "after-close"})
    assert [frame["type"] for frame in ws.sent] == ["status", "pong"]


async def test_execution_ws_receives_broadcast_while_connected(client: AsyncClient):
    """连接存活期间必须真正注册进广播组（只断言 accept 无法发现漏注册）。"""
    token, _case_id, execution_id = await _setup_case_execution(client)
    entered = asyncio.Event()

    class GatedWS(FakeFrontendWS):
        async def receive(self) -> dict:
            entered.set()
            await asyncio.sleep(0.05)
            return await super().receive()

    ws = GatedWS(frames=[{"type": "close"}], token=token)
    task = asyncio.create_task(execution_ws(cast(WebSocket, ws), execution_id))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await execution_manager.broadcast(execution_id, {"type": "log", "message": "live"})
    await task

    assert {"type": "log", "message": "live"} in ws.sent


async def test_execution_ws_tolerates_malformed_frames(client: AsyncClient):
    """畸形帧回 PROTOCOL_ERROR 后继续服务，不得断连（此前与 agent_ws 不对称）。"""
    token, _case_id, execution_id = await _setup_case_execution(client)

    ws = FakeFrontendWS(
        frames=[
            "{不是合法 JSON",
            '"JSON 但非对象"',
            {"type": "unknown-op"},
            {"type": "ping"},
            {"type": "close"},
        ],
        token=token,
    )
    await execution_ws(cast(WebSocket, ws), execution_id)

    assert ws.closed is None
    assert [frame["type"] for frame in ws.sent] == ["status", "error", "error", "pong"]
    assert all(f["code"] == "PROTOCOL_ERROR" for f in ws.sent if f["type"] == "error")


async def test_execution_ws_closes_1009_on_oversized_frame(client: AsyncClient):
    """超长帧按 1009 关闭（与 Agent 通道共用同一上限）。"""
    token, _case_id, execution_id = await _setup_case_execution(client)

    huge = json.dumps({"type": "ping", "padding": "x" * (settings.agent_ws_max_frame_bytes + 1)})
    ws = FakeFrontendWS(frames=[huge], token=token)
    await execution_ws(cast(WebSocket, ws), execution_id)

    assert ws.closed == (1009, "消息过大")
    assert [frame["type"] for frame in ws.sent] == ["status"]


async def test_config_ws_auth_boundary_and_ping(client: AsyncClient):
    """档案配置广播路由：同样按 token + 项目可见性鉴权。"""
    token_owner, _case_id, execution_id = await _setup_case_execution(client)
    project_id = await _project_id_of_execution(execution_id)
    token_other = await _register_and_login(client, "pytest_ws_other")

    for candidate in (None, "not-a-jwt"):
        ws = FakeFrontendWS(token=candidate)
        await config_ws(cast(WebSocket, ws), project_id)
        assert ws.accepted is False
        assert ws.closed == (1008, "认证失败或无权访问该项目")

    denied = FakeFrontendWS(token=token_other)
    await config_ws(cast(WebSocket, denied), project_id)
    assert denied.closed == (1008, "认证失败或无权访问该项目")

    await client.put(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {token_owner}"},
        json={"visibility": "public"},
    )
    allowed = FakeFrontendWS(frames=[{"type": "ping"}, {"type": "close"}], token=token_other)
    await config_ws(cast(WebSocket, allowed), project_id)
    assert allowed.accepted is True
    assert allowed.sent == [{"type": "pong"}]
    # 断开后必须注销，否则档案变更会持续向死连接推送
    await profile_config_manager.broadcast(project_id, {"type": "profile_config"})
    assert allowed.sent == [{"type": "pong"}]


async def test_agent_ws_closes_1008_when_version_gate_rejects(client: AsyncClient):
    """路由级版本门禁：走真实 register_agent（不 monkeypatch），拒绝时 1008。

    `test_security.py::test_register_rejects_old_agent_version` 覆盖的是仓储层
    返回值；这里补的是 agent_ws 对 `reply is None` 的处理，避免两层之间失联。
    """
    agent_id_str = f"pytest_ws_agent_{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as db:
        db.add(Agent(agent_key=hash_psk("sk-ws-version"), agent_id=agent_id_str, status="offline"))
        await db.commit()

    ws = _FakeAgentWS([
        _text_frame(json.dumps({
            "type": "register",
            "agent_id": agent_id_str,
            "agent_key": "sk-ws-version",
            "version": "0.0.1",
        })),
    ])
    await agent_ws(cast(WebSocket, ws))

    assert ws.closed == (1008, "Agent 认证失败或版本不受支持")
    assert ws.sent == []


async def test_assertion_result_replay_cannot_flip_terminal_status(client: AsyncClient):
    """重投的 assertion_result 不得把已落库的失败断言翻成通过。

    此前 `_upsert_assertion` 对 status 无条件覆盖，网络重投一条 pass 就能把
    fail 翻掉，导致断言明细与真实执行结果不符。
    """
    _token, _case_id, execution_id = await _setup_case_execution(client)
    async with SessionLocal() as db:
        agent_id = await _create_agent()
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await _bind_execution_to_agent(db, execution_id, agent_id)
        _execution_case, execution_steps, execution_assertions = await _snapshot_ids(
            db, execution_id
        )
        step_id = execution_steps[0].id
        assertion_id = execution_assertions[0].id

    def payload(status: str) -> dict:
        return {
            "execution_id": execution_id,
            "session_token": "sess-token",
            "execution_step_id": step_id,
            "assertions": [
                {
                    "execution_assertion_id": assertion_id,
                    "type": "text_equals",
                    "expected": "admin",
                    "actual": "guest",
                    "status": status,
                }
            ],
        }

    async with SessionLocal() as db:
        # 断言只有 pass/fail 两态（handle_assertion_result 会先归一化）
        await handlers.handle_assertion_result(db, agent_id, payload("pass"))
        stored = await db.get(ExecutionAssertion, assertion_id)
        assert stored is not None and stored.status == "pass"

        # 后续上报失败：允许升级为 fail
        await handlers.handle_assertion_result(db, agent_id, payload("fail"))
        stored = await db.get(ExecutionAssertion, assertion_id)
        assert stored is not None and stored.status == "fail"

        # 重投"通过"：不得把已落库的失败翻回去
        await handlers.handle_assertion_result(db, agent_id, payload("pass"))
        stored = await db.get(ExecutionAssertion, assertion_id)
        assert stored is not None and stored.status == "fail", "失败断言不得被重投翻转"

        # 兼容写法 passed 同样不能翻转 fail
        await handlers.handle_assertion_result(db, agent_id, payload("passed"))
        stored = await db.get(ExecutionAssertion, assertion_id)
        assert stored is not None and stored.status == "fail"
        await db.rollback()
