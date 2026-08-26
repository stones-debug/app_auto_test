import re
from datetime import UTC, datetime

from fastapi import WebSocket
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
    ExecutionStep,
    ExecutionSuite,
    Report,
)
from app.services.screenshot_store import validate_object_key
from app.services.worker_service import min_agent_version
from app.ws.managers import agent_manager, execution_manager

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


_CASE_PHASE_MAP = {"setup": "case_setup", "main": "case_main", "teardown": "case_teardown"}


def _case_phase_to_exec(phase: str | None) -> str:
    return _CASE_PHASE_MAP.get(str(phase or "main"), "case_main")


async def _resolve_case(db: AsyncSession, execution: Execution, payload: dict) -> ExecutionCase | None:
    """按 execution_case_id 优先、fallback 旧协议 case_id（TestCase id）定位执行用例，并校验归属当前 execution。"""
    case_pk = payload.get("execution_case_id")
    if case_pk is not None:
        case = await db.get(ExecutionCase, case_pk)
        if case is not None and case.execution_id == execution.id:
            return case
    legacy_case_id = payload.get("case_id")
    if legacy_case_id is not None:
        # 同一 case_id 可在多套件出现，用 first() 避免歧义报错（V2 请携带 execution_case_id 精确匹配）
        case = (
            await db.execute(
                select(ExecutionCase)
                .where(
                    ExecutionCase.execution_id == execution.id,
                    ExecutionCase.case_id == legacy_case_id,
                )
                .limit(1)
            )
        ).scalars().first()
        if case is not None:
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
    parent_case: ExecutionCase | None,
    parent_suite: ExecutionSuite | None,
) -> tuple[ExecutionStep | None, ExecutionCase | None, ExecutionSuite | None]:
    """定位执行步骤：优先 execution_step_id 精确匹配（校验归属），
    其次按 (父节点, step_order) 兼容定位。返回 (step, case, suite)。"""
    step_id = payload.get("execution_step_id")
    if step_id is not None:
        step = await db.get(ExecutionStep, step_id)
        if step is not None and await _step_belongs_to(db, step, execution.id):
            if step.execution_case_id is not None:
                return step, await db.get(ExecutionCase, step.execution_case_id), None
            if step.execution_suite_id is not None:
                return step, None, await db.get(ExecutionSuite, step.execution_suite_id)
            return step, parent_case, parent_suite
    step_order = payload.get("step_order")
    if step_order is not None:
        if parent_case is not None:
            step = (
                await db.execute(
                    select(ExecutionStep).where(
                        ExecutionStep.execution_case_id == parent_case.id,
                        ExecutionStep.step_order == step_order,
                    )
                )
            ).scalar_one_or_none()
            return step, parent_case, None
        if parent_suite is not None:
            step = (
                await db.execute(
                    select(ExecutionStep).where(
                        ExecutionStep.execution_suite_id == parent_suite.id,
                        ExecutionStep.step_order == step_order,
                    )
                )
            ).scalar_one_or_none()
            return step, None, parent_suite
    return None, parent_case, parent_suite


def _parse_terminal_status(status: str | None) -> str | None:
    """规范化状态：小写入态；terminal 才返回（供 case_status/suite_status 使用）。"""
    raw = str(status or "").lower()
    if raw in TERMINAL_STATES:
        return raw
    return None


async def _upsert_assertion(
    db: AsyncSession, execution_case_id: int, assertion_order: int, data: dict
) -> None:
    """按 execution_assertion_id 优先、fallback (execution_case_id, assertion_order) 更新或创建执行断言。

    §3.1：快照预建 pending（S1 固化），Agent 按 execution_assertion_id 上报后更新；
    旧 Agent / 兼容路径无预建行或未带 id 时，回退按 assertion_order 定位或插入。
    """
    assertion_id = data.get("execution_assertion_id")
    existing: ExecutionAssertion | None = None
    if assertion_id is not None:
        candidate = await db.get(ExecutionAssertion, assertion_id)
        # 归属校验：id 必须属于当前用例，否则视为不匹配（拒绝越权写入）
        if candidate is not None and candidate.execution_case_id == execution_case_id:
            existing = candidate
            if assertion_order is None:
                assertion_order = candidate.assertion_order
    if existing is None and assertion_order is not None:
        existing = (
            await db.execute(
                select(ExecutionAssertion).where(
                    ExecutionAssertion.execution_case_id == execution_case_id,
                    ExecutionAssertion.assertion_order == assertion_order,
                )
            )
        ).scalar_one_or_none()
    if existing is not None:
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
        return
    expected = data.get("expected") if data.get("expected") is not None else data.get("expected_value")
    actual = data.get("actual") if data.get("actual") is not None else data.get("actual_value")
    db.add(
        ExecutionAssertion(
            execution_case_id=execution_case_id,
            assertion_order=assertion_order or 1,
            assertion_type=data.get("type") or data.get("assertion_type") or "",
            expected_value=str(expected) if expected is not None else None,
            actual_value=str(actual) if actual is not None else None,
            status=data.get("status") or "pass",
            error_message=data.get("error_message"),
        )
    )


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
        await db.commit()


def version_supported(version: str | None, minimum: str) -> bool:
    """CR-21：Agent 注册语义化版本比较（major.minor.patch）。未上报版本宽松放行。"""
    if not version:
        return True

    def _parts(v: str) -> tuple[int, ...]:
        nums = [int(p) for p in re.split(r"[^\d]+", v.strip()) if p.isdigit()][:3]
        return tuple(nums) or (0,)

    return _parts(version) >= _parts(minimum)


async def handle_register(db: AsyncSession, ws: WebSocket, payload: dict) -> dict | None:
    agent_id_str = payload.get("agent_id")
    agent_key = payload.get("agent_key")
    if not agent_id_str or not agent_key:
        await ws.close(code=1008, reason="缺少 agent_id 或 agent_key")
        return None
    agent = (
        await db.execute(select(Agent).where(Agent.agent_id == agent_id_str))
    ).scalar_one_or_none()
    if agent is None or agent.deleted_at is not None or not verify_psk(agent_key, agent.agent_key):
        # Step 6：软注销 Agent 不接受 WS 注册
        await ws.close(code=1008, reason="Agent 认证失败")
        return None
    # CR-21：注册时语义化版本比较（min_agent_version，来自 Registry 生成产物）
    _min_agent = min_agent_version()
    if not version_supported(payload.get("version"), _min_agent):
        await ws.close(
            code=1008,
            reason=f"Agent 版本过低，最低要求 {_min_agent}",
        )
        return None

    agent.status = "online"
    agent.last_heartbeat = datetime.now(UTC)
    if payload.get("hostname"):
        agent.hostname = payload["hostname"]
    if payload.get("platform"):
        agent.platform = payload["platform"]
    if payload.get("version"):
        agent.version = payload["version"]
    await db.commit()
    await agent_manager.connect(agent.id, ws)
    return {"type": "registered", "agent_id": agent.id, "status": "ok"}


async def handle_heartbeat(db: AsyncSession, agent_id: int, _payload: dict) -> None:
    agent = await db.get(Agent, agent_id)
    if agent is not None:
        agent.status = "online"
        agent.last_heartbeat = datetime.now(UTC)
        await db.commit()


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
    await db.commit()


async def handle_log(db: AsyncSession, agent_id: int, payload: dict) -> None:
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
    await db.commit()
    if execution_id:
        await execution_manager.broadcast(
            execution_id,
            {
                "type": "log",
                "execution_id": execution_id,
                "level": log.level,
                "message": log.message,
                "step_order": payload.get("step_order"),
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )


async def handle_step_result(db: AsyncSession, agent_id: int, payload: dict) -> None:
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
    step_order = payload.get("step_order")

    # 协议 V2：按 execution_case_id / execution_suite_id 确定父节点（兼容旧 case_id）
    parent_case = await _resolve_case(db, execution, payload)
    parent_suite = None if parent_case is not None else await _resolve_suite(db, execution, payload)

    # 定位执行步骤：优先 execution_step_id（S1 已预建 pending 行），其次 (父节点, step_order)
    step, parent_case, parent_suite = await _locate_step(
        db, execution, payload, parent_case, parent_suite
    )

    if parent_case is None and parent_suite is None:
        db.add(
            ExecutionLog(
                execution_id=execution_id,
                level="WARN",
                message=f"step_result 未匹配执行节点 execution_id={execution_id}",
                source="agent",
            )
        )
        await db.commit()
        return

    # 确定执行阶段（套件步 / 用例步）：已定位到行则以行 phase 为准（S1 固化），否则按 payload 推断
    if step is not None:
        exec_phase = step.phase
    elif parent_case is not None:
        exec_phase = _case_phase_to_exec(payload.get("phase"))
    else:
        exec_phase = (
            payload.get("phase")
            if payload.get("phase") in ("suite_setup", "suite_teardown")
            else "suite_setup"
        )

    # 从快照取下发参数（用例 steps_snapshot 或套件 setup/teardown snapshot）
    snapshot_parameters: dict = {}
    if parent_case is not None:
        snap = _find_snapshot_step(parent_case.steps_snapshot, step_order)
        snapshot_parameters = snap.get("params")
    else:
        snap = _find_snapshot_step(parent_suite.setup_steps_snapshot, step_order)
        if not snap:
            snap = _find_snapshot_step(parent_suite.teardown_steps_snapshot, step_order)
        snapshot_parameters = snap.get("params")
    if not isinstance(snapshot_parameters, dict):
        snapshot_parameters = {}

    if step is None:
        if step_order is None:
            db.add(
                ExecutionLog(
                    execution_id=execution_id,
                    level="WARN",
                    message=f"step_result 缺 step_order 无法创建步骤 execution_id={execution_id}",
                    source="agent",
                )
            )
            await db.commit()
            return
        step = ExecutionStep(
            execution_case_id=parent_case.id if parent_case else None,
            execution_suite_id=parent_suite.id if parent_suite else None,
            phase=exec_phase,
            step_order=step_order,
            action=payload.get("action") or snap.get("action") or "unknown",
            parameters=dict(snapshot_parameters),
            status=payload.get("status") or "passed",
            started_at=now,
            finished_at=now,
            duration=payload.get("duration"),
            actual_value=payload.get("actual_value"),
            error_message=payload.get("error_message"),
            screenshot_path=screenshot_path,
        )
        db.add(step)
    else:
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
        elif parent_case.status != "failed":
            parent_case.status = "running"
    else:
        if parent_suite.started_at is None:
            parent_suite.started_at = now
        if step.status == "failed":
            parent_suite.status = "failed"
        elif parent_suite.status != "failed":
            parent_suite.status = "running"

    for order, assertion in enumerate(payload.get("assertions") or [], start=1):
        if parent_case is not None:
            await _upsert_assertion(db, parent_case.id, order, assertion)
    await db.commit()

    await execution_manager.broadcast(
        execution_id,
        {
            "type": "step_result",
            "execution_id": execution_id,
            "case_id": parent_case.case_id if parent_case else None,
            "execution_case_id": parent_case.id if parent_case else None,
            "execution_suite_id": parent_suite.id if parent_suite else None,
            "execution_step_id": step.id,
            "step_order": step_order,
            "phase": exec_phase,
            "status": step.status,
            "case_status": parent_case.status if parent_case else parent_suite.status,
            "duration": step.duration,
            "screenshot_url": step.screenshot_path,
            "artifact_id": step.id if step.screenshot_path else None,
            "timestamp": now.isoformat(),
        },
    )


async def handle_assertion_result(db: AsyncSession, agent_id: int, payload: dict) -> None:
    execution_id = payload.get("execution_id")
    if execution_id is None:
        return
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    execution_case = await _resolve_case(db, execution, payload)
    if execution_case is None:
        return
    normalized_assertions: list[dict] = []
    for order, assertion in enumerate(payload.get("assertions") or [], start=1):
        raw_status = str(assertion.get("status") or "fail").lower()
        normalized = {
            **assertion,
            # DB/前端协议统一使用 pass/fail；兼容旧 Agent 的 passed/failed。
            "status": "pass" if raw_status in {"pass", "passed"} else "fail",
        }
        normalized_assertions.append(normalized)
        # 协议 V2：优先按 execution_assertion_id 更新（S1 已固化 pending），兼容按 assertion_order 定位
        await _upsert_assertion(
            db, execution_case.id, assertion.get("assertion_order") or order, normalized
        )
    assertion_failed = any(item["status"] == "fail" for item in normalized_assertions)
    if execution_case.status != "failed":
        execution_case.status = "failed" if assertion_failed else "passed"
    now = datetime.now(UTC)
    execution_case.finished_at = now
    if execution_case.started_at is not None:
        execution_case.duration = int((now - execution_case.started_at).total_seconds() * 1000)
    await db.commit()
    # V2 §8.2：assertion 广播（前端按 execution_id+case_id+assertions 幂等合并）
    await execution_manager.broadcast(
        execution_id,
        {
            "type": "assertion_result",
            "execution_id": execution_id,
            "case_id": execution_case.case_id,
            "execution_case_id": execution_case.id,
            "step_order": payload.get("step_order"),
            "assertions": normalized_assertions,
            "case_status": execution_case.status,
            "timestamp": now.isoformat(),
        },
    )


async def handle_case_status(db: AsyncSession, agent_id: int, payload: dict) -> None:
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
    else:
        parent_case.status = "running"
        if parent_case.started_at is None:
            parent_case.started_at = now
    if payload.get("error_message") is not None:
        parent_case.error_message = payload["error_message"]
    await db.commit()
    await execution_manager.broadcast(
        execution_id,
        {
            "type": "case_status",
            "execution_id": execution_id,
            "execution_case_id": parent_case.id,
            "case_id": parent_case.case_id,
            "status": parent_case.status,
            "timestamp": now.isoformat(),
        },
    )


async def handle_suite_status(db: AsyncSession, agent_id: int, payload: dict) -> None:
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
    else:
        suite.status = "running"
        if suite.started_at is None:
            suite.started_at = now
    if payload.get("error_message") is not None:
        suite.error_message = payload["error_message"]
    await db.commit()
    await execution_manager.broadcast(
        suite.execution_id,
        {
            "type": "suite_status",
            "execution_id": suite.execution_id,
            "execution_suite_id": suite.id,
            "suite_id": suite.suite_id,
            "status": suite.status,
            "timestamp": now.isoformat(),
        },
    )


async def handle_execution_result(db: AsyncSession, agent_id: int, payload: dict) -> None:
    execution_id = payload.get("execution_id")
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    status = (payload.get("status") or "error").lower()
    if status not in TERMINAL_STATES:
        status = "error"
    # 服务端以已落库的用例结果为准，不能让迟到、旧版本或异常 Agent 的 passed
    # 覆盖断言/步骤已经判定的失败状态。
    if status == "passed":
        failed_case_id = await db.scalar(
            select(ExecutionCase.id)
            .where(
                ExecutionCase.execution_id == execution.id,
                ExecutionCase.status == "failed",
            )
            .limit(1)
        )
        if failed_case_id is not None:
            status = "failed"
            db.add(
                ExecutionLog(
                    execution_id=execution_id,
                    level="WARN",
                    message="Agent 上报执行通过，但服务端已记录失败用例，终态已修正为 failed",
                    source="worker",
                )
            )
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
    await db.commit()
    # V2 §8.2：completed 携带 report_id（若已生成）
    report = (
        await db.execute(select(Report).where(Report.execution_id == execution_id))
    ).scalar_one_or_none()
    await execution_manager.broadcast(
        execution_id,
        {
            "type": "completed",
            "execution_id": execution_id,
            "status": status,
            "report_id": report.id if report else None,
            "timestamp": now.isoformat(),
        },
    )
