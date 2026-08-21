"""Windows 方案 §4.1：桌面控制器非 UI 部件测试（设置持久化 + AsyncBridge）。"""

import asyncio
import threading

from desktop.controller import AsyncBridge, load_server_url, save_server_url


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
