import asyncio
import json
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models import Execution, Project, ProjectMember, User
from app.schemas.ws import validate_agent_message
from app.ws.handlers import (
    handle_assertion_result,
    handle_case_status,
    handle_device_list,
    handle_execution_result,
    handle_heartbeat,
    handle_log,
    handle_register,
    handle_step_result,
    handle_suite_status,
    mark_agent_offline,
)
from app.ws.managers import agent_manager, execution_manager, profile_config_manager

router = APIRouter(tags=["WebSocket"])


async def _can_access_execution(db: AsyncSession, user: User, execution: Execution) -> bool:
    project = await db.get(Project, execution.project_id)
    if project is None:
        return False
    if project.owner_id == user.id:
        return True
    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if member is not None:
        return True
    return project.visibility == "public"


@router.websocket("/ws/executions/{execution_id}")
async def execution_ws(
    websocket: WebSocket,
    execution_id: int,
    db: AsyncSession = Depends(get_db),
):
    token = websocket.query_params.get("token")
    payload = decode_token(token) if token else None
    if payload is None or payload.get("type") != "access":
        await websocket.close(code=1008, reason="认证失败")
        return
    user = await db.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        await websocket.close(code=1008, reason="用户不存在或已禁用")
        return
    execution = await db.get(Execution, execution_id)
    if execution is None:
        await websocket.close(code=1008, reason="执行记录不存在")
        return
    if not await _can_access_execution(db, user, execution):
        await websocket.close(code=1008, reason="无权访问该执行")
        return

    await websocket.accept()
    await execution_manager.connect(execution_id, websocket)
    await websocket.send_json(
        {
            "type": "status",
            "execution_id": execution_id,
            "status": execution.status,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "close":
                break
    except WebSocketDisconnect:
        pass
    finally:
        await execution_manager.disconnect(execution_id, websocket)


@router.websocket("/ws/projects/{project_id}/config")
async def config_ws(
    websocket: WebSocket,
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """方案 §4.9：档案配置变更通知——只提示刷新，不携带配置正文，非一致性来源。"""
    token = websocket.query_params.get("token")
    payload = decode_token(token) if token else None
    if payload is None or payload.get("type") != "access":
        await websocket.close(code=1008, reason="认证失败")
        return
    user = await db.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        await websocket.close(code=1008, reason="用户不存在或已禁用")
        return
    if not await _can_access_project(db, user, project_id):
        await websocket.close(code=1008, reason="无权访问该项目")
        return

    await websocket.accept()
    await profile_config_manager.connect(project_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "close":
                break
    except WebSocketDisconnect:
        pass
    finally:
        await profile_config_manager.disconnect(project_id, websocket)


async def _can_access_project(db: AsyncSession, user: User, project_id: int) -> bool:
    project = await db.get(Project, project_id)
    if project is None:
        return False
    if project.owner_id == user.id:
        return True
    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if member is not None:
        return True
    return project.visibility == "public"


@router.websocket("/ws/agent")
async def agent_ws(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    await websocket.accept()
    current_agent_id: int | None = None
    pre_register_messages = 0
    # Step 5：整条连接共享一个注册期限——若按"每条消息各自计时"，
    # 未注册连接可周期性发消息无限续命，期限形同虚设
    register_deadline = time.monotonic() + settings.agent_ws_register_timeout_seconds
    try:
        while True:
            if current_agent_id is None:
                remaining = register_deadline - time.monotonic()
                if remaining <= 0:
                    await websocket.close(code=1008, reason="注册超时")
                    return
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=remaining)
                except TimeoutError:
                    await websocket.close(code=1008, reason="注册超时")
                    return
            else:
                message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                return

            # Step 5：先量尺寸再解析——receive_json() 无法在解析前拿到帧大小
            text = message.get("text")
            payload = message.get("bytes")
            frame_size = len(text.encode("utf-8")) if text is not None else len(payload or b"")
            if frame_size > settings.agent_ws_max_frame_bytes:
                await websocket.close(code=1009, reason="消息过大")
                return

            if current_agent_id is None:
                pre_register_messages += 1
                if pre_register_messages > settings.agent_ws_max_pre_register_messages:
                    await websocket.close(code=1008, reason="注册前消息数超限")
                    return

            try:
                data = json.loads(text if text is not None else payload)
            except (TypeError, ValueError):
                data = None
            if not isinstance(data, dict):
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "PROTOCOL_ERROR",
                        "message": "消息必须是 JSON 对象",
                    }
                )
                continue

            msg_type = data.get("type")
            # Step 10：按 type 验证 payload；协议错误回结构化 error，不写入 DB
            try:
                valid = validate_agent_message(data)
            except Exception:
                valid = None
            if valid is None:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "PROTOCOL_ERROR",
                        "message": f"Agent 消息无效或未知 type: {msg_type!r}",
                    }
                )
                continue
            if msg_type == "register":
                reply = await handle_register(db, websocket, valid)
                if reply is None:
                    return
                current_agent_id = reply["agent_id"]
                await websocket.send_json(reply)
            elif msg_type == "heartbeat":
                if current_agent_id is not None:
                    await handle_heartbeat(db, current_agent_id, valid)
            elif msg_type == "device_list":
                if current_agent_id is not None:
                    await handle_device_list(db, current_agent_id, valid)
            elif msg_type == "log":
                if current_agent_id is not None:
                    await handle_log(db, current_agent_id, valid)
            elif msg_type == "step_result":
                if current_agent_id is not None:
                    await handle_step_result(db, current_agent_id, valid)
            elif msg_type == "assertion_result":
                if current_agent_id is not None:
                    await handle_assertion_result(db, current_agent_id, valid)
            elif msg_type == "case_status":
                if current_agent_id is not None:
                    await handle_case_status(db, current_agent_id, valid)
            elif msg_type == "suite_status":
                if current_agent_id is not None:
                    await handle_suite_status(db, current_agent_id, valid)
            elif msg_type == "execution_result":
                if current_agent_id is not None:
                    acknowledged = await handle_execution_result(db, current_agent_id, valid)
                    if acknowledged:
                        await websocket.send_json(
                            {
                                "type": "execution_result_ack",
                                "execution_id": valid["execution_id"],
                                "session_token": valid.get("session_token"),
                            }
                        )
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        if current_agent_id is not None:
            # CR-16：带连接身份断开；仅当仍是当前连接时才标记离线，
            # 避免旧连接关闭把已重连的新连接误标为 offline
            removed = await agent_manager.disconnect(current_agent_id, websocket)
            if removed:
                await mark_agent_offline(db, current_agent_id)
