import asyncio
import json
import logging
import platform
from collections.abc import Awaitable, Callable

import websockets

from request_logging import format_for_log

logger = logging.getLogger("agent.ws")

MAX_SIZE = 10 * 1024 * 1024  # 10MB

KeyProvider = str | Callable[[], str]


class AuthError(Exception):
    pass


MessageHandler = Callable[[dict], Awaitable[None]]


class AgentWSClient:
    def __init__(
        self,
        url: str,
        agent_key: KeyProvider,
        agent_id: str,
        version: str = "3.2.0",
        protocol_version: str | None = None,
        heartbeat_interval: int = 30,
    ) -> None:
        self.url = url
        self.agent_key = agent_key
        self.agent_id = agent_id
        self.version = version
        self.protocol_version = protocol_version
        self.heartbeat_interval = heartbeat_interval
        self.ws = None
        self.on_message: MessageHandler | None = None
        self.on_registered: MessageHandler | None = None
        self._stop = asyncio.Event()
        # 业务消息只能在注册握手完成后发送。服务端重启时发送方会在这里等待
        # 新连接，而不是让连接异常冒泡并中断正在执行的用例。
        self._registered = asyncio.Event()
        self._send_lock = asyncio.Lock()

    def _resolve_key(self) -> str:
        """解析当前 PSK：可传静态字符串或回调（绑定后无需重启即可重连）。"""
        key = self.agent_key() if callable(self.agent_key) else self.agent_key
        return key or ""

    async def connect(self) -> None:
        self._registered.clear()
        register_payload = {
            "type": "register",
            "agent_key": self._resolve_key(),
            "agent_id": self.agent_id,
            "hostname": platform.node(),
            "platform": platform.system().lower(),
            "version": self.version,
        }
        if self.protocol_version is not None:
            register_payload["protocol_version"] = self.protocol_version
        logger.info("WS 请求连接 %s params=%s", self.url, format_for_log(register_payload))
        ws = await websockets.connect(
            self.url,
            max_size=MAX_SIZE,
            ping_interval=20,
            ping_timeout=60,
            proxy=None,
        )
        self.ws = ws
        try:
            await ws.send(json.dumps(register_payload, ensure_ascii=False))
            reply = json.loads(await ws.recv())
            logger.info("WS 响应 %s params=%s", self.url, format_for_log(reply))
            if reply.get("status") != "ok":
                raise AuthError(f"注册失败: {reply}")
            self._registered.set()
            logger.info("已注册到服务器: agent_id=%s", reply.get("agent_id"))
            if self.on_registered is not None:
                await self.on_registered(reply)
        except BaseException:
            self._registered.clear()
            if self.ws is ws:
                self.ws = None
            try:
                await ws.close()
            except Exception:
                pass
            raise

    async def send(self, payload: dict) -> None:
        """可靠发送业务消息；断线时等待重连并在注册完成后重试。

        测试执行器逐步 await 本方法，因此断线窗口会暂停执行，既不会丢失
        步骤顺序，也不会因一次 ConnectionClosed 把整次执行异常终止。
        """
        encoded = json.dumps(payload, ensure_ascii=False)
        logger.info("WS 请求发送 %s params=%s", self.url, format_for_log(payload))
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._registered.wait(), timeout=1)
            except TimeoutError:
                continue
            if self._stop.is_set():
                break
            async with self._send_lock:
                ws = self.ws
                if ws is None or not self._registered.is_set():
                    continue
                try:
                    await ws.send(encoded)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # 只清除当前失败连接的就绪状态；run_forever 会负责重连。
                    if self.ws is ws:
                        self._registered.clear()
                    logger.warning("消息发送失败，等待重连后重试: %s", exc)
            await asyncio.sleep(0)
        raise ConnectionError("WebSocket 已停止")

    async def run_forever(self, retry_on_auth: bool = False) -> None:
        """连接循环。retry_on_auth=True 时认证失败不退出（桌面模式：绑定 Key 后自动重连）。"""
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
                    self._registered.clear()
            except AuthError:
                if not retry_on_auth:
                    logger.error("认证失败，停止重连（请检查 agent_key/agent_id）")
                    raise
                logger.warning("认证失败（可能尚未绑定），%ss 后重试", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
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
            logger.info("WS 响应接收 %s params=%s", self.url, format_for_log(message))
            msg_type = message.get("type")
            if msg_type == "ping":
                await self.send({"type": "pong"})
            elif msg_type == "pong":
                continue
            elif self.on_message is not None:
                await self.on_message(message)

    async def close(self) -> None:
        self._stop.set()
        # 唤醒所有等待连接的 send；它们会看到 _stop 后退出。
        self._registered.set()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.ws = None
