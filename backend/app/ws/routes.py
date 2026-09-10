"""WebSocket 连接路由；数据库生命周期由 ws_ingest_service 管理。"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.request_logging import format_for_log
from app.schemas.ws import validate_agent_message
from app.services import ws_ingest_service
from app.ws.managers import agent_manager, execution_manager, profile_config_manager

router = APIRouter(tags=["WebSocket"])


async def _read_frontend_frame(websocket: WebSocket) -> dict | None:
    """读取前端连接的一帧并解析为 JSON 对象。

    返回 None 表示畸形帧（非 JSON / 非对象），由调用方回错误包后继续循环。
    前端这两条路由此前直接用 `receive_json()`，畸形帧抛出的非
    `WebSocketDisconnect` 异常会穿透 `except WebSocketDisconnect` 直接断连，
    与 agent_ws 的健壮性不对称。
    """
    message = await websocket.receive()
    if message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(1000)
    text = message.get("text")
    raw = message.get("bytes")
    frame_size = len(text.encode("utf-8")) if text is not None else len(raw or b"")
    if frame_size > settings.agent_ws_max_frame_bytes:
        # 与 Agent 通道共用同一帧上限：前端只发 ping/close 控制帧，
        # 这里的作用是防止畸形超长帧无上限占用内存，而非业务校验。
        await websocket.close(code=1009, reason="消息过大")
        raise WebSocketDisconnect(1009)
    try:
        data = json.loads(text if isinstance(text, str) else (raw or b""))
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def _pump_frontend_messages(websocket: WebSocket) -> None:
    """前端连接的消息泵：仅处理 ping/close 控制帧，畸形帧回错误包不中断。"""
    while True:
        data = await _read_frontend_frame(websocket)
        if data is None:
            await websocket.send_json(
                {"type": "error", "code": "PROTOCOL_ERROR", "message": "消息必须是 JSON 对象"}
            )
            continue
        if data.get("type") == "ping":
            await websocket.send_json({"type": "pong"})
        elif data.get("type") == "close":
            return


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
        await _pump_frontend_messages(websocket)
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
        await _pump_frontend_messages(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        await profile_config_manager.disconnect(project_id, websocket)


@router.websocket("/ws/agent")
async def agent_ws(websocket: WebSocket, _legacy_db=None):
    logger = logging.getLogger("app.request")
    url = getattr(websocket, "url", None)
    logger.info("WS 连接请求 path=%s", getattr(url, "path", "/ws/agent"))
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
            logger.info("WS 请求 /ws/agent params=%s", format_for_log(data))
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
                    logger.info("WS 响应 /ws/agent params=%s", format_for_log(reply))
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
