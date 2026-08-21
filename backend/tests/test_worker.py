import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import (
    Agent,
    Device,
    Execution,
    ExecutionCase,
    ExecutionLog,
    ExecutionQueue,
    ExecutionStep,
    Report,
)
from app.services import worker_service

REG = {"username": "pytest_worker_user", "email": "pw@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup_case(client: AsyncClient) -> tuple[str, int]:
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post(
        "/api/projects", headers=headers, json={"name": "Worker测试项目", "visibility": "private"}
    )
    project_id = project.json()["id"]
    element = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "登录按钮", "locator_type": "id", "locator_value": "${btn_id}"},
    )
    element_id = element.json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        headers=headers,
        json={
            "name": "登录用例",
            "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}],
            "assertions": [{"order": 1, "type": "element_exists", "element_id": element_id, "params": {}}],
        },
    )
    return token, case.json()["id"]


async def _create_agent_device(
    *, agent_status: str = "online", device_status: str = "idle", stale: bool = False
) -> tuple[int, int]:
    async with SessionLocal() as db:
        agent = Agent(
            agent_key=f"sk-{uuid.uuid4().hex}",
            agent_id=f"pytest_agent_{uuid.uuid4().hex[:8]}",
            hostname="pytest-host",
            status=agent_status,
            last_heartbeat=datetime.now(UTC) - (timedelta(hours=1) if stale else timedelta(seconds=5)),
        )
        db.add(agent)
        await db.flush()
        device = Device(
            agent_id=agent.id,
            name="pytest设备",
            platform="android",
            udid=f"pytest-udid-{uuid.uuid4().hex[:8]}",
            status=device_status,
        )
        db.add(device)
        await db.commit()
        return agent.id, device.id


async def _create_execution(client: AsyncClient, token: str, case_id: int, parameters: dict) -> int:
    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"parameters": parameters, "timeout_seconds": 300},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ---------- 变量渲染 ----------


def test_render_text_and_undefined():
    assert worker_service.render_text("x=${a}-${b}", {"a": "1", "b": "2"}) == "x=1-2"
    assert worker_service.render_value({"k": "v=${g}"}, {"g": "global"}) == {"k": "v=global"}
    with pytest.raises(ValueError):
        worker_service.render_text("${undefined_var}", {})


# ---------- 队列认领 + Agent 不在线 ----------


async def test_claim_and_run_agent_offline(client: AsyncClient):
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "btn_login"}})

    async with SessionLocal() as db:
        item = await worker_service.claim_next_queue(db, "worker-test")
        assert item is not None
        assert item.execution_id == execution_id
        assert item.status == "claimed"
        assert item.claimed_by == "worker-test"

        async def offline_sender(agent_id, payload):
            return False

        await worker_service.run_execution(db, execution_id, "worker-test", agent_sender=offline_sender)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "error"
        assert execution.started_at is not None
        assert execution.finished_at is not None

        device = await db.get(Device, device_id)
        assert device.status == "idle"
        assert device.locked_by_execution is None

        queue = (await db.execute(select(ExecutionQueue).where(ExecutionQueue.execution_id == execution_id))).scalar_one()
        assert queue.status == "done"

        report = (await db.execute(select(Report).where(Report.execution_id == execution_id))).scalar_one()
        assert report.total == 1
        assert report.skipped == 1

        log = (await db.execute(select(ExecutionLog).where(ExecutionLog.execution_id == execution_id))).scalar_one()
        assert "不在线" in log.message


async def test_run_undefined_variable_errors(client: AsyncClient):
    token, case_id = await _setup_case(client)
    await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {})

    async with SessionLocal() as db:
        await worker_service.claim_next_queue(db, "worker-test")

        async def offline_sender(agent_id, payload):
            return False

        await worker_service.run_execution(db, execution_id, "worker-test", agent_sender=offline_sender)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "error"
        log = (await db.execute(select(ExecutionLog).where(ExecutionLog.execution_id == execution_id))).scalar_one()
        assert "未定义变量" in log.message


# ---------- 设备原子锁 ----------


async def test_select_and_lock_device(client: AsyncClient):
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    token = login.json()["access_token"]
    project = await client.post(
        "/api/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "设备锁测试项目", "visibility": "private"},
    )
    project_id = project.json()["id"]
    _agent_id, device_id = await _create_agent_device()

    async with SessionLocal() as db:
        e1 = Execution(project_id=project_id, type="case", status="queued")
        db.add(e1)
        await db.flush()
        device = await worker_service.select_and_lock_device(db, e1)
        assert device is not None
        assert device.id == device_id
        assert device.status == "busy"
        assert device.locked_by_execution == e1.id

        e2 = Execution(project_id=project_id, type="case", status="queued")
        db.add(e2)
        await db.flush()
        assert await worker_service.select_and_lock_device(db, e2) is None
        await db.rollback()


# ---------- 快照 ----------


async def test_create_execution_snapshots(client: AsyncClient):
    token, case_id = await _setup_case(client)
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "btn_login"}})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        cases = await worker_service.create_execution_cases_from_execution(db, execution)
        assert len(cases) == 1
        ec = cases[0]
        assert ec.status == "pending"
        assert ec.steps_snapshot[0]["action"] == "click"
        assert ec.assertions_snapshot[0]["type"] == "element_exists"
        element_id = str(ec.steps_snapshot[0]["element_id"])
        assert ec.elements_snapshot[element_id]["locator_value"] == "btn_login"


async def test_create_execution_snapshots_idempotent(client: AsyncClient):
    """重复创建快照不产生重复行，且删除旧快照不触发 FK 错误（含已注入步骤）。"""
    token, case_id = await _setup_case(client)
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        db.add(ExecutionStep(execution_case_id=ec.id, step_order=1, action="click", status="passed"))
        await db.commit()

        cases = await worker_service.create_execution_cases_from_execution(db, execution)
        assert len(cases) == 1

    async with SessionLocal() as db:
        ecs = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalars().all()
        assert len(ecs) == 1
        steps = (await db.execute(
            select(ExecutionStep).where(ExecutionStep.execution_case_id == ecs[0].id)
        )).scalars().all()
        assert len(steps) == 0


# ---------- 扫描任务 ----------


async def test_timeout_scan(client: AsyncClient):
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution.status = "running"
        execution.device_id = device_id
        execution.started_at = datetime.now(UTC) - timedelta(minutes=10)
        execution.timeout_seconds = 60
        device = await db.get(Device, device_id)
        device.status = "busy"
        device.locked_by_execution = execution_id
        await db.commit()

        await worker_service.timeout_scan(db)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "error"
        device = await db.get(Device, device_id)
        assert device.status == "idle"
        assert device.locked_by_execution is None


async def test_reclaim_stale_claimed(client: AsyncClient):
    token, case_id = await _setup_case(client)
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}})

    async with SessionLocal() as db:
        queue = (await db.execute(select(ExecutionQueue).where(ExecutionQueue.execution_id == execution_id))).scalar_one()
        queue.status = "claimed"
        queue.claimed_by = "dead-worker"
        queue.claimed_at = datetime.now(UTC) - timedelta(minutes=20)
        await db.commit()

        await worker_service.reclaim_stale_claimed(db)

    async with SessionLocal() as db:
        queue = (await db.execute(select(ExecutionQueue).where(ExecutionQueue.execution_id == execution_id))).scalar_one()
        assert queue.status == "pending"
        assert queue.retry_count == 1
        assert queue.claimed_by is None


async def test_mark_terminal_derives_case_status(client: AsyncClient):
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        # 模拟步骤已执行：passed
        step = ExecutionStep(execution_case_id=ec.id, step_order=1, action="click", status="passed")
        db.add(step)
        execution.status = "running"
        execution.started_at = datetime.now(UTC) - timedelta(minutes=10)
        execution.timeout_seconds = 60
        await db.commit()

        await worker_service.timeout_scan(db)  # 触发终态（超时时间短）

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "error"
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        # 步骤全 passed，无失败 → 归为 passed（不因执行超时而误判为 skipped）
        assert ec.status == "passed"


async def test_run_execution_finalizes_on_terminal(client: AsyncClient):
    """Agent 回传 execution_result（另一会话设置终态）后，Worker 轮询应能检测并 finalize。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "btn_login"}})

    async with SessionLocal() as db:
        item = await worker_service.claim_next_queue(db, "worker-test")
        assert item is not None

    async def sender(agent_id, payload):
        return True

    async def run_worker():
        async with SessionLocal() as wdb:
            await worker_service.run_execution(
                wdb, execution_id, "worker-test", agent_sender=sender, poll_interval=0.05
            )

    task = asyncio.create_task(run_worker())

    # 主测试用独立会话轮询设备，等待 worker 锁定并进入轮询
    async with SessionLocal() as poll_db:
        for _ in range(100):
            device = await poll_db.get(Device, device_id)
            if device is not None and device.status == "busy":
                break
            await asyncio.sleep(0.05)

    # 模拟 FastAPI（Agent 回传 execution_result）在另一会话设置终态
    async with SessionLocal() as db2:
        execution = await db2.get(Execution, execution_id)
        execution.status = "passed"
        execution.finished_at = datetime.now(UTC)
        await db2.commit()

    await asyncio.wait_for(task, timeout=5)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "passed"
        queue = (await db.execute(select(ExecutionQueue).where(ExecutionQueue.execution_id == execution_id))).scalar_one()
        assert queue.status == "done"
        device = await db.get(Device, device_id)
        assert device.status == "idle"
        assert device.locked_by_execution is None
        report = (await db.execute(select(Report).where(Report.execution_id == execution_id))).scalar_one()
        assert report.total == 1
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        assert ec.status == "passed"


async def test_agent_heartbeat_scan(client: AsyncClient):
    token, case_id = await _setup_case(client)
    agent_id, device_id = await _create_agent_device(stale=True)
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution.status = "running"
        execution.device_id = device_id
        execution.started_at = datetime.now(UTC)
        await db.commit()
        device = await db.get(Device, device_id)
        device.status = "busy"
        device.locked_by_execution = execution_id
        await db.commit()

        await worker_service.agent_heartbeat_scan(db)

    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        assert agent.status == "offline"
        execution = await db.get(Execution, execution_id)
        assert execution.status == "error"
        device = await db.get(Device, device_id)
        assert device.status == "idle"
        assert device.locked_by_execution is None


# ---------- CR-06：stopping 宽限期与失联终结 ----------


async def test_timeout_scan_forces_stopped_after_grace(client: AsyncClient):
    """CR-06：stopping 超过 deadline（超时 + 宽限期）被强制 stopped 并释放设备。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution.status = "stopping"
        execution.device_id = device_id
        execution.started_at = datetime.now(UTC) - timedelta(minutes=10)
        execution.timeout_seconds = 60
        await db.commit()
        device = await db.get(Device, device_id)
        device.status = "busy"
        device.locked_by_execution = execution_id
        await db.commit()

        await worker_service.timeout_scan(db)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "stopped"
        log = (await db.execute(
            select(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
        )).scalar_one()
        assert "宽限期" in log.message
        device = await db.get(Device, device_id)
        assert device.status == "idle"
        assert device.locked_by_execution is None


async def test_timeout_scan_keeps_stopping_within_grace(client: AsyncClient):
    """CR-06：宽限期内 stopping 不被误终态。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution.status = "stopping"
        execution.device_id = device_id
        execution.started_at = datetime.now(UTC) - timedelta(seconds=5)
        execution.timeout_seconds = 60
        await db.commit()

        await worker_service.timeout_scan(db)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "stopping"


async def test_agent_heartbeat_scan_terminates_stopping(client: AsyncClient):
    """CR-06：Agent 失联时 stopping 执行也被终结为 stopped。"""
    token, case_id = await _setup_case(client)
    agent_id, device_id = await _create_agent_device(stale=True)
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}})

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution.status = "stopping"
        execution.device_id = device_id
        execution.started_at = datetime.now(UTC)
        await db.commit()
        device = await db.get(Device, device_id)
        device.status = "busy"
        device.locked_by_execution = execution_id
        await db.commit()

        await worker_service.agent_heartbeat_scan(db)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "stopped"


# ---------- CR-12：queued 取消与 Worker 原子认领竞争 ----------


async def test_cancelled_queued_execution_not_started_by_worker(client: AsyncClient):
    """CR-12：queued 执行被取消后，Worker 认领不得将其反写为 running。"""
    token, case_id = await _setup_case(client)
    await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}})

    # 用户取消 queued 执行
    cancelled = await client.post(
        f"/api/executions/{execution_id}/stop",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # Worker 尝试执行：原子认领应失败（status != queued），不得覆盖 cancelled
    async with SessionLocal() as db:
        await worker_service.claim_next_queue(db, "worker-test")

        async def sender(agent_id, payload):
            return True

        await worker_service.run_execution(db, execution_id, "worker-test", agent_sender=sender)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "cancelled"
        assert execution.started_at is None  # 从未被认领启动
        queue = (await db.execute(select(ExecutionQueue).where(ExecutionQueue.execution_id == execution_id))).scalar_one()
        assert queue.status == "done"
        # 不应生成报告
        assert (await db.execute(select(Report).where(Report.execution_id == execution_id))).scalars().all() == []
