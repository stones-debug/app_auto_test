"""Windows 方案 §4.1：Appium 生命周期测试（启动/就绪/清理子进程树）。"""

import sys
import time
from pathlib import Path

import pytest

from appium_lifecycle import AppiumError, AppiumServer, resolve_android_sdk_root


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
    monkeypatch.setattr("appium_lifecycle.bundled_appium", lambda: None)
    monkeypatch.setattr("appium_lifecycle.dev_bundled_appium", lambda: None)
    server = AppiumServer()
    with pytest.raises(AppiumError, match="未找到 appium"):
        server._command()


def test_command_resolution_dev_vendor(monkeypatch):
    """源码/调试模式：仓库 vendor/appium 兜底（无需显式配置）。"""
    node = Path("D:/vendor/appium/node/node.exe")
    main_js = Path("D:/vendor/appium/appium/node_modules/appium/build/lib/main.js")
    monkeypatch.setattr(
        "appium_lifecycle.dev_bundled_appium",
        lambda: (node, main_js, None),
    )
    server = AppiumServer(port=4730)
    cmd = server._command()
    assert cmd[:2] == [str(node), str(main_js)]
    assert "--port" in cmd and cmd[cmd.index("--port") + 1] == "4730"


def test_command_resolution_uses_agent_vendor_appium():
    """源码直接执行 main.py 时必须定位 agent/vendor/appium。"""
    vendor = Path(__file__).resolve().parents[1] / "vendor" / "appium"
    if not (vendor / "node" / "node.exe").is_file():
        pytest.skip("agent vendor Appium 未提供")

    server = AppiumServer()
    cmd = server._command()

    assert Path(cmd[0]).resolve() == (vendor / "node" / "node.exe").resolve()
    assert Path(cmd[1]).resolve() == (
        vendor / "appium" / "node_modules" / "appium" / "build" / "lib" / "main.js"
    ).resolve()


def test_resolve_android_sdk_root_from_environment(tmp_path, monkeypatch):
    sdk = tmp_path / "Android" / "Sdk"
    (sdk / "platform-tools").mkdir(parents=True)
    monkeypatch.setenv("ANDROID_HOME", str(sdk))
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr("appium_lifecycle.shutil.which", lambda _name: None)
    assert resolve_android_sdk_root() == sdk.resolve()


def test_resolve_android_sdk_root_from_agent_vendor(monkeypatch):
    vendor = Path(__file__).resolve().parents[1] / "vendor"
    if not (vendor / "platform-tools").is_dir():
        pytest.skip("agent vendor platform-tools 未提供")
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr("appium_lifecycle.shutil.which", lambda _name: None)
    assert resolve_android_sdk_root() == vendor.resolve()


def test_start_sets_android_sdk_environment(tmp_path, monkeypatch):
    sdk = tmp_path / "Android" / "Sdk"
    (sdk / "platform-tools").mkdir(parents=True)
    captured: dict = {}

    class FakeProcess:
        pid = 123

        @staticmethod
        def poll():
            return None

    def fake_popen(*_args, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr("appium_lifecycle.subprocess.Popen", fake_popen)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr("appium_lifecycle.shutil.which", lambda _name: None)
    server = AppiumServer(command=["node", "appium.js"], android_sdk_root=sdk)
    server.start()

    child_env = captured["env"]
    assert child_env["ANDROID_HOME"] == str(sdk.resolve())
    assert child_env["ANDROID_SDK_ROOT"] == str(sdk.resolve())
    assert child_env["PATH"].split(";")[:2] == [
        str((sdk / "platform-tools").resolve()),
        str((sdk / "emulator").resolve()),
    ]


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
