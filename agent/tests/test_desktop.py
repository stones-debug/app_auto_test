"""Windows 方案 §4.1：桌面控制器非 UI 部件测试（设置持久化 + AsyncBridge + 绑定刷新健壮性）。"""

import asyncio
import threading
from types import SimpleNamespace

from binding import BindingManager
from credentials import CredentialStore, FileCredentialBackend
from desktop.controller import AsyncBridge, DesktopController, load_server_url, save_server_url
from main import AgentApp


def test_server_url_persist_roundtrip(tmp_path):
    save_server_url(tmp_path, "ws://127.0.0.1:8001/ws/agent")
    assert load_server_url(tmp_path, "ws://default") == "ws://127.0.0.1:8001/ws/agent"


def test_server_url_falls_back_to_default(tmp_path):
    assert load_server_url(tmp_path, "ws://default") == "ws://default"
    save_server_url(tmp_path, "  ")
    assert load_server_url(tmp_path, "ws://default") == "ws://default"


async def test_async_bridge_runs_coroutine_on_loop_thread():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        bridge = AsyncBridge(loop, timeout=5)
        result = bridge.call(lambda: asyncio.sleep(0.01, result="ok"))
        assert result == "ok"

        # fire-and-forget 不阻塞
        calls: list[str] = []

        async def record():
            await asyncio.sleep(0.01)
            calls.append("done")

        bridge.call_async(record)
        deadline = asyncio.get_event_loop().time() + 2
        while not calls and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert calls == ["done"]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


class StubBridge:
    """与 AsyncBridge 同接口，在专用后台循环线程执行协程。"""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def call(self, coro_factory, timeout: float = 5.0) -> object:
        future = asyncio.run_coroutine_threadsafe(coro_factory(), self.loop)
        return future.result(timeout=timeout)

    def call_async(self, coro_factory) -> None:
        asyncio.run_coroutine_threadsafe(coro_factory(), self.loop)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


def test_refresh_bindings_unbound_does_not_crash(tmp_path):
    """BUG 回归：未绑定（无机器 PSK）时启动刷新不得抛异常打断 UI。"""
    creds = CredentialStore(backend=FileCredentialBackend(tmp_path / "credentials"))
    bindings = BindingManager("http://t", "agent-unbound", creds)
    app = AgentApp({}, bindings=bindings)
    bridge = StubBridge()
    try:
        ctrl = DesktopController(app, bridge, tmp_path, tmp_path / "logs", "ws://t")
        messages: list[str] = []
        ctrl.bindings_var = SimpleNamespace(set=lambda v: messages.append(v))
        ctrl._refresh_bindings()
        assert messages and "未绑定" in messages[0]
    finally:
        bridge.close()


def test_refresh_bindings_renders_users(tmp_path):
    class FakeBindings:
        async def list_users(self):
            return [{"username": "alice"}, {"username": "bob"}]

        def machine_psk(self):
            return "sk-x"

    app = AgentApp({}, bindings=FakeBindings())
    bridge = StubBridge()
    try:
        ctrl = DesktopController(app, bridge, tmp_path, tmp_path / "logs", "ws://t")
        messages: list[str] = []
        ctrl.bindings_var = SimpleNamespace(set=lambda v: messages.append(v))
        ctrl._refresh_bindings()
        assert messages and "alice、bob" in messages[0]
    finally:
        bridge.close()
