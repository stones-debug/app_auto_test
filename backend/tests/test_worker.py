import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import (
    Agent,
    Device,
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionLog,
    ExecutionQueue,
    ExecutionStep,
    ExecutionSuite,
    Report,
)
from app.models import TestCase as CaseModel
from app.services import worker_service
from app.services.worker_runtime import WorkerRuntime
from tests.helpers import create_bound_agent_device

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
            # 断言下沉到步骤内（StepCreate.assertions），用例级 assertions 字段已不存在
            "steps": [
                {
                    "order": 1,
                    "action": "click",
                    "element_id": element_id,
                    "params": {},
                    "assertions": [
                        {"order": 1, "type": "element_exists", "element_id": element_id, "params": {}}
                    ],
                }
            ],
        },
    )
    return token, case.json()["id"]


async def _create_agent_device(
    *, agent_status: str = "online", device_status: str = "idle", stale: bool = False
) -> tuple[int, int]:
    """创建绑定到 REG 用户的 agent+device（Windows 方案 §3.3：执行设备须授权）。"""
    return await create_bound_agent_device(
        REG["username"],
        agent_status=agent_status,
        device_status=device_status,
        stale=stale,
    )


async def _create_execution(client: AsyncClient, token: str, case_id: int, parameters: dict, device_id: int) -> int:
    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_id": device_id, "parameters": parameters, "timeout_seconds": 300},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_embedded_runtime_consumes_and_finalizes_execution(client: AsyncClient):
    """只启动嵌入 runtime 即可完成队列消费、报告汇总和设备释放。"""
    token, case_id = await _setup_case(client)
    agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(
        client,
        token,
        case_id,
        {"variables": {"btn_id": "login-button"}},
        device_id,
    )
    sent_messages: list[tuple[int, dict]] = []
    terminal_tasks: list[asyncio.Task] = []

    async def sender(target_agent_id: int, payload: dict) -> bool:
        sent_messages.append((target_agent_id, payload))

        async def finish_execution() -> None:
            await asyncio.sleep(0.02)
            async with SessionLocal() as finish_db:
                execution = await finish_db.get(Execution, execution_id)
                execution.status = "passed"
                execution.finished_at = datetime.now(UTC)
                await finish_db.commit()

        terminal_tasks.append(asyncio.create_task(finish_execution()))
        return True

    runtime = WorkerRuntime(
        "fastapi-embedded-test",
        enable_scans=False,
        agent_sender=sender,
        poll_interval=0.01,
        execution_poll_interval=0.01,
    )
    await runtime.start()
    try:
        async with asyncio.timeout(3):
            while True:
                async with SessionLocal() as check_db:
                    queue = (
                        await check_db.execute(
                            select(ExecutionQueue).where(
                                ExecutionQueue.execution_id == execution_id
                            )
                        )
                    ).scalar_one()
                    if queue.status == "done":
                        break
                await asyncio.sleep(0.01)
    finally:
        await runtime.stop()
        if terminal_tasks:
            await asyncio.gather(*terminal_tasks)

    assert len(sent_messages) == 1
    assert sent_messages[0][0] == agent_id
    assert sent_messages[0][1]["type"] == "start_test"
    assert sent_messages[0][1]["execution_id"] == execution_id

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        device = await db.get(Device, device_id)
        report = (
            await db.execute(select(Report).where(Report.execution_id == execution_id))
        ).scalar_one()
        assert execution.status == "passed"
        assert execution.finalized_at is not None
        assert device.status == "idle"
        assert device.locked_by_execution is None
        assert report.total == 1


# ---------- 变量渲染 ----------


def test_render_text_and_undefined():
    assert worker_service.render_text("x=${a}-${b}", {"a": "1", "b": "2"}) == "x=1-2"
    assert worker_service.render_value({"k": "v=${g}"}, {"g": "global"}) == {"k": "v=global"}
    with pytest.raises(ValueError):
        worker_service.render_text("${undefined_var}", {})


async def test_snapshot_selects_and_reorders_pre_main_post_steps(client: AsyncClient):
    token, case_id = await _setup_case(client)
    headers = {"Authorization": f"Bearer {token}"}
    updated = await client.put(
        f"/api/cases/{case_id}",
        headers=headers,
        json={
            "steps": [
                {"order": 1, "phase": "setup", "action": "sleep", "params": {"duration": 0}},
                {"order": 1, "phase": "main", "action": "back", "params": {}},
                {"order": 1, "phase": "teardown", "action": "screenshot", "params": {}},
            ]
        },
    )
    assert updated.status_code == 200, updated.text
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(
        client,
        token,
        case_id,
        {
            "variables": {"btn_id": "login-button"},
            "use_pre_steps": True,
            "use_post_steps": True,
        },
        device_id,
    )

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        cases = await worker_service.create_execution_cases_from_execution(db, execution)

    steps = cases[0].steps_snapshot
    assert [step["phase"] for step in steps] == ["setup", "main", "teardown"]
    assert [step["order"] for step in steps] == [1, 2, 3]
    assert [step["source_order"] for step in steps] == [1, 1, 1]


# ---------- 队列认领 + Agent 不在线 ----------


async def test_claim_and_run_agent_offline(client: AsyncClient):
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "btn_login"}}, device_id)

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
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {}, device_id)

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
        # 指定设备路径（确定性，避免共享库中其他在线 Agent 的设备干扰）
        e1.device_id = device_id
        device = await worker_service.select_and_lock_device(db, e1)
        assert device is not None
        assert device.id == device_id
        assert device.status == "busy"
        assert device.locked_by_execution == e1.id

        # 原子性：e2 无法锁定 e1 已占用的设备（可能拿到其他空闲设备或 None）
        e2 = Execution(project_id=project_id, type="case", status="queued")
        db.add(e2)
        await db.flush()
        device2 = await worker_service.select_and_lock_device(db, e2)
        assert device2 is None or device2.id != device_id
        await db.rollback()


# ---------- 快照 ----------


async def test_create_execution_snapshots(client: AsyncClient):
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "btn_login"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        cases = await worker_service.create_execution_cases_from_execution(db, execution)
        assert len(cases) == 1
        ec = cases[0]
        assert ec.status == "pending"
        assert ec.steps_snapshot[0]["action"] == "click"
        # 断言下沉到步骤内，ExecutionCase 模型不再有 assertions_snapshot 列
        assert ec.steps_snapshot[0]["assertions"][0]["type"] == "element_exists"
        element_id = str(ec.steps_snapshot[0]["element_id"])
        assert ec.elements_snapshot[element_id]["locator_value"] == "btn_login"


async def test_create_execution_snapshots_idempotent(client: AsyncClient):
    """重复创建快照不产生重复行，且删除旧快照不触发 FK 错误（含已注入步骤）。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        injected_step = await db.scalar(
            select(ExecutionStep).where(ExecutionStep.execution_case_id == ec.id)
        )
        injected_step.status = "passed"
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
        assert len(steps) == len(ecs[0].steps_snapshot)
        assert all(step.status == "pending" for step in steps)


async def test_suites_payload_injects_execution_node_ids(client: AsyncClient):
    """协议 V2：_build_suites_payload 下发步骤/断言时必须注入预建的 execution_*_id，
    且套件元素表随套件 payload 一并下发（否则套件前后置元素操作失败）。

    回归1：此前原样下发 assertions_snapshot（无 id），Agent 又按遍历序号从 1 重编号，
    导致后端 _upsert_assertion 匹配不到预建 pending 行而插入新断言，原行被标记 skipped，
    造成报告重复与统计错误。模拟档案路径（materialize_snapshot）预建断言的形态。
    回归2：此前套件 payload 缺 elements_snapshot，Agent 始终以空元素表执行套件前后置。
    """
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "btn_login"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.app_profile_id is None
        # 无档案 → 走 _rebuild_legacy_snapshot 重建用例（含断言 snapshot，但不预建断言行）
        await worker_service.create_execution_cases_from_execution(db, execution)
        await db.commit()
        ec = (
            await db.execute(
                select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
            )
        ).scalars().first()
        assert ec is not None
        # 模拟档案路径：执行已含 assertion snapshot（order 从 steps 数量后开始），预建 pending 断言
        # 断言下沉后挂在步骤上，这里从 steps_snapshot 展平
        assertions_snapshot = [
            a for step in (ec.steps_snapshot or []) for a in (step.get("assertions") or [])
        ]
        assert len(assertions_snapshot) >= 1
        sent_order = int(assertions_snapshot[0].get("order") or 0)
        row = await db.scalar(
            select(ExecutionAssertion)
            .join(ExecutionStep, ExecutionAssertion.execution_step_id == ExecutionStep.id)
            .where(
                ExecutionStep.execution_case_id == ec.id,
                ExecutionAssertion.assertion_order == sent_order,
            )
        )
        assert row is not None
        # 模拟档案路径：套件元素表已固化（materialize_snapshot 写入 elements_snapshot）
        suite_row = (
            await db.execute(
                select(ExecutionSuite).where(
                    ExecutionSuite.execution_id == execution_id,
                    ExecutionSuite.is_virtual.is_(True),
                )
            )
        ).scalars().first()
        assert suite_row is not None
        suite_row.elements_snapshot = {"9": {"locator_type": "id", "locator_value": "svc_btn"}}
        await db.commit()
        precreated_id = row.id

        payload = await worker_service._build_suites_payload(db, execution)
        assert len(payload) == 1
        suite_payload = payload[0]
        # 套件元素表必须随 payload 下发（非空、非缺字段）
        assert suite_payload.get("elements_snapshot") == {"9": {"locator_type": "id", "locator_value": "svc_btn"}}
        case_payload = suite_payload["cases"][0]
        sent_step = case_payload["steps_snapshot"][0]
        precreated_step = await db.scalar(
            select(ExecutionStep).where(
                ExecutionStep.execution_case_id == ec.id,
                ExecutionStep.step_order == int(sent_step["order"]),
            )
        )
        assert sent_step["execution_step_id"] == precreated_step.id
        # 断言随步骤下发（_build_suites_payload 注入到 steps_snapshot 各条目内）
        sent_assertion = sent_step["assertions"][0]
        # 必须注入 execution_assertion_id，且与预建行一致
        assert sent_assertion.get("execution_assertion_id") == precreated_id
        # assertion_order 应沿用快照 order（而非 Agent 从 1 重编号），与预建行 assertion_order 对齐
        assert int(sent_assertion.get("order") or 0) == sent_order


async def test_suites_payload_carries_suite_step_continue_on_failure(client: AsyncClient):
    """协议 V2：套件步的 continue_on_failure 必须经 ExecutionStep 行固化并下发。

    回归：快照（setup_steps_snapshot）保留 continue_on_failure，但 ExecutionStep 无该列、
    payload 也不下发，Agent 运行时步失败始终中止（continue_on_failure 配置丢失）。
    """
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await db.commit()
        suite = (
            await db.execute(
                select(ExecutionSuite).where(
                    ExecutionSuite.execution_id == execution_id,
                    ExecutionSuite.is_virtual.is_(True),
                )
            )
        ).scalars().first()
        assert suite is not None
        db.add(
            ExecutionStep(
                execution_suite_id=suite.id,
                phase="suite_setup",
                step_order=1,
                action="sleep",
                parameters={"duration": 1},
                continue_on_failure=True,
                status="pending",
            )
        )
        await db.commit()

        payload = await worker_service._build_suites_payload(db, execution)
        setup_steps = payload[0]["setup_steps"]
        assert len(setup_steps) == 1
        assert setup_steps[0]["continue_on_failure"] is True


# ---------- 智能元素定位（无档案 build_case_snapshot） ----------


async def test_build_case_snapshot_smart_element_config_raw(client: AsyncClient):
    """无档案路径 build_case_snapshot：smart 元素写 locator_config raw，不渲染 ${...} 变量。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post(
        "/api/projects", headers=headers, json={"name": "Worker智能定位项目", "visibility": "private"}
    )
    project_id = project.json()["id"]
    smart_config = {
        "version": 1,
        "alternatives": [
            {"target": [{"attribute": "text", "operator": "equals", "value": "${device_name}"}]}
        ],
    }
    el = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "智能按钮", "locator_type": "smart", "locator_config": smart_config},
    )
    assert el.status_code == 201, el.text
    element_id = el.json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        headers=headers,
        json={
            "name": "智能元素用例",
            "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}],
        },
    )
    assert case.status_code == 201, case.text

    async with SessionLocal() as db:
        case_model = await db.get(CaseModel, case.json()["id"])
        snapshot = await worker_service.build_case_snapshot(db, case_model, {})

    snap = snapshot["elements"][str(element_id)]
    assert snap["locator_type"] == "smart"
    assert snap["locator_value"] is None
    # ${device_name} 保持未渲染（raw 透传，后端不因未定义变量报错）
    assert snap["locator_config"]["alternatives"][0]["target"][0]["value"] == "${device_name}"


async def test_smart_locator_payload_contains_raw_config(client: AsyncClient):
    """协议 V2：_build_suites_payload 下发 start_test payload 时，
    smart 元素 locator_config 保持 raw 透传（${device_name} 未渲染、locator_value 为 None），
    普通元素 locator_value 已渲染为最终值、locator_config 为 None，条目均含 platform。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post(
        "/api/projects", headers=headers, json={"name": "Worker智能Payload项目", "visibility": "private"}
    )
    project_id = project.json()["id"]
    smart_config = {
        "version": 1,
        "alternatives": [
            {"target": [{"attribute": "text", "operator": "equals", "value": "${device_name}"}]}
        ],
    }
    smart_el = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "智能按钮", "locator_type": "smart", "locator_config": smart_config},
    )
    assert smart_el.status_code == 201, smart_el.text
    smart_id = smart_el.json()["id"]
    ordinary_el = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "普通按钮", "locator_type": "id", "locator_value": "${btn_id}"},
    )
    assert ordinary_el.status_code == 201, ordinary_el.text
    ordinary_id = ordinary_el.json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        headers=headers,
        json={
            "name": "混合元素用例",
            "steps": [
                {"order": 1, "action": "click", "element_id": smart_id, "params": {}},
                {"order": 2, "action": "click", "element_id": ordinary_id, "params": {}},
            ],
        },
    )
    assert case.status_code == 201, case.text

    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(
        client, token, case.json()["id"], {"variables": {"btn_id": "admin"}}, device_id
    )

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        await db.commit()
        payload = await worker_service._build_suites_payload(db, execution)

    assert len(payload) == 1
    elements = payload[0]["cases"][0]["elements_snapshot"]

    smart_snap = elements[str(smart_id)]
    assert smart_snap["locator_type"] == "smart"
    assert smart_snap["locator_value"] is None
    assert isinstance(smart_snap["locator_config"], dict)
    # ${device_name} 原样未渲染
    assert smart_snap["locator_config"]["alternatives"][0]["target"][0]["value"] == "${device_name}"
    assert smart_snap["platform"] == "both"

    ordinary_snap = elements[str(ordinary_id)]
    assert ordinary_snap["locator_type"] == "id"
    # 普通元素变量已渲染为最终值
    assert ordinary_snap["locator_value"] == "admin"
    assert ordinary_snap["locator_config"] is None
    assert ordinary_snap["platform"] == "both"


# ---------- 扫描任务 ----------


async def test_timeout_scan(client: AsyncClient):
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

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
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

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
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        # 模拟步骤已执行：passed（快照重建已预建 pending 行，直接更新状态）
        step = (await db.execute(
            select(ExecutionStep).where(
                ExecutionStep.execution_case_id == ec.id,
                ExecutionStep.step_order == 1,
            )
        )).scalar_one()
        step.status = "passed"
        execution.status = "running"
        execution.started_at = datetime.now(UTC) - timedelta(minutes=10)
        execution.timeout_seconds = 60
        await db.commit()

        await worker_service.timeout_scan(db)  # 触发终态（超时时间短）

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "error"
        # Windows 方案 §2：唯一终态汇总完成时刻必须落库
        assert execution.finalized_at is not None
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        # CR-11：执行超时中断 → 当前 case 归为 error（而非误判 passed）
        assert ec.status == "error"
        step = (await db.execute(
            select(ExecutionStep).where(ExecutionStep.execution_case_id == ec.id)
        )).scalar_one()
        assert step.status == "passed"  # 已执行步骤不受影响


async def test_run_execution_finalizes_on_terminal(client: AsyncClient):
    """Agent 回传 execution_result（另一会话设置终态）后，Worker 轮询应能检测并 finalize。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "btn_login"}}, device_id)

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
        # CR-11：终态为 passed 时无失败 case 归为 passed
        assert ec.status == "passed"


async def test_agent_heartbeat_scan(client: AsyncClient):
    token, case_id = await _setup_case(client)
    agent_id, device_id = await _create_agent_device(stale=True)
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

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


async def test_agent_heartbeat_scan_null_heartbeat(client: AsyncClient):
    """last_heartbeat IS NULL 的 online agent 也应被置离线（NULL < threshold 恒为假）。"""
    await client.post("/api/auth/register", json=REG)
    agent_id, device_id = await _create_agent_device(agent_status="online")
    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        agent.last_heartbeat = None
        await db.commit()

        await worker_service.agent_heartbeat_scan(db)

    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        assert agent.status == "offline"


async def test_runtime_start_scans_marks_stale_agents_offline(client: AsyncClient):
    """后端重启场景：runtime 启动时立即扫描，残留 online 且心跳过期的 agent 被置离线。"""
    await client.post("/api/auth/register", json=REG)
    agent_id, _device_id = await _create_agent_device(stale=True)
    runtime = WorkerRuntime("boot-scan-test", enable_scans=True, poll_interval=60)
    try:
        await runtime.start()
    finally:
        await runtime.stop()
    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        assert agent.status == "offline"


# ---------- CR-06：stopping 宽限期与失联终结 ----------


async def test_timeout_scan_forces_stopped_after_grace(client: AsyncClient):
    """CR-06：stopping 超过 deadline（超时 + 宽限期）被强制 stopped 并释放设备。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

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
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

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


async def test_timeout_scan_forces_stopped_from_stop_requested_at(client: AsyncClient):
    """Windows 方案 §2：stopping 宽限期从 stop_requested_at（用户请求停止时刻）起算。

    started_at 很近（旧口径不会强制），但 stop_requested_at 已超过宽限期 → 必须强制 stopped。
    """
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution.status = "stopping"
        execution.device_id = device_id
        execution.started_at = datetime.now(UTC) - timedelta(seconds=5)
        execution.stop_requested_at = datetime.now(UTC) - timedelta(
            seconds=settings.execution_stop_grace_seconds + 10
        )
        execution.timeout_seconds = 60
        await db.commit()

        await worker_service.timeout_scan(db)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == "stopped"
        assert execution.finalized_at is not None
        device = await db.get(Device, device_id)
        assert device.status == "idle"
        assert device.locked_by_execution is None


async def test_timeout_scan_keeps_stopping_within_grace_from_stop_requested_at(client: AsyncClient):
    """Windows 方案 §2：stop_requested_at 在宽限期内时 stopping 不被误终态。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution.status = "stopping"
        execution.device_id = device_id
        execution.started_at = datetime.now(UTC) - timedelta(minutes=10)
        execution.stop_requested_at = datetime.now(UTC) - timedelta(seconds=5)
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
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

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


# ---------- CR-10/CR-11：小数成功率 + 幂等汇总 + 状态归并 ----------


async def test_mark_terminal_fractional_success_rate(client: AsyncClient):
    """CR-10：success_rate 保存两位小数（66.67），0/0 → 0。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        # 再补 2 个用例行：共 3 个 case，其中 1 个 failed → 2/3 = 66.67
        exec_suite_id = (
            await db.execute(
                select(ExecutionSuite).where(ExecutionSuite.execution_id == execution_id)
            )
        ).scalar_one().id
        # 断言下沉后 ExecutionCase 不再有 assertions_snapshot 列
        db.add(ExecutionCase(execution_id=execution_id, execution_suite_id=exec_suite_id, case_id=9001, case_name="c1", case_order=2, status="running", steps_snapshot=[]))
        db.add(ExecutionCase(execution_id=execution_id, execution_suite_id=exec_suite_id, case_id=9002, case_name="c2", case_order=3, status="running", steps_snapshot=[]))
        await db.commit()
        ecs = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id).order_by(ExecutionCase.id)
        )).scalars().all()
        assert len(ecs) == 3
        # ecs[0] 来自快照重建（已预建 order=1 行）→ 更新；ecs[1]/ecs[2] 为手补用例（空快照）→ 新建
        first_step = (await db.execute(
            select(ExecutionStep).where(
                ExecutionStep.execution_case_id == ecs[0].id,
                ExecutionStep.step_order == 1,
            )
        )).scalar_one()
        first_step.status = "failed"
        for ec in ecs[1:]:
            db.add(ExecutionStep(execution_case_id=ec.id, step_order=1, action="click", status="passed"))
        await db.commit()
        execution.status = "running"
        execution.started_at = datetime.now(UTC) - timedelta(seconds=10)
        await db.commit()
        await worker_service._mark_terminal(db, execution, "passed")

    async with SessionLocal() as db:
        report = (await db.execute(select(Report).where(Report.execution_id == execution_id))).scalar_one()
        assert report.total == 3
        assert report.passed == 2
        assert report.failed == 1
        assert float(report.success_rate) == 66.67


async def test_mark_terminal_zero_cases_rate_zero(client: AsyncClient):
    """CR-10：无用例时成功率为 0 而非除零错误。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution.status = "running"
        execution.started_at = datetime.now(UTC)
        await db.commit()
        await worker_service._mark_terminal(db, execution, "error", "无快照用例")

    async with SessionLocal() as db:
        report = (await db.execute(select(Report).where(Report.execution_id == execution_id))).scalar_one()
        assert report.total == 0
        assert float(report.success_rate) == 0.0


async def test_mark_terminal_idempotent_under_concurrency(client: AsyncClient):
    """CR-11：并发终态汇总只产生一份报告，且只有一个进程推进状态。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        execution.status = "running"
        execution.device_id = device_id
        execution.started_at = datetime.now(UTC)
        await db.commit()
        device = await db.get(Device, device_id)
        device.status = "busy"
        device.locked_by_execution = execution_id
        await db.commit()

    async def _finalize():
        async with SessionLocal() as sdb:
            ex = await sdb.get(Execution, execution_id)
            await worker_service._mark_terminal(sdb, ex, "stopped")

    await asyncio.gather(_finalize(), _finalize())

    async with SessionLocal() as db:
        reports = (await db.execute(select(Report).where(Report.execution_id == execution_id))).scalars().all()
        assert len(reports) == 1  # 幂等：只一份报告
        execution = await db.get(Execution, execution_id)
        assert execution.status == "stopped"
        device = await db.get(Device, device_id)
        assert device.status == "idle"
        assert device.locked_by_execution is None


async def test_mark_terminal_skips_unexecuted_steps(client: AsyncClient):
    """CR-11：中断时当前 case 置 stopped，未执行步骤置 skipped。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        # 快照重建已预建 order=1 的 pending 行：更新为 passed；另补一条 order=2 pending（尚未执行）
        step1 = (await db.execute(
            select(ExecutionStep).where(
                ExecutionStep.execution_case_id == ec.id,
                ExecutionStep.step_order == 1,
            )
        )).scalar_one()
        step1.status = "passed"
        db.add(ExecutionStep(execution_case_id=ec.id, step_order=2, action="input", status="pending"))
        execution.status = "running"
        execution.started_at = datetime.now(UTC)
        await db.commit()
        await worker_service._mark_terminal(db, execution, "stopped")

    async with SessionLocal() as db:
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        assert ec.status == "stopped"
        steps = (await db.execute(
            select(ExecutionStep).where(ExecutionStep.execution_case_id == ec.id).order_by(ExecutionStep.step_order)
        )).scalars().all()
        assert steps[0].status == "passed"
        assert steps[1].status == "skipped"


async def test_mark_terminal_never_started_case_with_precreated_rows_is_skipped(client: AsyncClient):
    """CR-11 修正：快照预建的 pending 步骤/断言行不算"已开始"信号。

    回归：材料化路径（档案执行）在创建时预建全部 ExecutionStep/ExecutionAssertion（pending），
    旧逻辑以"存在步骤或断言行"判断是否开始，导致尚未开始的后续用例被误判为 stopped/error；
    按方案应为 skipped（未开始→skipped，已开始→stopped/error）。
    """
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        # 材料化路径：快照预建 pending 行已由重建产生（用例从未 report running，无 started_at）
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        execution.status = "running"
        execution.started_at = datetime.now(UTC)
        await db.commit()
        # 停止收敛：用例从未 report running/case_status，无 started_at → skipped
        await worker_service._mark_terminal(db, execution, "stopped")

    async with SessionLocal() as db:
        ec = (await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        assert ec.status == "skipped"
        assert ec.started_at is None
        steps = (await db.execute(
            select(ExecutionStep).where(ExecutionStep.execution_case_id == ec.id)
        )).scalars().all()
        # 收敛后未执行步骤仍置 skipped（与终态一致）
        assert all(s.status == "skipped" for s in steps)


# ---------- CR-12：queued 取消与 Worker 原子认领竞争 ----------


async def test_cancelled_queued_execution_not_started_by_worker(client: AsyncClient):
    """CR-12：queued 执行被取消后，Worker 认领不得将其反写为 running。"""
    token, case_id = await _setup_case(client)
    _agent_id, device_id = await _create_agent_device()
    execution_id = await _create_execution(client, token, case_id, {"variables": {"btn_id": "x"}}, device_id)

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
