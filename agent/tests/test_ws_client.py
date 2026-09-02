import asyncio
import json

import httpx
import pytest
import websockets

from uploader import Uploader
from ws_client import AgentWSClient, AuthError


async def test_ws_client_register_handshake():
    registered = {}

    async def handler(ws):
        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "register"
        assert msg["agent_key"] == "sk-test"
        assert msg["agent_id"] == "agent-1"
        assert msg["protocol_version"] == "3.2.0"
        await ws.send(json.dumps({"type": "registered", "agent_id": 42, "status": "ok"}))
        await asyncio.sleep(0.2)
        await ws.close()

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = AgentWSClient(
            url=f"ws://127.0.0.1:{port}/ws/agent",
            agent_key="sk-test",
            agent_id="agent-1",
            protocol_version="3.2.0",
        )

        async def on_registered(reply):
            registered["reply"] = reply

        client.on_registered = on_registered
        await client.connect()
        assert registered["reply"]["status"] == "ok"
        assert registered["reply"]["agent_id"] == 42
        await client.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_ws_client_auth_failure_stops():
    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "error", "code": "AUTH_FAILED"}))
        await ws.close()

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = AgentWSClient(
            url=f"ws://127.0.0.1:{port}/ws/agent",
            agent_key="sk-wrong",
            agent_id="agent-1",
        )
        with pytest.raises(AuthError):
            await client.connect()
        await client.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_ws_client_send_receive():
    received = []
    got_message = asyncio.Event()

    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "registered", "agent_id": 1, "status": "ok"}))
        msg = json.loads(await ws.recv())
        assert msg["type"] == "heartbeat"
        await ws.send(json.dumps({"type": "start_test", "execution_id": 5}))
        await asyncio.sleep(0.3)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async def on_message(message):
            received.append(message)
            got_message.set()

        client = AgentWSClient(f"ws://127.0.0.1:{port}/ws/agent", "sk-test", "agent-1")
        client.on_message = on_message
        await client.connect()
        recv_task = asyncio.create_task(client._receive_loop())
        await client.send({"type": "heartbeat"})
        await asyncio.wait_for(got_message.wait(), timeout=2)
        assert received and received[0]["type"] == "start_test"
        recv_task.cancel()
        await client.close()
    finally:
        server.close()
        await server.wait_closed()


# ---------- Windows 方案 §4.1：动态 PSK 回调 + 桌面模式认证重试 ----------


class FakeWebSocket:
    """最小 WS 替身：recv 返回注册成功，send 记录，可迭代立即结束。"""

    def __init__(self, reply: dict | None = None) -> None:
        self.reply = reply or {"status": "ok", "agent_id": 1}
        self.sent: list[str] = []

    async def recv(self) -> str:
        return json.dumps(self.reply)

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class FailAfterRegisterWebSocket(FakeWebSocket):
    """注册消息可发送，第一条业务消息模拟服务端重启导致连接断开。"""

    def __init__(self) -> None:
        super().__init__()
        self.business_send_failed = asyncio.Event()

    async def send(self, data: str) -> None:
        if self.sent:
            self.business_send_failed.set()
            raise ConnectionError("server restarted")
        await super().send(data)


def _install_fake_connect(monkeypatch, factory) -> None:
    async def fake_connect(*args, **kwargs):
        return factory()

    monkeypatch.setattr("ws_client.websockets.connect", fake_connect)


async def test_callable_agent_key_resolved_at_connect(monkeypatch):
    ws = FakeWebSocket()
    _install_fake_connect(monkeypatch, lambda: ws)

    resolved: list[str] = []

    def provider() -> str:
        resolved.append("called")
        return "sk-live"

    client = AgentWSClient("ws://t/ws/agent", provider, "agent-x")
    await client.connect()
    register = json.loads(ws.sent[0])
    assert register["agent_key"] == "sk-live"
    assert resolved == ["called"]  # 连接时才解析


async def test_static_agent_key_still_works(monkeypatch):
    ws = FakeWebSocket()
    _install_fake_connect(monkeypatch, lambda: ws)

    client = AgentWSClient("ws://t/ws/agent", "sk-static", "agent-y")
    await client.connect()
    register = json.loads(ws.sent[0])
    assert register["agent_key"] == "sk-static"


async def test_business_send_waits_for_reregister_and_retries(monkeypatch):
    """服务端重启窗口中的执行消息应在新连接注册后续传，不向执行器抛错。"""
    first = FailAfterRegisterWebSocket()
    second = FakeWebSocket()
    sockets = iter([first, second])
    _install_fake_connect(monkeypatch, lambda: next(sockets))

    client = AgentWSClient("ws://t/ws/agent", "sk-static", "agent-reconnect")
    await client.connect()
    sending = asyncio.create_task(client.send({"type": "execution_result", "execution_id": 7}))
    await asyncio.wait_for(first.business_send_failed.wait(), timeout=1)

    # 模拟 run_forever 建立并完成新的注册握手。
    await client.connect()
    await asyncio.wait_for(sending, timeout=1)

    assert [json.loads(raw)["type"] for raw in second.sent] == ["register", "execution_result"]
    await client.close()


async def test_run_forever_raises_auth_without_retry(monkeypatch):
    def factory():
        raise AuthError("认证失败")

    _install_fake_connect(monkeypatch, factory)
    client = AgentWSClient("ws://t/ws/agent", "sk-wrong", "agent-z")
    with pytest.raises(AuthError):
        await client.run_forever(retry_on_auth=False)


async def test_run_forever_retries_on_auth_in_desktop_mode(monkeypatch):
    """桌面模式：认证失败（未绑定）不退出，绑定 Key 后重连即用新 PSK。"""
    calls = {"n": 0}
    ws = FakeWebSocket()

    def factory():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise AuthError("尚未绑定")
        if calls["n"] == 4:
            raise asyncio.CancelledError()  # 终止循环（测试用）
        return ws

    _install_fake_connect(monkeypatch, factory)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("ws_client.asyncio.sleep", no_sleep)

    client = AgentWSClient("ws://t/ws/agent", lambda: "sk-bound-later", "agent-w")
    await client.run_forever(retry_on_auth=True)
    # 两轮认证失败重试 + 一次成功连接 + 一次取消退出
    assert calls["n"] == 4


async def test_uploader_resolves_callable_key(tmp_path, monkeypatch):
    requests_log: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "content": request.read(),
            }
        )
        return httpx.Response(200, json={"path": "execution_1/screenshots/a.png"})

    uploader = Uploader(
        "http://t",
        lambda: "sk-upload",
        "agent-u",
        transport=httpx.MockTransport(handler),
    )
    file = tmp_path / "shot.png"
    file.write_bytes(b"png-data")
    result = await uploader.upload_screenshot(1, str(file), "sess")
    assert result == "execution_1/screenshots/a.png"
    entry = requests_log[0]
    assert entry["method"] == "POST"
    assert entry["url"] == "http://t/api/agent/upload"
    assert entry["headers"]["x-agent-key"] == "sk-upload"
    body = entry["content"]
    # multipart：文件内容 + 关键字段（execution_id / agent_id / session_token）
    assert b"png-data" in body
    assert b'name="execution_id"' in body or b"execution_id" in body
    assert b"agent-u" in body
    assert b"sess" in body
    assert b"shot.png" in body
