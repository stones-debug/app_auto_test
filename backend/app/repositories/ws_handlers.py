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
from app.services.execution_summary import merge_runtime_status
from app.services.screenshot_store import validate_object_key
from app.services.sensitive_snapshot import REDACTED
from app.services.worker_service import min_agent_version, protocol_version

TERMINAL_STATES = {"passed", "failed", "error", "stopped", "skipped", "cancelled"}
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


# 步骤/节点状态严重度：与 execution_summary._STATUS_PRIORITY 同口径
_STEP_STATUS_SEVERITY = ("error", "failed", "stopped", "skipped", "passed")


def _merge_step_status(current: str | None, incoming: str | None) -> str:
    """合并步骤/节点状态：已终态不被晚到或重投的消息回退。

    与 :func:`merge_runtime_status` 的区别：后者面向父节点聚合（``running``
    优先于 ``passed``，避免兄弟节点未跑完就定稿），而步骤/节点是叶子节点，
    收到终态就应当生效；这里只保证「终态不回退」。

    此前 step/node 直接 ``payload["status"]`` 赋值，缺少 case/suite 级
    :func:`_merge_terminal_status` 同款保护 —— 网络重投一条更优状态即可把
    已落库的 ``failed`` 翻成 ``passed``（V1.1 §10.14.6 晚到消息规则）。
    """
    current_norm = str(current or "").strip().lower()
    incoming_norm = str(incoming or "").strip().lower()
    if not incoming_norm:
        return current_norm
    if incoming_norm not in TERMINAL_STATES:
        # 非终态：已终态的步骤/节点保持终态，不被 running/pending 回退
        return current_norm if current_norm in TERMINAL_STATES else incoming_norm
    if current_norm not in TERMINAL_STATES:
        return incoming_norm
    # 双方均终态：取更严重者（cancelled 与 stopped 同属中断终态）
    left = "stopped" if current_norm == "cancelled" else current_norm
    right = "stopped" if incoming_norm == "cancelled" else incoming_norm
    for state in _STEP_STATUS_SEVERITY:
        if state in (left, right):
            return state
    return right


def _fill_if_present(target: object, field: str, value: object) -> None:
    """仅在 payload 明确提供该字段时覆盖。

    重投/补报的 step_result、node_result 通常不带截图与错误信息，直接赋值会把
    首次上报已固化的失败现场（截图、错误信息、实际值）清成 None，
    等于抹掉「执行失败自动截图」的证据。
    """
    if value is not None:
        setattr(target, field, value)


def _safe_result_value(value: object, paths: object) -> str | None:
    """Mask result fields when the prebuilt node/step path metadata is non-empty."""
    if value is None:
        return None
    return REDACTED if isinstance(paths, list) and paths else str(value)


# Agent 对断言的 pass/fail 与 passed/failed 两种写法都存在，比较严重度时统一
_ASSERTION_STATUS_ALIASES = {"pass": "passed", "fail": "failed"}


def _assertion_status_key(status: object) -> str:
    """把断言状态归一到 passed/failed 口径，仅用于比较，不改变落库写法。"""
    raw = str(status or "").strip().lower()
    return _ASSERTION_STATUS_ALIASES.get(raw, raw)


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
    sensitive_paths = existing.sensitive_parameter_paths or []
    existing.assertion_type = data.get("type") or data.get("assertion_type") or existing.assertion_type
    expected = data.get("expected") if data.get("expected") is not None else data.get("expected_value")
    if expected is not None:
        safe_expected = _safe_result_value(expected, sensitive_paths)
        existing.expected_value = str(safe_expected)
        data["expected"] = str(safe_expected)
    actual = data.get("actual") if data.get("actual") is not None else data.get("actual_value")
    if actual is not None:
        safe_actual = _safe_result_value(actual, sensitive_paths)
        existing.actual_value = str(safe_actual)
        data["actual"] = str(safe_actual)
    if data.get("status"):
        incoming = str(data["status"])
        # 与步骤/节点同口径：已终态的断言不被晚到/重投消息回退。
        # 落库仍保留 Agent 的原始写法（pass/fail 与 passed/failed 并存），
        # 只在比较严重度时归一，避免改动既有状态的取值口径。
        if _merge_step_status(_assertion_status_key(existing.status), _assertion_status_key(incoming)) == _assertion_status_key(incoming):
            existing.status = incoming
    if data.get("error_message") is not None:
        existing.error_message = str(
            _safe_result_value(data["error_message"], sensitive_paths)
        )
        data["error_message"] = existing.error_message
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
    execution = (
        await db.execute(
            select(Execution)
            .where(Execution.id == execution_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
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
    reported_protocol = payload.get("protocol_version")
    if reported_protocol is not None and reported_protocol != protocol_version():
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
    message = payload.get("message") or ""
    # The wire protocol has no node id for logs.  Avoid scanning the snapshot
    # (and avoid leaking a value from an old Agent) by using the execution-level
    # sensitivity marker as a conservative boundary.
    if execution.sensitive_variable_names:
        message = REDACTED
    log = ExecutionLog(
        execution_id=execution.id,
        level=payload.get("level") or "INFO",
        message=message,
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
    # 终态不回退 + 现场只在明确提供时覆盖（重投不得清空已落库的失败证据）
    step.status = _merge_step_status(step.status, payload.get("status"))
    if step.status in TERMINAL_STATES and step.finished_at is None:
        step.finished_at = now
    _fill_if_present(step, "duration", payload.get("duration"))
    _fill_if_present(
        step, "actual_value",
        _safe_result_value(payload.get("actual_value"), step.sensitive_parameter_paths),
    )
    _fill_if_present(
        step, "error_message",
        _safe_result_value(payload.get("error_message"), step.sensitive_parameter_paths),
    )
    if screenshot_path is not None:
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
        "actual_value": _safe_result_value(step.actual_value, step.sensitive_parameter_paths),
        "error_message": _safe_result_value(step.error_message, step.sensitive_parameter_paths),
        "screenshot_url": step.screenshot_path,
        "artifact_id": f"step:{step.id}" if step.screenshot_path else None,
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
    node.status = merge_runtime_status(node.status, "running")
    node.started_at = node.started_at or now
    if payload.get("attempt") is not None:
        node.attempt_count = max(node.attempt_count or 0, int(payload["attempt"]))
    if case is not None and case.status not in TERMINAL_STATES:
        case.status = merge_runtime_status(case.status, "running")
        case.started_at = case.started_at or node.started_at or now
    if suite is not None and suite.status not in TERMINAL_STATES:
        suite.status = merge_runtime_status(suite.status, "running")
        suite.started_at = suite.started_at or node.started_at or now
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
    # 无 status 时保持 fail-closed（按 error 处理），但已终态的节点不被回退
    node.status = _merge_step_status(node.status, payload.get("status") or "error")
    if node.status in TERMINAL_STATES and node.finished_at is None:
        node.finished_at = now
    _fill_if_present(node, "duration", payload.get("duration"))
    _fill_if_present(
        node, "actual_value",
        _safe_result_value(payload.get("actual_value"), node.sensitive_parameter_paths),
    )
    if payload.get("expected_value") is not None:
        node.expected_value = str(
            _safe_result_value(payload.get("expected_value"), node.sensitive_parameter_paths)
        )
    _fill_if_present(
        node, "error_message",
        _safe_result_value(payload.get("error_message"), node.sensitive_parameter_paths),
    )
    attempt_count = payload.get("attempt_count")
    if isinstance(attempt_count, int):
        node.attempt_count = attempt_count
    screenshot_path = payload.get("screenshot_path")
    if validate_object_key(execution_id, screenshot_path):
        node.screenshot_path = screenshot_path
    if case is not None:
        if case.started_at is None:
            case.started_at = node.started_at or now
        parent_status = node.status if node.status in {"failed", "error", "stopped", "skipped"} else "running"
        case.status = merge_runtime_status(case.status, parent_status)
        if node.kind == "assertion" and node.status in {"failed", "error"}:
            case.error_message = case.error_message or (
                "断言执行错误" if node.status == "error" else "断言失败"
            )
        elif node.kind == "action" and node.status in {"failed", "error"}:
            case.error_message = node.error_message
    if suite is not None and suite.status not in TERMINAL_STATES:
        parent_status = node.status if node.status in {"failed", "error", "stopped", "skipped"} else "running"
        suite.status = merge_runtime_status(suite.status, parent_status)
    await db.flush()
    return {
        "type": "node_result", "execution_id": execution_id, "execution_node_id": node.id,
        "execution_case_id": case.id if case else None, "execution_suite_id": suite.id if suite else None,
        "node_order": node.node_order, "kind": node.kind, "status": node.status,
        "duration": node.duration,
        "actual_value": _safe_result_value(node.actual_value, node.sensitive_parameter_paths),
        "expected_value": _safe_result_value(node.expected_value, node.sensitive_parameter_paths),
        "error_message": _safe_result_value(node.error_message, node.sensitive_parameter_paths),
        "attempt_count": node.attempt_count, "screenshot_url": node.screenshot_path,
        "artifact_id": f"node:{node.id}" if node.screenshot_path else None,
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
        execution_case.status = merge_runtime_status(execution_case.status, "failed")
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
        parent_case.status = (
            merge_runtime_status(parent_case.status, terminal)
            if parent_case.status in TERMINAL_STATES
            else terminal
        )
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
        parent_case.error_message = (
            REDACTED if execution.sensitive_variable_names else payload["error_message"]
        )
    await db.flush()
    return {
        "type": "case_status",
        "execution_id": execution_id,
        "execution_case_id": parent_case.id,
        "case_id": parent_case.case_id,
        "status": parent_case.status,
        "duration": parent_case.duration,
        "error_message": (
            REDACTED if execution.sensitive_variable_names and parent_case.error_message
            else parent_case.error_message
        ),
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
        suite.status = (
            merge_runtime_status(suite.status, terminal)
            if suite.status in TERMINAL_STATES
            else terminal
        )
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
        suite.error_message = (
            REDACTED if execution.sensitive_variable_names else payload["error_message"]
        )
    await db.flush()
    return {
        "type": "suite_status",
        "execution_id": suite.execution_id,
        "execution_suite_id": suite.id,
        "suite_id": suite.suite_id,
        "status": suite.status,
        "duration": suite.duration,
        "error_message": (
            REDACTED if execution.sensitive_variable_names and suite.error_message
            else suite.error_message
        ),
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
    suite_node_statuses = set(
        (
            await db.execute(
                select(ExecutionNode.status)
                .join(
                    ExecutionSuite,
                    ExecutionSuite.id == ExecutionNode.execution_suite_id,
                )
                .where(
                    ExecutionSuite.execution_id == execution_id,
                    ExecutionNode.execution_case_id.is_(None),
                    ExecutionNode.status.in_(("error", "failed", "stopped")),
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
        if candidate in suite_node_statuses:
            return candidate, f"{candidate} 套件节点"
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
    forced_termination_message: str | None = None
    if execution.status == "stopping" and execution.termination_reason == "timeout":
        # A timeout is an execution-level error even when Agent acknowledges
        # with stopped/passed after receiving stop_test.
        merged_status = "error"
        forced_termination_message = f"执行超时（>{execution.timeout_seconds}s）"
    elif execution.status == "stopping" and execution.termination_reason == "user_stop":
        # A user stop keeps its explicit stopped semantics despite an in-flight
        # Agent result racing the stop request.
        merged_status = "stopped"
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
    error_message = (
        REDACTED if execution.sensitive_variable_names and payload.get("error_message") else
        payload.get("error_message")
    )
    if forced_termination_message:
        db.add(
            ExecutionLog(
                execution_id=execution_id,
                level="ERROR",
                message=forced_termination_message,
                source="worker",
            )
        )
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
