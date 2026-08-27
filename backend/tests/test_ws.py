import uuid
from datetime import UTC, datetime

import pytest
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
    ExecutionStep,
    ExecutionSuite,
    Project,
    User,
)
from app.services import worker_service
from app.ws import handlers
from app.ws.managers import agent_manager, execution_manager
from tests.helpers import create_bound_agent_device

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
        json={
            "name": "WS用例",
            "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}],
            "assertions": [
                {
                    "order": 1,
                    "type": "text_equals",
                    "element_id": element_id,
                    "params": {"expected": "admin"},
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
    assertions = list(
        (
            await db.execute(
                select(ExecutionAssertion)
                .where(ExecutionAssertion.execution_case_id == execution_case.id)
                .order_by(ExecutionAssertion.assertion_order)
            )
        ).scalars()
    )
    return execution_case, steps, assertions


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

        await handlers.handle_execution_result(db, agent_id, {"execution_id": execution_id, "session_token": "sess-token", "status": "PASSED"})

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
                "execution_case_id": execution_case.id,
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
        rows = (await db.execute(
            select(ExecutionAssertion).where(ExecutionAssertion.execution_case_id == ec.id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].assertion_type == "text_equals"
        assert rows[0].status == "pass"
        assert ec.status == "passed"

    # B5：assertion_result 广播（V2 §8.2）
    assertion_msgs = [m for m in front.sent if m["type"] == "assertion_result"]
    assert len(assertion_msgs) == 1
    assert assertion_msgs[0]["case_id"] == case_id
    assert assertion_msgs[0]["assertions"][0]["status"] == "pass"
    assert assertion_msgs[0]["assertions"][0]["assertion_order"] == 1
    assert assertion_msgs[0]["assertions"][0]["execution_assertion_id"] == execution_assertions[0].id
    assert assertion_msgs[0]["case_status"] == "passed"
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
                "execution_case_id": execution_case.id,
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
        assertion = (
            await db.execute(
                select(ExecutionAssertion)
                .where(ExecutionAssertion.execution_case_id == execution_case.id)
            )
        ).scalar_one()
        assert assertion.status == "fail"
        assert execution_case.status == "failed"
        assert execution.status == "failed"


# ---------- CR-16：Agent 重连身份 / CR-24：广播空组清理 ----------


class FailingWebSocket:
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
