"""Windows 方案 §4.1：Appium 生命周期测试（启动/就绪/清理子进程树）。"""

import sys
import time
from pathlib import Path

import pytest

from appium_lifecycle import AppiumError, AppiumServer


@pytest.fixture
def sleepy_script(tmp_path: Path) -> Path:
    script = tmp_path / "sleepy.py"
    script.write_text("import time; time.sleep(300)\n", encoding="utf-8")
    return script


def test_command_resolution(monkeypatch):
    # 显式 command 优先
    server = AppiumServer(command=["node", "/opt/appium.js"])
    assert server._command() == ["node", "/opt/appium.js"]

    # appium_bin
    server = AppiumServer(appium_bin="my-appium", port=4730)
    cmd = server._command()
    assert cmd[0] == "my-appium"
    assert "--port" in cmd and cmd[cmd.index("--port") + 1] == "4730"

    # node_bin + appium_js
    server = AppiumServer(node_bin="node18", appium_js="C:/appium/main.js")
    cmd = server._command()
    assert cmd[:2] == ["node18", "C:/appium/main.js"]
    assert "--port" in cmd

    # 都没有且 PATH 无 appium → 明确错误
    monkeypatch.setattr("appium_lifecycle.shutil.which", lambda name: None)
    server = AppiumServer()
    with pytest.raises(AppiumError, match="未找到 appium"):
        server._command()


async def test_start_stop_lifecycle(sleepy_script):
    server = AppiumServer(
        command=[sys.executable, str(sleepy_script)],
        ready_timeout=0.5,
        probe=lambda url: False,
    )
    assert not server.running
    server.start()
    assert server.running
    server.start()  # 幂等：已启动不再重复拉起

    server.stop()
    assert not server.running
    server.stop()  # 幂等


async def test_wait_ready_ok(sleepy_script):
    server = AppiumServer(
        command=[sys.executable, str(sleepy_script)],
        ready_timeout=5,
        probe=lambda url: url.endswith("/status"),
    )
    server.start()
    try:
        await server.wait_ready()
    finally:
        server.stop()


async def test_wait_ready_timeout(sleepy_script):
    server = AppiumServer(
        command=[sys.executable, str(sleepy_script)],
        ready_timeout=0.3,
        probe=lambda url: False,
    )
    server.start()
    try:
        started = time.monotonic()
        with pytest.raises(AppiumError, match="启动超时"):
            await server.wait_ready()
        assert time.monotonic() - started < 5
    finally:
        server.stop()


async def test_start_missing_binary_raises(monkeypatch):
    monkeypatch.setattr("appium_lifecycle.shutil.which", lambda name: None)
    server = AppiumServer(appium_bin="no-such-appium-xyz", ready_timeout=0.5, probe=lambda url: False)
    with pytest.raises(FileNotFoundError):
        server.start()
    assert not server.running
