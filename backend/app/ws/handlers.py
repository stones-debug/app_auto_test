from datetime import UTC, datetime

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Agent,
    Device,
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionLog,
    ExecutionStep,
)
from app.ws.managers import agent_manager, execution_manager

TERMINAL_STATES = {"passed", "failed", "error", "stopped", "cancelled"}


async def mark_agent_offline(db: AsyncSession, agent_id: int) -> None:
    agent = await db.get(Agent, agent_id)
    if agent is not None:
        agent.status = "offline"
        await db.commit()


async def handle_register(db: AsyncSession, ws: WebSocket, payload: dict) -> dict | None:
    agent_id_str = payload.get("agent_id")
    agent_key = payload.get("agent_key")
    if not agent_id_str or not agent_key:
        await ws.close(code=1008, reason="缺少 agent_id 或 agent_key")
        return None
    agent = (
        await db.execute(select(Agent).where(Agent.agent_id == agent_id_str))
    ).scalar_one_or_none()
    if agent is None or agent.agent_key != agent_key:
        await ws.close(code=1008, reason="Agent 认证失败")
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
                capabilities=item.get("capabilities") or {},
                last_heartbeat=datetime.now(UTC),
            )
            db.add(device)
        else:
            device.status = item.get("status") or device.status
            device.last_heartbeat = datetime.now(UTC)
    await db.commit()


async def handle_log(db: AsyncSession, agent_id: int, payload: dict) -> None:
    execution_id = payload.get("execution_id")
    log = ExecutionLog(
        execution_id=execution_id,
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
    if step is None:
        step = ExecutionStep(
            execution_case_id=execution_case.id,
            step_order=step_order,
            action=payload.get("action") or "unknown",
            parameters={},
            status=payload.get("status") or "passed",
            started_at=now,
            finished_at=now,
            duration=payload.get("duration"),
            actual_value=payload.get("actual_value"),
            error_message=payload.get("error_message"),
            screenshot_path=payload.get("screenshot_path"),
        )
        db.add(step)
    else:
        step.status = payload.get("status") or step.status
        step.finished_at = now
        step.duration = payload.get("duration")
        step.actual_value = payload.get("actual_value")
        step.error_message = payload.get("error_message")
        step.screenshot_path = payload.get("screenshot_path")
    await db.flush()

    if execution_case.status == "pending":
        execution_case.status = "running"
    if step.status == "failed":
        execution_case.status = "failed"

    for assertion in payload.get("assertions") or []:
        db.add(
            ExecutionAssertion(
                execution_step_id=step.id,
                assertion_type=assertion.get("type") or "",
                expected_value=str(assertion.get("expected", "")),
                actual_value=str(assertion.get("actual", "")),
                status=assertion.get("status") or "pass",
                error_message=assertion.get("error_message"),
            )
        )
    await db.commit()

    await execution_manager.broadcast(
        execution_id,
        {
            "type": "step_result",
            "execution_id": execution_id,
            "case_id": case_id,
            "step_order": step_order,
            "status": step.status,
            "duration": step.duration,
            "screenshot_url": step.screenshot_path,
            "timestamp": now.isoformat(),
        },
    )


async def handle_assertion_result(db: AsyncSession, agent_id: int, payload: dict) -> None:
    execution_id = payload.get("execution_id")
    case_id = payload.get("case_id")
    if execution_id is None or case_id is None:
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
    last_step = (
        await db.execute(
            select(ExecutionStep)
            .where(ExecutionStep.execution_case_id == execution_case.id)
            .order_by(ExecutionStep.step_order.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last_step is None:
        db.add(
            ExecutionLog(
                execution_id=execution_id,
                level="WARN",
                message=f"assertion_result 无步骤可关联 (case_id={case_id})",
                source="agent",
            )
        )
        await db.commit()
        return
    for assertion in payload.get("assertions") or []:
        db.add(
            ExecutionAssertion(
                execution_step_id=last_step.id,
                assertion_type=assertion.get("type") or "",
                expected_value=str(assertion.get("expected") or ""),
                actual_value=str(assertion.get("actual") or ""),
                status=assertion.get("status") or "fail",
                error_message=assertion.get("error_message"),
            )
        )
    await db.commit()


async def handle_execution_result(db: AsyncSession, agent_id: int, payload: dict) -> None:
    execution_id = payload.get("execution_id")
    execution = await db.get(Execution, execution_id)
    if execution is None:
        return
    status = (payload.get("status") or "error").lower()
    if status not in TERMINAL_STATES:
        status = "error"
    now = datetime.now(UTC)
    execution.status = status
    execution.finished_at = now
    if execution.started_at is not None:
        execution.duration = int((now - execution.started_at).total_seconds() * 1000)
    await db.commit()
    await execution_manager.broadcast(
        execution_id,
        {
            "type": "completed",
            "execution_id": execution_id,
            "status": status,
            "timestamp": now.isoformat(),
        },
    )
