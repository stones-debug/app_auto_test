"""WebSocket 处理器兼容层。真实消息事务由 ws_ingest_service 管理。"""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.ws_handlers import (
    TERMINAL_STATES,
    WRITE_STATES,
    _bound_execution,
    _find_snapshot_step,
    _locate_step,
    _merge_terminal_status,
    _parse_terminal_status,
    _resolve_case,
    _resolve_suite,
    _settle_execution_cases,
    _step_belongs_to,
    _stored_execution_terminal,
    _upsert_assertion,
    version_supported,
)
from app.repositories.ws_handlers import (
    handle_assertion_result as _handle_assertion_result,
)
from app.repositories.ws_handlers import (
    handle_case_status as _handle_case_status,
)
from app.repositories.ws_handlers import (
    handle_device_list as _handle_device_list,
)
from app.repositories.ws_handlers import (
    handle_execution_result as _handle_execution_result,
)
from app.repositories.ws_handlers import (
    handle_heartbeat as _handle_heartbeat,
)
from app.repositories.ws_handlers import (
    handle_log as _handle_log,
)
from app.repositories.ws_handlers import (
    handle_register as _handle_register,
)
from app.repositories.ws_handlers import (
    handle_step_result as _handle_step_result,
)
from app.repositories.ws_handlers import (
    handle_suite_status as _handle_suite_status,
)
from app.repositories.ws_handlers import (
    mark_agent_offline as _mark_agent_offline,
)
from app.services.ws_ingest_service import commit_session, publish_handler_result
from app.ws.managers import agent_manager


async def _run_compat(db: AsyncSession, handler: Callable[..., Awaitable[Any]], *args: Any) -> Any:
    result = await handler(db, *args)
    await commit_session(db)
    await publish_handler_result(result)
    if isinstance(result, dict) and result.get("ack"):
        return True
    if isinstance(result, dict) and "event" in result:
        return None
    return result


async def handle_register(db: AsyncSession, ws: Any, payload: dict) -> dict | None:
    result = await _handle_register(db, payload)
    await commit_session(db)
    if result is None:
        await ws.close(code=1008, reason="Agent 认证失败或版本不受支持")
    else:
        await agent_manager.connect(result["agent_id"], ws)
    return result


async def handle_heartbeat(db: AsyncSession, agent_id: int, payload: dict) -> Any:
    return await _run_compat(db, _handle_heartbeat, agent_id, payload)


async def handle_device_list(db: AsyncSession, agent_id: int, payload: dict) -> Any:
    return await _run_compat(db, _handle_device_list, agent_id, payload)


async def handle_log(db: AsyncSession, agent_id: int, payload: dict) -> Any:
    return await _run_compat(db, _handle_log, agent_id, payload)


async def handle_step_result(db: AsyncSession, agent_id: int, payload: dict) -> Any:
    return await _run_compat(db, _handle_step_result, agent_id, payload)


async def handle_assertion_result(db: AsyncSession, agent_id: int, payload: dict) -> Any:
    return await _run_compat(db, _handle_assertion_result, agent_id, payload)


async def handle_case_status(db: AsyncSession, agent_id: int, payload: dict) -> Any:
    return await _run_compat(db, _handle_case_status, agent_id, payload)


async def handle_suite_status(db: AsyncSession, agent_id: int, payload: dict) -> Any:
    return await _run_compat(db, _handle_suite_status, agent_id, payload)


async def handle_execution_result(db: AsyncSession, agent_id: int, payload: dict) -> bool:
    return bool(await _run_compat(db, _handle_execution_result, agent_id, payload))


async def mark_agent_offline(db: AsyncSession, agent_id: int) -> None:
    await _run_compat(db, _mark_agent_offline, agent_id)


__all__ = [
    "TERMINAL_STATES", "WRITE_STATES", "_bound_execution", "_find_snapshot_step", "_locate_step",
    "_merge_terminal_status", "_parse_terminal_status", "_resolve_case", "_resolve_suite",
    "_settle_execution_cases", "_step_belongs_to", "_stored_execution_terminal", "_upsert_assertion",
    "handle_assertion_result", "handle_case_status", "handle_device_list", "handle_execution_result",
    "handle_heartbeat", "handle_log", "handle_register", "handle_step_result", "handle_suite_status",
    "mark_agent_offline", "version_supported",
]
