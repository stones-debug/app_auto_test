"""WebSocket 消息短事务编排。"""

from contextlib import asynccontextmanager

from app.core.database import SessionLocal
from app.core.security import decode_token
from app.repositories import access as access_repo
from app.repositories import auth as auth_repo
from app.repositories import executions as executions_repo
from app.repositories import projects as projects_repo
from app.repositories import ws_handlers
from app.ws.managers import execution_manager

HANDLERS = {
    "register": ws_handlers.handle_register,
    "heartbeat": ws_handlers.handle_heartbeat,
    "device_list": ws_handlers.handle_device_list,
    "log": ws_handlers.handle_log,
    "step_result": ws_handlers.handle_step_result,
    "assertion_result": ws_handlers.handle_assertion_result,
    "case_status": ws_handlers.handle_case_status,
    "suite_status": ws_handlers.handle_suite_status,
    "execution_result": ws_handlers.handle_execution_result,
}


@asynccontextmanager
async def session_scope():
    async with SessionLocal() as db:
        try:
            yield db
        except Exception:
            await db.rollback()
            raise


async def commit_session(db) -> None:
    await db.commit()


async def publish_handler_result(result: dict | None) -> None:
    """事务提交成功后再广播，避免客户端看到未提交的数据。"""
    if not isinstance(result, dict):
        return
    event = result.get("event") if result.get("ack") else result
    if isinstance(event, dict) and event.get("execution_id") is not None:
        await execution_manager.broadcast(event["execution_id"], event)


async def authorize_execution(token: str | None, execution_id: int) -> tuple[bool, str | None]:
    payload = decode_token(token) if token else None
    if payload is None or payload.get("type") != "access":
        return False, None
    async with session_scope() as db:
        user = await auth_repo.get_user_by_id(db, int(payload["sub"]))
        execution = await executions_repo.get_by_id(db, execution_id)
        if user is None or user.status != "active" or execution is None:
            return False, None
        project = await projects_repo.get_by_id(db, execution.project_id)
        if project is None:
            return False, None
        member = await access_repo.get_project_member(db, project.id, user.id)
        allowed = project.owner_id == user.id or member is not None or project.visibility == "public"
        return allowed, execution.status if allowed else None


async def authorize_project(token: str | None, project_id: int) -> bool:
    payload = decode_token(token) if token else None
    if payload is None or payload.get("type") != "access":
        return False
    async with session_scope() as db:
        user = await auth_repo.get_user_by_id(db, int(payload["sub"]))
        project = await projects_repo.get_by_id(db, project_id)
        if user is None or user.status != "active" or project is None:
            return False
        member = await access_repo.get_project_member(db, project_id, user.id)
        return project.owner_id == user.id or member is not None or project.visibility == "public"


async def handle_agent_message(agent_id: int, payload: dict) -> dict | None:
    handler = HANDLERS.get(payload["type"])
    if handler is None:
        return None
    async with session_scope() as db:
        result = await handler(db, agent_id, payload)
        await db.commit()
    await publish_handler_result(result if isinstance(result, dict) else None)
    if payload["type"] == "register" and isinstance(result, dict):
        return result
    if payload["type"] == "execution_result" and result:
        return {
            "type": "execution_result_ack",
            "execution_id": payload["execution_id"],
            "session_token": payload.get("session_token"),
        }
    return None


async def mark_agent_offline(agent_id: int) -> None:
    async with session_scope() as db:
        await ws_handlers.mark_agent_offline(db, agent_id)
        await db.commit()
