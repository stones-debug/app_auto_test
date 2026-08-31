"""Step 6：设备强制释放矩阵 + Agent 软注销/重新激活 + 并发互斥 + 恢复扫描。"""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_psk
from app.main import app
from app.models import Agent, AgentUser, Device, DevicePreference, Execution, Report, User

REG = {"username": "pytest_step6_admin", "email": "s6@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _admin_token(client: AsyncClient) -> str:
    await client.post("/api/auth/register", json=REG)
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == REG["username"]))).scalar_one()
        user.is_admin = True
        await db.commit()
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    return login.json()["access_token"]


async def _make_agent(agent_id: str = "pytest_step6_agent") -> tuple[int, int]:
    """创建 online Agent + idle 设备，返回 (agent_db_id, device_id)。"""
    async with SessionLocal() as db:
        agent = Agent(agent_key=hash_psk("sk-step6"), agent_id=agent_id, status="online")
        db.add(agent)
        await db.flush()
        device = Device(agent_id=agent.id, name="S6设备", platform="android", udid="u-s6", status="idle")
        db.add(device)
        await db.commit()
        return agent.id, device.id


async def _project_owner_id() -> int:
    """返回测试管理员用户 id（每测试新建，id 会递增，不可硬编码）。"""
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == REG["username"]))).scalar_one()
        return user.id


async def _make_execution(status: str = "queued", *, finalized: bool = False) -> int:
    """创建一条执行（不锁定设备）。返回 execution_id。"""
    from app.models import Project

    now = datetime.now(UTC)
    async with SessionLocal() as db:
        project = Project(name="S6执行项目", owner_id=await _project_owner_id(), visibility="private")
        db.add(project)
        await db.commit()
        await db.refresh(project)
        execution = Execution(
            project_id=project.id,
            type="case",
            status=status,
            started_at=now if status != "queued" else None,
            finished_at=now if status in ("passed", "failed", "error", "stopped", "cancelled") else None,
            finalized_at=now if finalized else None,
        )
        db.add(execution)
        await db.commit()
        return execution.id


async def _lock_device_into(device_id: int, status: str, *, finalized: bool) -> int:
    """把设备锁到一条执行上并设置指定状态。返回 execution_id。"""
    execution_id = await _make_execution(status, finalized=finalized)
    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        execution.device_id = device_id
        device = await db.get(Device, device_id)
        device.status = "busy"
        device.locked_by_execution = execution_id
        await db.commit()
    return execution_id


# ---------- 释放矩阵 ----------


async def test_release_no_lock_sets_idle_or_offline(client: AsyncClient):
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    _agent_id, device_id = await _make_agent()

    resp = await client.post(f"/api/devices/{device_id}/release", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "released"
    assert body["device"]["status"] == "idle"
    assert body["execution_id"] is None


async def test_release_queued_cancels_and_releases(client: AsyncClient):
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    _agent_id, device_id = await _make_agent()
    exec_id = await _lock_device_into(device_id, "queued", finalized=False)

    resp = await client.post(f"/api/devices/{device_id}/release", headers=headers)
    body = resp.json()
    assert body["action"] == "released"
    assert body["device"]["locked_by_execution"] is None
    async with SessionLocal() as db:
        execution = await db.get(Execution, exec_id)
        assert execution.status == "cancelled"
        assert execution.finalized_at is not None


async def test_release_running_requests_stop_keeps_lock(client: AsyncClient):
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    _agent_id, device_id = await _make_agent()
    exec_id = await _lock_device_into(device_id, "running", finalized=False)

    resp = await client.post(f"/api/devices/{device_id}/release", headers=headers)
    body = resp.json()
    assert body["action"] == "stop_requested"
    assert body["execution_id"] == exec_id
    assert body["execution_status"] == "stopping"
    assert body["device"]["status"] == "busy"
    assert body["device"]["locked_by_execution"] == exec_id
    async with SessionLocal() as db:
        execution = await db.get(Execution, exec_id)
        assert execution.status == "stopping"


async def test_release_stopping_idempotent(client: AsyncClient):
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    _agent_id, device_id = await _make_agent()
    exec_id = await _lock_device_into(device_id, "stopping", finalized=False)

    resp = await client.post(f"/api/devices/{device_id}/release", headers=headers)
    body = resp.json()
    assert body["action"] == "stop_requested"
    assert body["device"]["locked_by_execution"] == exec_id
    async with SessionLocal() as db:
        execution = await db.get(Execution, exec_id)
        assert execution.status == "stopping"  # 不重复写状态


async def test_release_terminal_unfinalized_keeps_lock(client: AsyncClient):
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    _agent_id, device_id = await _make_agent()
    exec_id = await _lock_device_into(device_id, "passed", finalized=False)

    resp = await client.post(f"/api/devices/{device_id}/release", headers=headers)
    body = resp.json()
    assert body["action"] == "finalization_pending"
    assert body["execution_id"] == exec_id
    assert body["device"]["locked_by_execution"] == exec_id


async def test_release_terminal_finalized_clears_stale_lock(client: AsyncClient):
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    _agent_id, device_id = await _make_agent()
    await _lock_device_into(device_id, "passed", finalized=True)

    resp = await client.post(f"/api/devices/{device_id}/release", headers=headers)
    body = resp.json()
    assert body["action"] == "released"
    assert body["device"]["locked_by_execution"] is None


async def test_release_orphan_lock_logged_and_cleared(client: AsyncClient):
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    _agent_id, device_id = await _make_agent()
    exec_id = await _lock_device_into(device_id, "running", finalized=False)
    # 模拟执行行被外部删除后遗留的孤儿锁：临时移除 FK，删除执行行后恢复
    from sqlalchemy import delete, text

    async with SessionLocal() as db:
        await db.execute(text("ALTER TABLE devices DROP CONSTRAINT fk_devices_locked_execution"))
        await db.execute(text("ALTER TABLE executions DROP CONSTRAINT executions_device_id_fkey"))
        await db.execute(delete(Execution).where(Execution.id == exec_id))
        await db.commit()
    try:
        resp = await client.post(f"/api/devices/{device_id}/release", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["action"] == "released"
        async with SessionLocal() as db:
            device = await db.get(Device, device_id)
            assert device.locked_by_execution is None
    finally:
        async with SessionLocal() as db:
            await db.execute(
                text("ALTER TABLE executions ADD CONSTRAINT executions_device_id_fkey "
                     "FOREIGN KEY (device_id) REFERENCES devices (id)")
            )
            await db.execute(
                text("ALTER TABLE devices ADD CONSTRAINT fk_devices_locked_execution "
                     "FOREIGN KEY (locked_by_execution) REFERENCES executions (id)")
            )
            await db.commit()


# ---------- Agent 软注销 ----------


async def test_agent_soft_delete_keeps_history(client: AsyncClient):
    """软注销保留 Agent/Device/Execution/Report 行；删除绑定与偏好；列表/详情不可见。"""
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    agent_id, device_id = await _make_agent()

    deleted = await client.delete(f"/api/agents/{agent_id}", headers=headers)
    assert deleted.status_code == 204

    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        assert agent.deleted_at is not None
        assert agent.status == "offline"
        device = await db.get(Device, device_id)
        assert device is not None  # 行保留
        assert device.status == "offline"

    listed = await client.get("/api/agents", headers=headers)
    assert all(a["id"] != agent_id for a in listed.json())
    dev_list = await client.get(f"/api/agents/{agent_id}/devices", headers=headers)
    assert dev_list.status_code == 404  # require_agent_access 拒绝软注销 Agent
    dev_get = await client.get(f"/api/devices/{device_id}", headers=headers)
    assert dev_get.status_code == 404  # require_device_access 联查 Agent


async def test_agent_delete_rejects_active_executions(client: AsyncClient):
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    agent_id, device_id = await _make_agent()
    await _lock_device_into(device_id, "queued", finalized=False)

    resp = await client.delete(f"/api/agents/{agent_id}", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "AGENT_HAS_ACTIVE_EXECUTIONS"

    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        assert agent.deleted_at is None  # 不得部分修改


async def test_agent_delete_removes_bindings_and_preferences(client: AsyncClient):
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    agent_id, device_id = await _make_agent()
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == REG["username"]))).scalar_one()
        db.add(
            AgentUser(agent_id=agent_id, user_id=user.id, revoke_credential_hash=hash_psk("rv"))
        )
        db.add(DevicePreference(user_id=user.id, device_id=device_id))
        await db.commit()

    assert (await client.delete(f"/api/agents/{agent_id}", headers=headers)).status_code == 204
    async with SessionLocal() as db:
        binds = (await db.execute(select(AgentUser).where(AgentUser.agent_id == agent_id))).scalars().all()
        prefs = (await db.execute(select(DevicePreference).where(DevicePreference.device_id == device_id))).scalars().all()
        assert len(binds) == 0
        assert len(prefs) == 0


# ---------- 软注销实例重新激活 ----------


async def test_soft_deleted_agent_reactivation_rotates_psk(client: AsyncClient):
    alice = {"username": "pytest_s6_alice", "email": "s6a@tl-tek.com", "password": "test123"}
    await client.post("/api/auth/register", json=alice)
    login = await client.post("/api/auth/login", json={"username": alice["username"], "password": alice["password"]})
    token = login.json()["access_token"]
    key_resp = await client.post("/api/me/agent-key", headers={"Authorization": f"Bearer {token}"})
    user_key = key_resp.json()["key"]

    install_id = "install-s6-reactivate"
    resp = await client.post("/api/agent/bind", json={"user_key": user_key, "install_id": install_id})
    assert resp.status_code == 201
    machine_psk = resp.json()["machine_psk"]

    # 经管理员接口软注销（会删除绑定）
    admin = await _admin_token(client)
    async with SessionLocal() as db:
        agent = (await db.execute(select(Agent).where(Agent.agent_id == install_id))).scalar_one()
        agent_db_id = agent.id
    assert (await client.delete(f"/api/agents/{agent_db_id}", headers={"Authorization": f"Bearer {admin}"})).status_code == 204

    headers = {"Authorization": f"Bearer {admin}"}
    # 注销后普通列表不可见；管理员列表也不可见
    listed = await client.get("/api/agents", headers=headers)
    assert all(a["id"] != agent_db_id for a in listed.json())

    # 有效用户 Key 可直接重新激活，不再要求旧机器 PSK。
    reactivated = await client.post(
        "/api/agent/bind",
        json={"user_key": user_key, "install_id": install_id},
    )
    assert reactivated.status_code == 201
    new_psk = reactivated.json()["machine_psk"]
    assert new_psk and new_psk != machine_psk

    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_db_id)
        assert agent.deleted_at is None
        assert agent.agent_key != hash_psk(machine_psk)
        # 新 PSK 可以认证（旧 PSK 失效）
        from app.core.security import verify_psk

        assert verify_psk(new_psk, agent.agent_key)
        assert not verify_psk(machine_psk, agent.agent_key)

    # 旧 PSK 不能再认证注册（WS 认证与查询同理）
    old_psk_query = await client.get(
        f"/api/agent/bindings?agent_id={install_id}", headers={"X-Agent-Key": machine_psk}
    )
    assert old_psk_query.status_code == 401
    new_psk_query = await client.get(
        f"/api/agent/bindings?agent_id={install_id}", headers={"X-Agent-Key": new_psk}
    )
    assert new_psk_query.status_code == 200


# ---------- 并发互斥 ----------


async def test_release_and_worker_lock_never_double_occupy(client: AsyncClient):
    """release 与 Worker 抢锁并发：任何时刻同一设备最多被一项执行占用。"""
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    _agent_id, device_id = await _make_agent()

    from app.services.worker_service import _lock_device

    exec_a = await _make_execution("queued")
    async with SessionLocal() as db:
        locked = await _lock_device(db, device_id, exec_a)
    assert locked is True

    exec_b = await _make_execution("queued")
    # 设备已 busy+locked：第二个执行无法再抢占
    async with SessionLocal() as db:
        second = await _lock_device(db, device_id, exec_b)
    assert second is False

    # 管理员强制释放：无活动执行场景直接 released
    resp = await client.post(f"/api/devices/{device_id}/release", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["action"] == "released"

    # 释放后可再次被新执行占用
    exec_c = await _make_execution("queued")
    async with SessionLocal() as db:
        third = await _lock_device(db, device_id, exec_c)
    assert third is True
    async with SessionLocal() as db:
        device = await db.get(Device, device_id)
        assert device.locked_by_execution == exec_c
        assert device.status == "busy"


# ---------- Worker 恢复扫描（finalize_unfinished_terminal） ----------

from app.models import ExecutionCase, ExecutionSuite, Project  # noqa: E402
from app.services import worker_service  # noqa: E402


async def test_finalize_unfinished_terminal_recovers_summary(client: AsyncClient):
    await _admin_token(client)
    _agent_id, device_id = await _make_agent()
    # Agent 已回传终态（status/finished_at）但 finalized_at 为空（进程崩溃场景）
    async with SessionLocal() as db:
        project = Project(name="S6恢复项目", owner_id=await _project_owner_id(), visibility="private")
        db.add(project)
        await db.commit()
        await db.refresh(project)
        execution = Execution(project_id=project.id, type="case", status="passed")
        execution.device_id = device_id
        db.add(execution)
        await db.flush()
        db.add(
            ExecutionSuite(
                execution_id=execution.id,
                suite_id=None,
                suite_name="恢复虚拟套件",
                suite_order=1,
                is_virtual=True,
                status="passed",
                setup_steps_snapshot=[],
                teardown_steps_snapshot=[],
                elements_snapshot={},
            )
        )
        suite = await db.execute(
            select(ExecutionSuite).where(ExecutionSuite.execution_id == execution.id)
        )
        suite_id = suite.scalar_one().id
        db.add(
            ExecutionCase(
                execution_id=execution.id,
                execution_suite_id=suite_id,
                case_id=1,
                case_name="恢复用例",
                case_order=1,
                status="passed",
                # 断言下沉后 ExecutionCase 不再有 assertions_snapshot 列
                steps_snapshot=[],
            )
        )
        device = await db.get(Device, device_id)
        device.status = "busy"
        device.locked_by_execution = execution.id
        await db.commit()
        exec_id = execution.id

    async with SessionLocal() as db:
        await worker_service.finalize_unfinished_terminal(db)

    async with SessionLocal() as db:
        execution = await db.get(Execution, exec_id)
        assert execution.finalized_at is not None
        report = (await db.execute(select(Report).where(Report.execution_id == exec_id))).scalar_one_or_none()
        assert report is not None
        assert report.passed == 1
        device = await db.get(Device, device_id)
        assert device.locked_by_execution is None  # Worker 汇总释放设备
        assert device.status == "idle"

    # 恢复扫描幂等：第二次执行不再产生第二份汇总（finalized_at 已置）
    async with SessionLocal() as db:
        await worker_service.finalize_unfinished_terminal(db)
    async with SessionLocal() as db:
        reports = (await db.execute(select(Report).where(Report.execution_id == exec_id))).scalars().all()
        assert len(reports) == 1


async def test_mark_terminal_cas_single_finalizer(client: AsyncClient):
    """_mark_terminal 以 finalized_at is null 为唯一汇总 CAS：并发调用只汇总一次。"""
    from app.models import Project

    await _admin_token(client)  # 保证 owner 用户存在
    async with SessionLocal() as db:
        project = Project(name="S6并发项目", owner_id=await _project_owner_id(), visibility="private")
        db.add(project)
        await db.commit()
        await db.refresh(project)
        execution = Execution(project_id=project.id, type="case", status="running")
        db.add(execution)
        await db.commit()
        exec_id = execution.id

    async with SessionLocal() as db1:

        e1 = await db1.get(Execution, exec_id)
        await worker_service._mark_terminal(db1, e1, "error", "并发A")
    async with SessionLocal() as db2:
        e2 = await db2.get(Execution, exec_id)
        await worker_service._mark_terminal(db2, e2, "error", "并发B")

    async with SessionLocal() as db:
        reports = (await db.execute(select(Report).where(Report.execution_id == exec_id))).scalars().all()
        execution = await db.get(Execution, exec_id)
        assert execution.finalized_at is not None
        assert execution.status == "error"
        assert len(reports) == 1
