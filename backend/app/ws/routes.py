"""WebSocket 连接路由；数据库生命周期由 ws_ingest_service 管理。"""

import asyncio
import json
import time
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.schemas.ws import validate_agent_message
from app.services import ws_ingest_service
from app.ws.managers import agent_manager, execution_manager, profile_config_manager

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/executions/{execution_id}")
async def execution_ws(websocket: WebSocket, execution_id: int):
    allowed, current_status = await ws_ingest_service.authorize_execution(websocket.query_params.get("token"), execution_id)
    if not allowed:
        await websocket.close(code=1008, reason="认证失败或无权访问该执行")
        return
    await websocket.accept()
    await execution_manager.connect(execution_id, websocket)
    await websocket.send_json({"type": "status", "execution_id": execution_id, "status": current_status, "timestamp": datetime.now(UTC).isoformat()})
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
        await execution_manager.disconnect(execution_id, websocket)


@router.websocket("/ws/projects/{project_id}/config")
async def config_ws(websocket: WebSocket, project_id: int):
    if not await ws_ingest_service.authorize_project(websocket.query_params.get("token"), project_id):
        await websocket.close(code=1008, reason="认证失败或无权访问该项目")
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


@router.websocket("/ws/agent")
async def agent_ws(websocket: WebSocket, _legacy_db=None):
    await websocket.accept()
    current_agent_id: int | None = None
    pre_register_messages = 0
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
            text = message.get("text")
            raw_payload = message.get("bytes")
            frame_size = len(text.encode("utf-8")) if text is not None else len(raw_payload or b"")
            if frame_size > settings.agent_ws_max_frame_bytes:
                await websocket.close(code=1009, reason="消息过大")
                return
            if current_agent_id is None:
                pre_register_messages += 1
                if pre_register_messages > settings.agent_ws_max_pre_register_messages:
                    await websocket.close(code=1008, reason="注册前消息数超限")
                    return
            try:
                raw_input = text if isinstance(text, str) else raw_payload
                data = json.loads(raw_input if isinstance(raw_input, (str, bytes, bytearray)) else "")
            except (TypeError, ValueError):
                data = None
            if not isinstance(data, dict):
                await websocket.send_json({"type": "error", "code": "PROTOCOL_ERROR", "message": "消息必须是 JSON 对象"})
                continue
            try:
                valid = validate_agent_message(data)
            except Exception:
                valid = None
            if valid is None:
                await websocket.send_json({"type": "error", "code": "PROTOCOL_ERROR", "message": f"Agent 消息无效或未知 type: {data.get('type')!r}"})
                continue
            try:
                if valid["type"] == "register":
                    if current_agent_id is not None:
                        # 一条连接只允许注册一次：重复 register 会让 manager 把当前
                        # socket 当作"旧连接"关闭后再重新保存该已关闭 socket；
                        # 注册为另一个 Agent 时还会留下旧 ID 指向本 socket 的脏映射。
                        await websocket.send_json({
                            "type": "error",
                            "code": "PROTOCOL_ERROR",
                            "message": "该连接已注册，拒绝重复 register",
                        })
                        continue
                    reply = await ws_ingest_service.register_agent(websocket, valid)
                    if reply is None:
                        await websocket.close(code=1008, reason="Agent 认证失败或版本不受支持")
                        return
                    current_agent_id = reply["agent_id"]
                elif current_agent_id is None:
                    continue
                else:
                    reply = await ws_ingest_service.handle_agent_message(current_agent_id, valid)
                if reply is not None:
                    await websocket.send_json(reply)
            except WebSocketDisconnect:
                raise
            except Exception:
                # 单条消息失败不得污染连接的后续处理；service 已回滚当前短事务。
                try:
                    await websocket.send_json({
                        "type": "error",
                        "code": "INGEST_ERROR",
                        "message": "消息处理失败，请稍后重试",
                    })
                except WebSocketDisconnect:
                    raise
                except Exception:
                    return
    except WebSocketDisconnect:
        pass
    finally:
        if current_agent_id is not None:
            removed = await agent_manager.disconnect(current_agent_id, websocket)
            if removed:
                await ws_ingest_service.mark_agent_offline(current_agent_id)
