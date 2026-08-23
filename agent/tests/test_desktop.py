"""Windows 方案 §4.1：桌面控制器非 UI 部件测试（设置持久化 + AsyncBridge + 绑定刷新健壮性）。"""

import asyncio
import threading
from types import SimpleNamespace

from binding import BindingManager
from credentials import CredentialStore, FileCredentialBackend
from desktop.controller import (
    USER_KEY_FILENAME,
    AsyncBridge,
    DesktopController,
    load_server_url,
    load_user_key,
    save_server_url,
    save_user_key,
)
from main import AgentApp


def test_server_url_persist_roundtrip(tmp_path):
    save_server_url(tmp_path, "ws://127.0.0.1:8001/ws/agent")
    assert load_server_url(tmp_path, "ws://default") == "ws://127.0.0.1:8001/ws/agent"


def test_server_url_falls_back_to_default(tmp_path):
    assert load_server_url(tmp_path, "ws://default") == "ws://default"
    save_server_url(tmp_path, "  ")
    assert load_server_url(tmp_path, "ws://default") == "ws://default"


def test_user_key_persist_roundtrip_in_runtime_dir(tmp_path):
    save_user_key(tmp_path, "  uak_public_secret  ")

    assert load_user_key(tmp_path) == "uak_public_secret"
    assert (tmp_path / USER_KEY_FILENAME).read_text(encoding="utf-8") == "uak_public_secret"


def test_user_key_missing_returns_empty(tmp_path):
    assert load_user_key(tmp_path) == ""


def test_user_key_invalid_text_returns_empty(tmp_path):
    (tmp_path / USER_KEY_FILENAME).write_bytes(b"\xff\xfe\x00")
    assert load_user_key(tmp_path) == ""


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


class FakeVar:
    def __init__(self, value="") -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


def test_bind_saves_key_and_keeps_masked_input_value(tmp_path, monkeypatch):
    class FakeBindings:
        async def bind(self, key):
            assert key == "uak_public_secret"
            return {"agent_id": "agent-1"}

    app = SimpleNamespace(bindings=FakeBindings())
    bridge = StubBridge()
    try:
        ctrl = DesktopController(
            app,
            bridge,
            tmp_path / "state",
            tmp_path / "logs",
            "ws://t",
            runtime_dir=tmp_path / "runtime",
        )
        ctrl.key_var = FakeVar("uak_public_secret")
        ctrl._refresh_bindings = lambda: None
        messages: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "tkinter.messagebox.showinfo",
            lambda title, message: messages.append((title, message)),
        )

        ctrl._on_bind()

        assert ctrl.key_var.get() == "uak_public_secret"
        assert load_user_key(tmp_path / "runtime") == "uak_public_secret"
        assert messages and messages[0][0] == "绑定成功"
    finally:
        bridge.close()


def test_toggle_key_visibility_defaults_to_masked(tmp_path):
    app = SimpleNamespace()
    ctrl = DesktopController(app, None, tmp_path, tmp_path, "ws://t", runtime_dir=tmp_path)
    entry_options: list[dict] = []
    button_options: list[dict] = []
    ctrl.key_entry = SimpleNamespace(configure=lambda **kwargs: entry_options.append(kwargs))
    ctrl.key_visibility_button = SimpleNamespace(
        configure=lambda **kwargs: button_options.append(kwargs)
    )

    ctrl._toggle_key_visibility()
    ctrl._toggle_key_visibility()

    assert entry_options == [{"show": ""}, {"show": "•"}]
    assert button_options == [{"text": "隐藏"}, {"text": "显示"}]


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


def test_desktop_shutdown_cancels_executions_and_joins_loop_thread(tmp_path):
    """桌面窗口关闭后：异步 shutdown 取消活动执行并收敛，后台循环线程正常退出。"""
    from main import AgentApp

    loop = asyncio.new_event_loop()
    shutdown_done = threading.Event()
    thread_error: list[Exception] = []

    async def _shutdown(app) -> None:
        try:
            await app.stop_all_executions()
        finally:
            shutdown_done.set()

    app = AgentApp({})

    class FakeClient:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send(self, payload: dict) -> None:
            self.sent.append(payload)

    client = FakeClient()
    app.client = client

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        try:
            # 模拟 serve_agent 循环：不退出，等待退出指令
            loop.run_forever()
        except Exception as exc:  # pragma: no cover
            thread_error.append(exc)
        finally:
            try:
                loop.close()
            except RuntimeError:
                pass

    thread = threading.Thread(target=_run_loop, daemon=True)
    thread.start()
    bridge = AsyncBridge(loop, timeout=5)
    try:
        # 在后台循环中启动一个长执行
        bridge.call(
            lambda: app.on_message(
                {
                    "type": "start_test",
                    "execution_id": 21,
                    "session_token": "t-21",
                    "parameters": {},
                    "device": {"udid": "u-21", "platform": "android"},
                    "cases": [{"case_id": 1, "case_name": "长执行", "steps_snapshot": [{"order": 1, "action": "sleep", "params": {"duration": 60}}], "assertions_snapshot": [], "elements_snapshot": {}}],
                }
            )
        )
        pending = app.runtimes[21].task
        assert not pending.done()

        # 桌面退出：先提交异步 shutdown 等待完成，再停循环并 join
        bridge.call(lambda: _shutdown(app))
        assert shutdown_done.wait(timeout=5)
        assert pending.done()
        assert not app.runtimes

        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not thread_error
    finally:
        if thread.is_alive():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)
