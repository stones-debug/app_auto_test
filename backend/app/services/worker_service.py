"""Worker 编排服务。

Repository 负责队列、锁、执行树和终态写入；本模块只负责阶段编排、事务提交
以及 Agent 网络 I/O。每个阶段都可以使用独立的 Session。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.repositories import executions as executions_repo
from app.repositories import worker as worker_repo

logger = logging.getLogger("worker")
TERMINAL_STATES = worker_repo.TERMINAL_STATES

protocol_version = worker_repo.protocol_version
min_agent_version = worker_repo.min_agent_version
render_text = worker_repo.render_text
render_value = worker_repo.render_value
build_base_variable_map = worker_repo.build_base_variable_map
build_variable_map = worker_repo.build_variable_map
build_case_snapshot = worker_repo.build_case_snapshot
_create_execution_cases = worker_repo.create_execution_cases_from_execution
_select_and_lock_device = worker_repo.select_and_lock_device
_reclaim_stale_claimed = worker_repo.reclaim_stale_claimed
_timeout_scan = worker_repo.timeout_scan
_finalize_unfinished_terminal = worker_repo.finalize_unfinished_terminal
_agent_heartbeat_scan = worker_repo.agent_heartbeat_scan
_mark_terminal_repo = worker_repo._mark_terminal
_lock_device_repo = worker_repo._lock_device
_build_suites_payload = worker_repo._build_suites_payload
_aggregate_status = worker_repo._aggregate_status
_rate_percent = worker_repo._rate_percent
_normalize_stuck_status = worker_repo._normalize_stuck_status
_stop_grace_exceeded = worker_repo._stop_grace_exceeded


async def _default_agent_sender(agent_id: int, payload: dict) -> bool:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{settings.backend_base_url}/internal/ws/agents/{agent_id}/send",
            json=payload,
            headers={"X-Internal-Token": settings.internal_token},
        )
        return response.status_code in (200, 202)


async def commit(db) -> None:
    commit_method = getattr(db, "commit", None)
    if commit_method is not None:
        await commit_method()


async def claim_next_queue(db, worker_id: str):
    item = await worker_repo.claim_next_queue(db, worker_id)
    await commit(db)
    return item


async def create_execution_cases_from_execution(db, execution):
    result = await _create_execution_cases(db, execution)
    await commit(db)
    return result


async def select_and_lock_device(db, execution):
    result = await _select_and_lock_device(db, execution)
    await commit(db)
    return result


async def _lock_device(db, device_id: int, execution_id: int) -> bool:
    result = await _lock_device_repo(db, device_id, execution_id)
    await commit(db)
    return result


async def _mark_terminal(db, execution, status: str, error_message: str | None = None):
    result = await _mark_terminal_repo(db, execution, status, error_message)
    await commit(db)
    return result


async def reclaim_stale_claimed(db, stale_minutes: int = 10) -> None:
    await _reclaim_stale_claimed(db, stale_minutes)
    await commit(db)


async def timeout_scan(db) -> None:
    await _timeout_scan(db)
    await commit(db)


async def finalize_unfinished_terminal(db) -> None:
    await _finalize_unfinished_terminal(db)
    await commit(db)


async def agent_heartbeat_scan(db) -> None:
    await _agent_heartbeat_scan(db)
    await commit(db)


@asynccontextmanager
async def _session_scope(factory):
    async with factory() as db:
        try:
            yield db
        except Exception:
            await db.rollback()
            raise


async def _finalize_error(factory, execution_id: int, message: str) -> None:
    async with _session_scope(factory) as db:
        execution = await executions_repo.get_by_id(db, execution_id)
        if execution is not None:
            await _mark_terminal_repo(db, execution, "error", message)
            await commit(db)


async def _restore_unsubmitted_execution(
    factory,
    execution_id: int,
    worker_id: str,
    session_token: str,
) -> None:
    """恢复已认领但尚未成功下发给 Agent 的执行。"""
    async with _session_scope(factory) as db:
        restored = await executions_repo.restore_reserved_execution(
            db,
            execution_id,
            worker_id=worker_id,
            session_token=session_token,
        )
        if not restored:
            return
        await db.commit()


async def restore_reserved_execution(execution_id: int, worker_id: str) -> bool:
    """恢复已认领但尚未开始执行协程的任务。

    Worker 停机时，刚从队列认领出来的任务可能还没来得及进入执行协程，
    因而不能依赖执行协程自身的取消清理逻辑。这里重新读取 session token，
    用同一条件更新执行、队列和设备，避免把已经下发或已完成的任务回滚。
    """
    async with _session_scope(SessionLocal) as db:
        execution = await executions_repo.get_by_id(db, execution_id)
        if execution is None or not execution.session_token:
            return False
        restored = await executions_repo.restore_reserved_execution(
            db,
            execution_id,
            worker_id=worker_id,
            session_token=execution.session_token,
        )
        if restored:
            await db.commit()
        return restored


async def run_reserved_execution(
    db,
    execution_id: int,
    worker_id: str,
    *,
    agent_sender: Callable[[int, dict], Awaitable[bool]] | None = None,
    poll_interval: float = 5.0,
    session_factory=None,
) -> None:
    """执行一个已由 ``claim_next_queue`` 原子认领的任务。

    这里不再改变 queued/running 状态，也不再选择或占用设备；所有外部网络
    等待都发生在短事务之外。
    """
    # db 仅保留为旧调用方的兼容参数；执行生命周期必须始终通过工厂切分短 Session。
    factory = session_factory or SessionLocal
    sender = agent_sender or _default_agent_sender
    dispatch_attempted = False
    session_token: str | None = None

    async def restore_before_dispatch() -> None:
        if dispatch_attempted or session_token is None:
            return
        try:
            await _restore_unsubmitted_execution(
                factory, execution_id, worker_id, session_token
            )
        except Exception:
            logger.exception("[%s] 恢复未下发执行失败 execution=%s", worker_id, execution_id)

    try:
        async with _session_scope(factory) as start_db:
            execution = await executions_repo.get_by_id(start_db, execution_id)
            if execution is None:
                return
            if execution.status in TERMINAL_STATES:
                if execution.finalized_at is None:
                    await _create_execution_cases(start_db, execution)
                    await _mark_terminal_repo(start_db, execution, execution.status)
                    await start_db.commit()
                return
            if execution.status not in {"running", "stopping"}:
                logger.info("[%s] execution=%s 非活动状态，跳过", worker_id, execution_id)
                return
            session_token = execution.session_token
            if not session_token:
                raise RuntimeError("执行缺少 session_token")
            await _create_execution_cases(start_db, execution)
            device = await executions_repo.get_device_for_execution(start_db, execution)
            if device is None:
                raise RuntimeError("执行设备不存在")
            agent_id = device.agent_id
            await start_db.commit()
    except asyncio.CancelledError:
        await restore_before_dispatch()
        raise
    except Exception as exc:
        await _finalize_error(factory, execution_id, f"Worker 启动阶段失败: {exc}")
        return

    try:
        async with _session_scope(factory) as payload_db:
            execution = await executions_repo.get_by_id(payload_db, execution_id)
            if execution is None or execution.device_id is None:
                return
            device = await executions_repo.get_device_for_execution(payload_db, execution)
            if device is None:
                raise RuntimeError("执行设备不存在")
            payload = {
                "type": "start_test", "execution_id": execution.id,
                "session_token": execution.session_token, "parameters": execution.parameters,
                "protocol_version": protocol_version(),
                "device": {"udid": device.udid, "platform": device.platform},
                "suites": await _build_suites_payload(payload_db, execution),
            }
            agent_id = device.agent_id
    except asyncio.CancelledError:
        await restore_before_dispatch()
        raise
    except Exception as exc:
        await _finalize_error(factory, execution_id, f"Worker 载荷构建失败: {exc}")
        return

    try:
        dispatch_attempted = True
        ok = await sender(agent_id, payload)
    except asyncio.CancelledError:
        await restore_before_dispatch()
        raise
    except Exception as exc:
        await _finalize_error(factory, execution_id, f"Agent 发送失败: {exc}")
        return
    if not ok:
        await _finalize_error(factory, execution_id, "Agent 不在线或未连接 WS，无法开始执行")
        return
    try:
        while True:
            async with _session_scope(factory) as poll_db:
                current = await executions_repo.get_by_id(poll_db, execution_id, populate_existing=True)
                if current is None:
                    return
                if current.status in TERMINAL_STATES:
                    await _mark_terminal_repo(poll_db, current, current.status)
                    await poll_db.commit()
                    return
                stopping = current.status == "stopping"
                should_stop = stopping and _stop_grace_exceeded(current)
                if should_stop:
                    await _mark_terminal_repo(poll_db, current, "stopped", f"停止宽限期超时（>{settings.execution_stop_grace_seconds}s），强制结束")
                    await poll_db.commit()
                    return
                device = await executions_repo.get_device_for_execution(poll_db, current)
                device_agent_id = device.agent_id if device is not None else None
            if stopping and device_agent_id is not None:
                try:
                    await sender(device_agent_id, {"type": "stop_test", "execution_id": execution_id})
                except Exception:
                    logger.exception("[%s] stop_test 发送失败 execution=%s", worker_id, execution_id)
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        await restore_before_dispatch()
        raise


async def run_execution(
    db,
    execution_id: int,
    worker_id: str,
    *,
    agent_sender: Callable[[int, dict], Awaitable[bool]] | None = None,
    poll_interval: float = 5.0,
    session_factory=None,
) -> None:
    """兼容旧调用名；任务必须先由 ``claim_next_queue`` 完成原子认领。"""
    await run_reserved_execution(
        db,
        execution_id,
        worker_id,
        agent_sender=agent_sender,
        poll_interval=poll_interval,
        session_factory=session_factory,
    )
