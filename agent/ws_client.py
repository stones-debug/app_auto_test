import asyncio
import json
import logging
import platform
from collections.abc import Awaitable, Callable

import websockets

logger = logging.getLogger("agent.ws")

MAX_SIZE = 10 * 1024 * 1024  # 10MB


class AuthError(Exception):
    pass


MessageHandler = Callable[[dict], Awaitable[None]]


class AgentWSClient:
    def __init__(
        self,
        url: str,
        agent_key: str,
        agent_id: str,
        version: str = "1.0.0",
        heartbeat_interval: int = 30,
    ) -> None:
        self.url = url
        self.agent_key = agent_key
        self.agent_id = agent_id
        self.version = version
        self.heartbeat_interval = heartbeat_interval
        self.ws = None
        self.on_message: MessageHandler | None = None
        self.on_registered: MessageHandler | None = None
        self._stop = asyncio.Event()

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.url, max_size=MAX_SIZE, ping_interval=20, ping_timeout=60)
        await self.send(
            {
                "type": "register",
                "agent_key": self.agent_key,
                "agent_id": self.agent_id,
                "hostname": platform.node(),
                "platform": platform.system().lower(),
                "version": self.version,
            }
        )
        reply = json.loads(await self.ws.recv())
        if reply.get("status") != "ok":
            raise AuthError(f"注册失败: {reply}")
        logger.info("已注册到服务器: agent_id=%s", reply.get("agent_id"))
        if self.on_registered is not None:
            await self.on_registered(reply)

    async def send(self, payload: dict) -> None:
        if self.ws is None:
            raise ConnectionError("WebSocket 未连接")
        await self.ws.send(json.dumps(payload, ensure_ascii=False))

    async def run_forever(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                await self.connect()
                backoff = 1
                heartbeat = asyncio.create_task(self._heartbeat_loop())
                try:
                    await self._receive_loop()
                finally:
                    heartbeat.cancel()
            except AuthError:
                logger.error("认证失败，停止重连（请检查 agent_key/agent_id）")
                raise
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("连接异常: %s，%ss 后重连", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await self.send({"type": "heartbeat"})
            except Exception as exc:
                logger.warning("心跳发送失败: %s", exc)
                break

    async def _receive_loop(self) -> None:
        if not self.ws:
            raise ConnectionError("WebSocket 未连接")
        async for raw in self.ws:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = message.get("type")
            if msg_type == "ping":
                await self.send({"type": "pong"})
            elif msg_type == "pong":
                continue
            elif self.on_message is not None:
                await self.on_message(message)

    async def close(self) -> None:
        self._stop.set()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
