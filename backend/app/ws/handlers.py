import re
from datetime import UTC, datetime

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_psk
from app.models import (
    Agent,
    Device,
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionLog,
    ExecutionStep,
    Report,
)
from app.services.screenshot_store import validate_object_key
from app.ws.managers import agent_manager, execution_manager

TERMINAL_STATES = {"passed", "failed", "error", "stopped", "cancelled"}
WRITE_STATES = {"running", "stopping"}


def _step_snapshot(execution_case: ExecutionCase, step_order: int) -> dict:
    """从不可变用例快照取步骤，保证执行参数按实际下发值落库。"""
    for item in execution_case.steps_snapshot or []:
        if not isinstance(item, dict):
            continue
        order = item.get("order") or item.get("step_order")
        if order == step_order:
            return item
    return {}


_CASE_PHASE_MAP = {"setup": "case_setup", "main": "case_main", "teardown": "case_teardown"}


def _case_phase_to_exec(phase: str | None) -> str:
    return _CASE_PHASE_MAP.get(str(phase or "main"), "case_main")


async def _upsert_assertion(
    db: AsyncSession, execution_case_id: int, assertion_order: int, data: dict
) -> None:
    """按 (execution_case_id, assertion_order) 更新或创建执行断言。

    §3.1：快照预建 pending，Agent 按 id 上报后更新；旧 Agent / 兼容路径无预建行时插入。
    """
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
            assertion_order=assertion_order,
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
    # CR-21：注册时语义化版本比较（min_agent_version）
    if not version_supported(payload.get("version"), settings.min_agent_version):
        await ws.close(
            code=1008,
            reason=f"Agent 版本过低，最低要求 {settings.min_agent_version}",
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
    case_id = payload.get("case_id")
    step_order = payload.get("step_order")
    if execution_id is None or case_id is None or step_order is None:
        return
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    screenshot_path = payload.get("screenshot_path")
    if not validate_object_key(execution_id, screenshot_path):
        screenshot_path = None
    execution_case = (
        await db.execute(
            select(ExecutionCase).where(
                ExecutionCase.execution_id == execution_id,
                ExecutionCase.case_id == case_id,
            )
        )
    ).scalar_one_or_none()
    if execution_case is None:
        db.add(
            ExecutionLog(
                execution_id=execution_id,
                level="WARN",
                message=f"step_result 未匹配 execution_case (case_id={case_id})",
                source="agent",
            )
        )
        await db.commit()
        return

    step = (
        await db.execute(
            select(ExecutionStep).where(
                ExecutionStep.execution_case_id == execution_case.id,
                ExecutionStep.step_order == step_order,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    snapshot = _step_snapshot(execution_case, step_order)
    snapshot_parameters = snapshot.get("params")
    if not isinstance(snapshot_parameters, dict):
        snapshot_parameters = {}
    if step is None:
        step = ExecutionStep(
            execution_case_id=execution_case.id,
            phase=_case_phase_to_exec(snapshot.get("phase") or "main"),
            step_order=step_order,
            action=payload.get("action") or snapshot.get("action") or "unknown",
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

    if execution_case.started_at is None:
        execution_case.started_at = now
    if step.status == "failed":
        execution_case.status = "failed"
        execution_case.finished_at = now
    elif execution_case.status != "failed":
        execution_case.status = "running"

    for order, assertion in enumerate(payload.get("assertions") or [], start=1):
        await _upsert_assertion(db, execution_case.id, order, assertion)
    await db.commit()

    await execution_manager.broadcast(
        execution_id,
        {
            "type": "step_result",
            "execution_id": execution_id,
            "case_id": case_id,
            "step_order": step_order,
            "phase": snapshot.get("phase") or "main",
            "status": step.status,
            "case_status": execution_case.status,
            "duration": step.duration,
            "screenshot_url": step.screenshot_path,
            "artifact_id": step.id if step.screenshot_path else None,
            "timestamp": now.isoformat(),
        },
    )


async def handle_assertion_result(db: AsyncSession, agent_id: int, payload: dict) -> None:
    execution_id = payload.get("execution_id")
    case_id = payload.get("case_id")
    if execution_id is None or case_id is None:
        return
    execution = await _bound_execution(db, agent_id, execution_id, payload.get("session_token"))
    if execution is None:
        return
    execution_case = (
        await db.execute(
            select(ExecutionCase).where(
                ExecutionCase.execution_id == execution_id,
                ExecutionCase.case_id == case_id,
            )
        )
    ).scalar_one_or_none()
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
        await _upsert_assertion(db, execution_case.id, order, normalized)
    assertion_failed = any(item["status"] == "fail" for item in normalized_assertions)
    if execution_case.status != "failed":
        execution_case.status = "failed" if assertion_failed else "passed"
    now = datetime.now(UTC)
    execution_case.finished_at = now
    if execution_case.started_at is not None:
        execution_case.duration = int((now - execution_case.started_at).total_seconds() * 1000)
    await db.commit()
    # V2 §8.2：assertion 广播（前端按 execution_id+case_id+step_order+type 幂等合并）
    await execution_manager.broadcast(
        execution_id,
        {
            "type": "assertion_result",
            "execution_id": execution_id,
            "case_id": case_id,
            "step_order": payload.get("step_order"),
            "assertions": normalized_assertions,
            "case_status": execution_case.status,
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
