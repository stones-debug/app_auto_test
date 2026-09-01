import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_psk
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
    Report,
)
from app.services.screenshot_store import validate_object_key
from app.services.worker_service import min_agent_version

TERMINAL_STATES = {"passed", "failed", "error", "stopped", "cancelled"}
WRITE_STATES = {"running", "stopping"}


def _find_snapshot_step(steps_snapshot: list, step_order: int) -> dict:
    """从不可变快照列表按 order/step_order 取步骤，保证执行参数按实际下发值落库。"""
    for item in steps_snapshot or []:
        if not isinstance(item, dict):
            continue
        order = item.get("order") or item.get("step_order")
        if order == step_order:
            return item
    return {}


async def _resolve_case(db: AsyncSession, execution: Execution, payload: dict) -> ExecutionCase | None:
    """仅按 execution_case_id 定位执行用例，并校验归属当前 execution。"""
    case_pk = payload.get("execution_case_id")
    if case_pk is None:
        return None
    case = await db.get(ExecutionCase, case_pk)
    if case is not None and case.execution_id == execution.id:
        return case
    return None


async def _resolve_suite(db: AsyncSession, execution: Execution, payload: dict) -> ExecutionSuite | None:
    """按 execution_suite_id 定位执行套件，并校验归属当前 execution。"""
    suite_pk = payload.get("execution_suite_id")
    if suite_pk is None:
        return None
    suite = await db.get(ExecutionSuite, suite_pk)
    if suite is not None and suite.execution_id == execution.id:
        return suite
    return None


async def _step_belongs_to(db: AsyncSession, step: ExecutionStep, execution_id: int) -> bool:
    """校验执行步骤的父节点（套件或用例）属于当前 execution，用于快照 ID 归属校验。"""
    if step.execution_case_id is not None:
        case = await db.get(ExecutionCase, step.execution_case_id)
        return case is not None and case.execution_id == execution_id
    if step.execution_suite_id is not None:
        suite = await db.get(ExecutionSuite, step.execution_suite_id)
        return suite is not None and suite.execution_id == execution_id
    return False


async def _locate_step(
    db: AsyncSession,
    execution: Execution,
    payload: dict,
) -> tuple[ExecutionStep | None, ExecutionCase | None, ExecutionSuite | None]:
    """仅按 execution_step_id 精确定位并校验归属。返回 (step, case, suite)；未命中返回 (None, None, None)。"""
    step_id = payload.get("execution_step_id")
    if step_id is None:
        return None, None, None
    step = await db.get(ExecutionStep, step_id)
    if step is not None and await _step_belongs_to(db, step, execution.id):
        if step.execution_case_id is not None:
            return step, await db.get(ExecutionCase, step.execution_case_id), None
        if step.execution_suite_id is not None:
            return step, None, await db.get(ExecutionSuite, step.execution_suite_id)
    return None, None, None


def _parse_terminal_status(status: str | None) -> str | None:
    """规范化状态：小写入态；terminal 才返回（供 case_status/suite_status 使用）。"""
    raw = str(status or "").lower()
    if raw in TERMINAL_STATES:
        return raw
    return None


# Step 7.2：Agent 上报终态与已落库分层终态按统一优先级合并（error > failed > stopped），
# 与权威口径 V1.1 §10.14 及 Worker 侧 `_aggregate_status` 一致。
def _merge_terminal_status(agent_status: str, stored_status: str | None) -> str:
    """合并 Agent 终态与服务端已落库终态：任一侧更严重时以更严重者为准。

    - Agent 上报 passed 不能覆盖已落库 error/failed/stopped；
    - Agent 上报 failed/stopped 也不能覆盖已落库 error；
    - cancelled 与 stopped 互相兼容（同属中断终态）。
    """
    agent_normalized = "stopped" if agent_status == "cancelled" else agent_status
    if agent_normalized not in ("passed", "failed", "error", "stopped"):
        agent_normalized = "error"
    stored_normalized = "stopped" if stored_status == "cancelled" else stored_status
    for state in ("error", "failed", "stopped"):
        if state in (agent_normalized, stored_normalized):
            return state
    return agent_normalized


async def _upsert_assertion(
    db: AsyncSession, execution_step_id: int, assertion_order: int, data: dict
) -> bool:
    """仅按 execution_assertion_id 更新预建断言，并严格校验步骤归属。"""
    assertion_id = data.get("execution_assertion_id")
    if assertion_id is None:
        return False
    existing = await db.get(ExecutionAssertion, assertion_id)
    if existing is None or existing.execution_step_id != execution_step_id:
        return False
    existing.assertion_type = data.get("type") or data.get("assertion_type") or existing.assertion_type
    expected = data.get("expected") if data.get("expected") is not None else data.get("expected_value")
    if expected is not None:
        existing.expected_value = str(expected)
    actual = data.get("actual") if data.get("actual") is not None else data.get("actual_value")
    if actual is not None:
        existing.actual_value = str(actual)
    if data.get("status"):
        existing.status = str(data["status"])
    if data.get("error_message") is not None:
        existing.error_message = data["error_message"]
    return True


async def _settle_execution_cases(
    db: AsyncSession,
    execution_id: int,
    execution_status: str,
    now: datetime,
) -> None:
    """Agent 终态提交时立即收敛用例状态，避免等待 Worker 汇总期间仍显示 running。"""
    cases = (
        await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == execution_id))
    ).scalars().all()
    for case in cases:
        if case.status not in {"pending", "running"}:
            continue
        had_started = case.status == "running"
        if execution_status == "passed":
            case.status = "passed"
        elif not had_started:
            case.status = "skipped"
        elif execution_status == "failed":
            case.status = "failed"
        elif execution_status in {"stopped", "cancelled"}:
            case.status = "stopped"
        else:
            case.status = "error"
        case.finished_at = now
        if case.started_at is not None:
            case.duration = int((now - case.started_at).total_seconds() * 1000)


async def _bound_execution(
    db: AsyncSession,
    agent_id: int,
    execution_id: int | None,
    session_token: str | None = None,
) -> Execution | None:
    """校验 Agent 回传的执行绑定关系（CR-05）。

    仅当 execution 的 device 属于当前 agent、状态为 running/stopping、
    且 session_token 匹配时返回 execution；否则返回 None（拒绝写入）。
    """
    if execution_id is None:
        return None
    execution = await db.get(Execution, execution_id)
    if execution is None:
        return None
    device = await db.get(Device, execution.device_id) if execution.device_id else None
    if device is None or device.agent_id != agent_id:
        return None
    if execution.session_token is not None and session_token != execution.session_token:
        return None
    if execution.status not in WRITE_STATES:
        return None
    return execution


async def mark_agent_offline(db: AsyncSession, agent_id: int) -> None:
    agent = await db.get(Agent, agent_id)
    if agent is not None:
        agent.status = "offline"
        await db.flush()


def version_supported(version: str | None, minimum: str) -> bool:
    """CR-21：Agent 注册语义化版本比较（major.minor.patch）。未上报版本宽松放行。"""
    if not version:
        return True

    def _parts(v: str) -> tuple[int, ...]:
        nums = [int(p) for p in re.split(r"[^\d]+", v.strip()) if p.isdigit()][:3]
        return tuple(nums) or (0,)

    return _parts(version) >= _parts(minimum)


async def handle_register(db: AsyncSession, payload: dict) -> dict | None:
    agent_id_str = payload.get("agent_id")
    agent_key = payload.get("agent_key")
    if not agent_id_str or not agent_key:
        return None
    agent = (
        await db.execute(select(Agent).where(Agent.agent_id == agent_id_str))
    ).scalar_one_or_none()
    if agent is None or agent.deleted_at is not None or not verify_psk(agent_key, agent.agent_key):
        # Step 6：软注销 Agent 不接受 WS 注册
        return None
    # CR-21：注册时语义化版本比较（min_agent_version，来自 Registry 生成产物）
    _min_agent = min_agent_version()
    if not version_supported(payload.get("version"), _min_agent):
        return None

    agent.status = "online"
    agent.last_heartbeat = datetime.now(UTC)
    if payload.get("hostname"):
        agent.hostname = payload["hostname"]
    if payload.get("platform"):
        agent.platform = payload["platform"]
    if payload.get("version"):
        agent.version = payload["version"]
    await db.flush()
    return {"type": "registered", "agent_id": agent.id, "status": "ok"}


async def handle_heartbeat(db: AsyncSession, agent_id: int, _payload: dict) -> None:
    agent = await db.get(Agent, agent_id)
    if agent is not None:
        agent.status = "online"
        agent.last_heartbeat = datetime.now(UTC)
        await db.flush()


async def handle_device_list(db: AsyncSession, agent_id: int, payload: dict) -> None:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        return
    existing = (
        await db.execute(select(Device).where(Device.agent_id == agent_id))
    ).scalars().all()
    by_udid = {d.udid: d for d in existing}
    for item in payload.get("devices") or []:
        udid = item.get("udid")
        if not udid:
            continue
        device = by_udid.get(udid)
        if device is None:
            device = Device(
                agent_id=agent_id,
                name=item.get("name") or udid,
                platform=item.get("platform") or "android",
                device_type=item.get("device_type") or "emulator",
                udid=udid,
                status=item.get("status") or "idle",
                connection_type=item.get("connection_type") or "usb",
                address=item.get("address"),
                capabilities=item.get("capabilities") or {},
                last_heartbeat=datetime.now(UTC),
            )
            db.add(device)
        else:
            # Windows 方案 §3.3：被执行锁定的设备保持 busy，不允许普通快照覆盖锁状态
            if device.status == "busy" and device.locked_by_execution is not None:
                continue
            device.status = item.get("status") or device.status
            if item.get("connection_type"):
                device.connection_type = item["connection_type"]
            if item.get("address"):
                device.address = item["address"]
            device.last_heartbeat = datetime.now(UTC)
    # CR-17：本次快照未出现且未锁定的设备标记 offline（拔出/离线）
    reported_udids = {
        item.get("udid") for item in payload.get("devices") or [] if item.get("udid")
    }
    for d in existing:
        if d.udid not in reported_udids and d.locked_by_execution is None:
            d.status = "offline"
            d.last_heartbeat = datetime.now(UTC)
    await db.flush()


async def handle_log(db: AsyncSession, agent_id: int, payload: dict) -> dict | None:
    execution_id = payload.get("execution_id")
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    log = ExecutionLog(
        execution_id=execution.id,
        level=payload.get("level") or "INFO",
        message=payload.get("message") or "",
        source="agent",
    )
    db.add(log)
    await db.flush()
    if execution_id:
        return {
            "type": "log",
            "execution_id": execution_id,
            "level": log.level,
            "message": log.message,
            "step_order": payload.get("step_order"),
            "timestamp": datetime.now(UTC).isoformat(),
        }


async def handle_step_result(db: AsyncSession, agent_id: int, payload: dict) -> dict | None:
    execution_id = payload.get("execution_id")
    if execution_id is None:
        return
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    screenshot_path = payload.get("screenshot_path")
    if not validate_object_key(execution_id, screenshot_path):
        screenshot_path = None
    now = datetime.now(UTC)
    # 协议 V2：只按 execution_step_id 精确路由，父节点一律由该行的预建归属确定；
    # 旧协议（无 execution_step_id、按 case_id/step_order 猜测）不再兼容。
    step, parent_case, parent_suite = await _locate_step(db, execution, payload)

    if step is None:
        db.add(
            ExecutionLog(
                execution_id=execution_id,
                level="WARN",
                message=(
                    f"step_result 未匹配预建步骤 "
                    f"execution_step_id={payload.get('execution_step_id')} execution_id={execution_id}"
                ),
                source="agent",
            )
        )
        await db.flush()
        return
    step_order = step.step_order

    # 阶段与参数都以服务端固化行/快照为准。
    exec_phase = step.phase

    # 从快照取下发参数（用例 steps_snapshot 或套件 setup/teardown snapshot）
    snapshot_parameters: dict = {}
    suite = parent_suite
    if parent_case is not None:
        snap = _find_snapshot_step(parent_case.steps_snapshot, step_order)
        params = snap.get("params")
        if isinstance(params, dict):
            snapshot_parameters = params
    else:
        if parent_suite is None:
            return
        suite = parent_suite
        snap = _find_snapshot_step(suite.setup_steps_snapshot, step_order)
        if not snap:
            snap = _find_snapshot_step(suite.teardown_steps_snapshot, step_order)
        params = snap.get("params")
        if isinstance(params, dict):
            snapshot_parameters = params
    if not isinstance(snapshot_parameters, dict):
        snapshot_parameters = {}

    if not step.parameters and snapshot_parameters:
        step.parameters = dict(snapshot_parameters)
    step.status = payload.get("status") or step.status
    step.finished_at = now
    step.duration = payload.get("duration")
    step.actual_value = payload.get("actual_value")
    step.error_message = payload.get("error_message")
    step.screenshot_path = screenshot_path
    await db.flush()

    if parent_case is not None:
        if parent_case.started_at is None:
            parent_case.started_at = now
        if step.status == "failed":
            parent_case.status = "failed"
            parent_case.finished_at = now
        elif parent_case.status not in TERMINAL_STATES:
            # Step 7.2：已终态的用例不被迟到的步骤结果回退为 running
            parent_case.status = "running"
        parent_status = parent_case.status
    else:
        if parent_suite is None:
            return
        suite = parent_suite
        if suite.started_at is None:
            suite.started_at = now
        if step.status == "failed":
            suite.status = "failed"
        elif suite.status not in TERMINAL_STATES:
            suite.status = "running"
        parent_status = suite.status

    await db.flush()

    return {
        "type": "step_result",
        "execution_id": execution_id,
        "case_id": parent_case.case_id if parent_case else None,
        "execution_case_id": parent_case.id if parent_case else None,
        "execution_suite_id": parent_suite.id if parent_suite else None,
        "execution_step_id": step.id,
        "step_order": step_order,
        "phase": exec_phase,
        "status": step.status,
        "case_status": parent_status,
        "duration": step.duration,
        "actual_value": step.actual_value,
        "error_message": step.error_message,
        "screenshot_url": step.screenshot_path,
        "artifact_id": step.id if step.screenshot_path else None,
        "timestamp": now.isoformat(),
    }


async def _locate_node(db: AsyncSession, execution: Execution, node_id: int):
    node = await db.get(ExecutionNode, node_id)
    if node is None:
        return None, None, None
    if node.execution_case_id is not None:
        case = await db.get(ExecutionCase, node.execution_case_id)
        if case is None or case.execution_id != execution.id:
            return None, None, None
        suite = await db.get(ExecutionSuite, case.execution_suite_id)
        return node, case, suite
    if node.execution_suite_id is not None:
        suite = await db.get(ExecutionSuite, node.execution_suite_id)
        if suite is None or suite.execution_id != execution.id:
            return None, None, None
        return node, None, suite
    return None, None, None


async def handle_node_started(db: AsyncSession, agent_id: int, payload: dict) -> dict | None:
    execution_id = payload.get("execution_id")
    node_id = payload.get("execution_node_id")
    if execution_id is None or node_id is None:
        return
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    node, case, suite = await _locate_node(db, execution, node_id)
    if node is None:
        return
    now = datetime.now(UTC)
    node.status = "running"
    node.started_at = node.started_at or now
    if payload.get("attempt") is not None:
        node.attempt_count = max(node.attempt_count or 0, int(payload["attempt"]))
    await db.flush()
    return {
        "type": "node_started", "execution_id": execution_id, "execution_node_id": node.id,
        "execution_case_id": case.id if case else None, "execution_suite_id": suite.id if suite else None,
        "node_order": node.node_order, "kind": node.kind, "timestamp": now.isoformat(),
    }


async def handle_node_result(db: AsyncSession, agent_id: int, payload: dict) -> dict | None:
    execution_id = payload.get("execution_id")
    node_id = payload.get("execution_node_id")
    if execution_id is None or node_id is None:
        return
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    node, case, suite = await _locate_node(db, execution, node_id)
    if node is None:
        return
    now = datetime.now(UTC)
    node.status = payload.get("status") or "error"
    node.finished_at = now
    node.duration = payload.get("duration")
    node.actual_value = payload.get("actual_value")
    node.expected_value = payload.get("expected_value") or node.expected_value
    node.error_message = payload.get("error_message")
    attempt_count = payload.get("attempt_count")
    if isinstance(attempt_count, int):
        node.attempt_count = attempt_count
    screenshot_path = payload.get("screenshot_path")
    node.screenshot_path = screenshot_path if validate_object_key(execution_id, screenshot_path) else None
    if case is not None:
        if case.started_at is None:
            case.started_at = node.started_at or now
        if node.kind == "assertion" and node.status == "failed":
            case.status = "failed"
            case.error_message = case.error_message or "断言失败"
        elif node.kind == "action" and node.status in {"failed", "error"} and not node.continue_on_failure:
            case.status = node.status
            case.error_message = node.error_message
        elif case.status not in TERMINAL_STATES:
            case.status = "running"
    if suite is not None and suite.status not in TERMINAL_STATES:
        if node.status in {"failed", "error"} and not node.continue_on_failure:
            suite.status = node.status
        else:
            suite.status = "running"
    await db.flush()
    return {
        "type": "node_result", "execution_id": execution_id, "execution_node_id": node.id,
        "execution_case_id": case.id if case else None, "execution_suite_id": suite.id if suite else None,
        "node_order": node.node_order, "kind": node.kind, "status": node.status,
        "duration": node.duration, "actual_value": node.actual_value,
        "expected_value": node.expected_value, "error_message": node.error_message,
        "attempt_count": node.attempt_count, "screenshot_url": node.screenshot_path,
        "timestamp": now.isoformat(),
    }


async def handle_assertion_result(db: AsyncSession, agent_id: int, payload: dict) -> dict | None:
    execution_id = payload.get("execution_id")
    if execution_id is None:
        return
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    step, execution_case, _suite = await _locate_step(db, execution, payload)
    if step is None or execution_case is None:
        return
    normalized_assertions: list[dict] = []
    for order, assertion in enumerate(payload.get("assertions") or [], start=1):
        assertion_order = assertion.get("assertion_order") or order
        raw_status = str(assertion.get("status") or "fail").lower()
        normalized = {
            **assertion,
            "assertion_order": assertion_order,
            # DB/前端协议统一使用 pass/fail；兼容旧 Agent 的 passed/failed。
            "status": "pass" if raw_status in {"pass", "passed"} else "fail",
        }
        matched = await _upsert_assertion(
            db, step.id, assertion_order, normalized
        )
        if not matched:
            db.add(
                ExecutionLog(
                    execution_id=execution_id,
                    level="WARN",
                    message=(
                        "assertion_result 未匹配预建断言 "
                        f"execution_assertion_id={assertion.get('execution_assertion_id')}"
                    ),
                    source="agent",
                )
            )
            continue
        normalized_assertions.append(normalized)
    if not normalized_assertions and payload.get("assertions"):
        await db.flush()
        return
    if any(item["status"] == "fail" for item in normalized_assertions):
        step.status = "failed"
        step.error_message = step.error_message or "步骤后断言失败"
        execution_case.status = "failed"
    now = datetime.now(UTC)
    await db.flush()
    # V2 §8.2：assertion 广播（前端按 execution_id+case_id+assertions 幂等合并）
    return {
        "type": "assertion_result",
        "execution_id": execution_id,
        "case_id": execution_case.case_id,
        "execution_case_id": execution_case.id,
        "execution_step_id": step.id,
        "step_order": payload.get("step_order"),
        "assertions": normalized_assertions,
        "timestamp": now.isoformat(),
    }


async def handle_case_status(db: AsyncSession, agent_id: int, payload: dict) -> dict | None:
    """协议 V2：用例级状态更新（{execution_case_id, status: running/terminal, error_message?}）。"""
    execution_id = payload.get("execution_id")
    case_pk = payload.get("execution_case_id")
    if execution_id is None and case_pk is None:
        return
    if execution_id is None:
        case = await db.get(ExecutionCase, case_pk)
        if case is None:
            return
        execution_id = case.execution_id
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    parent_case = await _resolve_case(db, execution, payload)
    if parent_case is None:
        return
    now = datetime.now(UTC)
    terminal = _parse_terminal_status(payload.get("status"))
    if terminal is not None:
        parent_case.status = terminal
        parent_case.finished_at = now
        if parent_case.started_at is not None:
            parent_case.duration = int((now - parent_case.started_at).total_seconds() * 1000)
    elif parent_case.status in TERMINAL_STATES:
        # Step 7.2：终态节点不被迟到的 running 消息回退（状态转换白名单：
        # running 仅允许从 pending/running 进入）
        return
    else:
        parent_case.status = "running"
        if parent_case.started_at is None:
            parent_case.started_at = now
    if payload.get("error_message") is not None:
        parent_case.error_message = payload["error_message"]
    await db.flush()
    return {
        "type": "case_status",
        "execution_id": execution_id,
        "execution_case_id": parent_case.id,
        "case_id": parent_case.case_id,
        "status": parent_case.status,
        "duration": parent_case.duration,
        "error_message": parent_case.error_message,
        "timestamp": now.isoformat(),
    }


async def handle_suite_status(db: AsyncSession, agent_id: int, payload: dict) -> dict | None:
    """协议 V2：套件级状态更新（{execution_suite_id, status, error_message?}）。"""
    suite_pk = payload.get("execution_suite_id")
    if suite_pk is None:
        return
    suite = await db.get(ExecutionSuite, suite_pk)
    if suite is None:
        return
    execution = await _bound_execution(db, agent_id, suite.execution_id, payload.get("session_token"))
    if execution is None:
        return
    now = datetime.now(UTC)
    terminal = _parse_terminal_status(payload.get("status"))
    if terminal is not None:
        suite.status = terminal
        suite.finished_at = now
        if suite.started_at is not None:
            suite.duration = int((now - suite.started_at).total_seconds() * 1000)
    elif suite.status in TERMINAL_STATES:
        # Step 7.2：终态节点不被迟到的 running 消息回退
        return
    else:
        suite.status = "running"
        if suite.started_at is None:
            suite.started_at = now
    if payload.get("error_message") is not None:
        suite.error_message = payload["error_message"]
    await db.flush()
    return {
        "type": "suite_status",
        "execution_id": suite.execution_id,
        "execution_suite_id": suite.id,
        "suite_id": suite.suite_id,
        "status": suite.status,
        "duration": suite.duration,
        "error_message": suite.error_message,
        "timestamp": now.isoformat(),
    }


async def _stored_execution_terminal(
    db: AsyncSession, execution_id: int
) -> tuple[str | None, str | None]:
    """返回已落库分层结果中阻止执行通过的最高优先级终态及来源。"""
    suite_statuses = set(
        (
            await db.execute(
                select(ExecutionSuite.status).where(
                    ExecutionSuite.execution_id == execution_id,
                    ExecutionSuite.status.in_(("error", "failed", "stopped")),
                )
            )
        ).scalars()
    )
    case_statuses = set(
        (
            await db.execute(
                select(ExecutionCase.status).where(
                    ExecutionCase.execution_id == execution_id,
                    ExecutionCase.status.in_(("error", "failed", "stopped")),
                )
            )
        ).scalars()
    )
    suite_step_statuses = set(
        (
            await db.execute(
                select(ExecutionStep.status)
                .join(
                    ExecutionSuite,
                    ExecutionSuite.id == ExecutionStep.execution_suite_id,
                )
                .where(
                    ExecutionSuite.execution_id == execution_id,
                    ExecutionStep.execution_case_id.is_(None),
                    ExecutionStep.status.in_(("error", "failed", "stopped")),
                )
            )
        ).scalars()
    )
    for candidate in ("error", "failed", "stopped"):
        if candidate in suite_statuses:
            return candidate, f"{candidate} 套件"
        if candidate in case_statuses:
            return candidate, f"{candidate} 用例"
        if candidate in suite_step_statuses:
            return candidate, f"{candidate} 套件步骤"
    return None, None


async def handle_execution_result(db: AsyncSession, agent_id: int, payload: dict) -> dict | bool:
    execution_id = payload.get("execution_id")
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        # ACK 可能在服务端提交终态后、到达 Agent 前断线。Agent 重连会重放，
        # 对同一设备/会话且已是终态的结果视为幂等成功并再次确认。
        settled = await db.get(Execution, execution_id) if execution_id is not None else None
        device = await db.get(Device, settled.device_id) if settled is not None and settled.device_id else None
        same_session = settled is not None and (
            settled.session_token is None or settled.session_token == payload.get("session_token")
        )
        return bool(
            settled is not None
            and device is not None
            and device.agent_id == agent_id
            and same_session
            and settled.status in TERMINAL_STATES
        )
    status = (payload.get("status") or "error").lower()
    if status not in TERMINAL_STATES:
        status = "error"
    # Step 7.2：服务端以已落库的分层结果为准，Agent 上报任何终态（含 failed/stopped）
    # 都与已落库终态按统一优先级合并——不能让迟到的 passed 覆盖失败，
    # 也不能让 Agent 的 failed/stopped 覆盖已落库的 error。
    stored_status, failure_source = await _stored_execution_terminal(db, execution.id)
    merged_status = _merge_terminal_status(status, stored_status)
    if merged_status != status:
        db.add(
            ExecutionLog(
                execution_id=execution_id,
                level="WARN",
                message=(
                    f"Agent 上报 {status}，服务端已记录{failure_source or '更高优先级终态'}，"
                    f"终态已修正为 {merged_status}"
                ),
                source="worker",
            )
        )
    status = merged_status
    now = datetime.now(UTC)
    execution.status = status
    execution.finished_at = now
    if execution.started_at is not None:
        execution.duration = int((now - execution.started_at).total_seconds() * 1000)
    await _settle_execution_cases(db, execution.id, status, now)
    error_message = payload.get("error_message")
    if error_message:
        db.add(
            ExecutionLog(
                execution_id=execution_id,
                level="ERROR",
                message=str(error_message),
                source="agent",
            )
        )
    await db.flush()
    # V2 §8.2：completed 携带 report_id（若已生成）
    report = (
        await db.execute(select(Report).where(Report.execution_id == execution_id))
    ).scalar_one_or_none()
    return {
        "ack": True,
        "event": {
            "type": "completed",
            "execution_id": execution_id,
            "status": status,
            "report_id": report.id if report else None,
            "timestamp": now.isoformat(),
        },
    }
