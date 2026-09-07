import asyncio
import sys
from pathlib import Path

import pytest

from main import (
    BANNER,
    AgentApp,
    build_agent_app,
    default_config_path,
    http_origin,
    load_config,
    print_banner,
    should_run_desktop,
)


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.parametrize(
    ("explicit_desktop", "frozen", "expected"),
    [
        (False, False, False),  # python main.py：保持无头模式
        (True, False, True),  # python main.py --desktop：显式桌面模式
        (False, True, True),  # 打包 EXE 双击/开始菜单：默认桌面模式
        (True, True, True),
    ],
)
def test_desktop_mode_selection(explicit_desktop: bool, frozen: bool, expected: bool):
    assert should_run_desktop(explicit_desktop, frozen=frozen) is expected


def test_default_config_path_uses_agent_directory_when_unfrozen():
    assert default_config_path() == Path(__file__).resolve().parent.parent / "config.yaml"


def test_default_config_path_uses_executable_directory_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app-auto-test-agent.exe"))
    assert default_config_path() == tmp_path / "config.yaml"


def test_load_config_missing_path_returns_empty_config(tmp_path):
    assert load_config(str(tmp_path / "missing.yaml")) == {}


def test_packaged_default_config_is_safe_and_has_appium_timeouts():
    config_path = Path(__file__).resolve().parent.parent / "packaging" / "config.yaml"
    config = load_config(str(config_path))
    assert config["server"] == "ws://192.168.100.7:8001/ws/agent"
    assert config["appium_command_timeout"] == 300
    assert config["appium_http_request_timeout"] == 30
    assert not {"agent_key", "agent_id", "machine_psk", "user_key"} & config.keys()


def test_publish_places_config_at_onedir_root_and_installer_uses_it():
    root = Path(__file__).resolve().parent.parent
    spec = (root / "packaging" / "pyinstaller.spec").read_text(encoding="utf-8")
    publish = (root / "packaging" / "publish.ps1").read_text(encoding="utf-8")
    setup = (root / "packaging" / "setup.iss").read_text(encoding="utf-8")

    assert '(str(project_root / "packaging" / "config.yaml"), ".")' not in spec
    assert '$defaultConfig = Join-Path $PSScriptRoot "config.yaml"' in publish
    assert '$bundledConfig = Join-Path $bundledApp "config.yaml"' in publish
    assert 'Copy-Item -LiteralPath $defaultConfig -Destination $bundledConfig -Force' in publish
    assert 'Source: "{#SourceDir}\\config.yaml"; DestDir: "{app}"' in setup
    assert "onlyifdoesntexist" in setup


def test_banner_contains_brand(capsys: pytest.CaptureFixture[str]) -> None:
    """启动横幅包含品牌标识与版本信息（console 模式）。"""
    assert BANNER.strip()
    print_banner()
    out = capsys.readouterr().out
    assert "APP" in out
    assert "Device Agent" in out


def test_build_agent_app_uses_install_identity_and_machine_psk():
    class Bindings:
        def machine_psk(self):
            return "sk-machine"

    app, client = build_agent_app(
        {"server": "ws://127.0.0.1:8001/ws/agent", "driver": "mock"},
        "agent-install-id",
        Bindings(),
    )

    assert client.agent_id == "agent-install-id"
    assert client._resolve_key() == "sk-machine"
    assert app.uploader.agent_id == "agent-install-id"
    assert app.uploader._resolve_key() == "sk-machine"


def _sleep_suite(duration: float = 5) -> list[dict]:
    return [
        {
            "execution_suite_id": 1001,
            "suite_id": None,
            "suite_name": "虚拟套件",
            "suite_order": 1,
            "is_virtual": True,
            "setup_steps": [],
            "cases": [
                {
                    "execution_case_id": 2001,
                    "case_id": 1,
                    "case_name": "睡眠用例",
                    "steps_snapshot": [{"order": 1, "execution_step_id": 3001, "action": "sleep", "params": {"duration": duration}}],
                    "assertions_snapshot": [],
                    "elements_snapshot": {},
                }
            ],
            "teardown_steps": [],
        }
    ]


async def test_start_test_runs_in_background_task():
    """CR-06：start_test 创建独立任务，不阻塞接收循环。"""
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 1,
            "session_token": "t-1",
            "parameters": {},
            "device": {"udid": "u-1", "platform": "android"},
            "suites": _sleep_suite(0.01),
        }
    )
    assert 1 in app.runtimes
    runtime = app.runtimes[1]
    assert runtime.cancel_event is not None
    await asyncio.wait_for(runtime.task, timeout=2)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "passed"
    assert 1 not in app.runtimes  # 完成回调清理


async def test_execution_result_is_retained_until_matching_server_ack():
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()

    await app._send_execution_result_safe(77, "session-77", "passed")
    assert app._pending_execution_results[77]["status"] == "passed"

    # 其它会话的确认不能清除当前执行结果。
    await app.on_message(
        {"type": "execution_result_ack", "execution_id": 77, "session_token": "stale-session"}
    )
    assert 77 in app._pending_execution_results

    await app.on_message(
        {"type": "execution_result_ack", "execution_id": 77, "session_token": "session-77"}
    )
    assert 77 not in app._pending_execution_results


async def test_reregister_resends_current_device_snapshot_without_blocking_callback():
    class ReconnectedRegistry:
        async def start(self, _callback):
            return False

        def current(self):
            return [{"udid": "device-1", "status": "idle"}]

    app = AgentApp({"driver": "mock"}, registry=ReconnectedRegistry())
    app.client = FakeClient()

    await app.on_registered({"status": "ok"})
    assert app._result_replay_task is not None
    await app._result_replay_task

    assert app.client.sent == [
        {
            "type": "device_list",
            "devices": [{"udid": "device-1", "status": "idle"}],
        }
    ]


async def test_start_test_current_screen_mode_attaches_driver(monkeypatch):
    from executor.driver import MockDriver

    driver = MockDriver()
    monkeypatch.setattr("main.create_driver", lambda *args, **kwargs: driver)
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 2,
            "session_token": "t-2",
            "parameters": {"attach_to_current_app": True},
            "device": {"udid": "u-2", "platform": "android"},
            "suites": _sleep_suite(0.01),
        }
    )

    await asyncio.wait_for(app.runtimes[2].task, timeout=2)

    assert driver.launched is True
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "passed"


async def test_stop_test_interrupts_blocking_execution():
    """CR-06：stop_test 立即生效（打断 sleep 等阻塞动作）并上报 stopped。"""
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 7,
            "session_token": "t-7",
            "parameters": {},
            "device": {"udid": "u-7", "platform": "android"},
            "suites": _sleep_suite(30),  # 长 sleep，等待 stop 打断
        }
    )
    await asyncio.sleep(0.05)
    await app.on_message({"type": "stop_test", "execution_id": 7})

    await asyncio.wait_for(app.runtimes[7].task, timeout=2)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "stopped"
    assert 7 not in app.runtimes


async def test_duplicate_start_test_ignored():
    """CR-06：同一 execution 重复 start_test 被忽略。"""
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    msg = {
        "type": "start_test",
        "execution_id": 9,
        "session_token": "t-9",
        "parameters": {},
        "device": {"udid": "u-9", "platform": "android"},
        "suites": _sleep_suite(0.01),
    }
    await app.on_message(msg)
    task = app.runtimes[9].task
    await app.on_message(msg)  # 重复消息
    assert app.runtimes[9].task is task  # 未创建新任务
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.parametrize(
    ("ws_url", "expected"),
    [
        ("ws://127.0.0.1:8001/ws/agent", "http://127.0.0.1:8001"),
        ("ws://test-server.example/ws/agent", "http://test-server.example"),
        ("ws://[::1]:8001/ws/agent", "http://[::1]:8001"),
        ("ws://10.0.0.5:9000/ws/agent", "http://10.0.0.5:9000"),
        ("wss://secure-host.test/ws/agent?x=1#frag", "https://secure-host.test"),
        ("ws://host:8001", "http://host:8001"),
    ],
)
def test_http_origin_maps_ws_to_http(ws_url: str, expected: str):
    assert http_origin(ws_url) == expected


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:8001/ws/agent",  # 非法 scheme
        "ftp://host/ws",  # 非法 scheme
        "ws://",  # 无 hostname
        "ws://user:pass@host/ws",  # 内嵌凭据
    ],
)
def test_http_origin_rejects_invalid_urls(bad_url: str):
    with pytest.raises(ValueError):
        http_origin(bad_url)


# ---------- Step 3：执行资源生命周期收敛 ----------


class FakeAppium:
    """AppiumServer 替身：记录 start/stop 次数，wait_ready 立即成功。"""

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    @property
    def running(self) -> bool:
        return self.starts > self.stops

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1

    async def wait_ready(self) -> None:
        return None


async def test_concurrent_appium_executions_start_stop_server_once():
    """并发两个 appium 执行只启动一次 Server，最后一个结束后只停止一次。"""
    appium = FakeAppium()
    app = AgentApp({"driver": "appium"}, appium=appium)
    app.client = FakeClient()

    async def start(eid: int, duration: float) -> None:
        await app.on_message(
            {
                "type": "start_test",
                "execution_id": eid,
                "session_token": f"t-{eid}",
                "parameters": {},
                "device": {"udid": f"u-{eid}", "platform": "android"},
                "suites": _sleep_suite(duration),
            }
        )

    await start(1, 1.2)
    await start(2, 0.3)
    # 等待两个执行任务真正运行（_run_execution 在后台 task 中启动）
    await asyncio.sleep(0.15)
    assert appium.starts == 1  # 并发执行复用同一 Server
    assert 2 in app.runtimes and 1 in app.runtimes

    await asyncio.wait_for(app.runtimes[2].task, timeout=5)
    assert appium.starts == 1  # 第一个结束后不停 Server（还有执行在用）
    assert 1 in app.runtimes
    await asyncio.wait_for(app.runtimes[1].task, timeout=5)
    assert appium.stops == 1  # 最后一个结束后才停止
    assert not app.runtimes


async def test_ensure_appium_failure_recovers_refs_and_reports_error(monkeypatch):
    class FlakyAppium(FakeAppium):
        async def wait_ready(self) -> None:
            raise RuntimeError("appium 起不来")

    appium = FlakyAppium()
    app = AgentApp({"driver": "appium"}, appium=appium)
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 3,
            "session_token": "t-3",
            "parameters": {},
            "device": {"udid": "u-3", "platform": "android"},
            "suites": _sleep_suite(0.01),
        }
    )
    await asyncio.wait_for(app.runtimes[3].task, timeout=5)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "error"
    assert "appium 起不来" in (results[0].get("error_message") or "")
    assert app._appium_refs == 0
    assert 3 not in app.runtimes


async def test_create_driver_failure_reports_error_and_cleans(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("驱动构造失败")

    monkeypatch.setattr("main.create_driver", boom)
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 4,
            "session_token": "t-4",
            "parameters": {},
            "device": {"udid": "u-4", "platform": "android"},
            "suites": _sleep_suite(0.01),
        }
    )
    await asyncio.wait_for(app.runtimes[4].task, timeout=5)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "error"
    assert "驱动构造失败" in (results[0].get("error_message") or "")
    assert 4 not in app.runtimes


async def test_tmpdir_failure_reports_error(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("临时目录创建失败")

    monkeypatch.setattr("main.tempfile.TemporaryDirectory", boom)
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 5,
            "session_token": "t-5",
            "parameters": {},
            "device": {"udid": "u-5", "platform": "android"},
            "suites": _sleep_suite(0.01),
        }
    )
    await asyncio.wait_for(app.runtimes[5].task, timeout=5)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "error"
    assert 5 not in app.runtimes


async def test_driver_quit_failure_does_not_hide_result(monkeypatch):
    class BadQuitDriver:
        def quit(self) -> None:
            raise RuntimeError("quit 失败")

    def bad_driver(*args, **kwargs):
        return BadQuitDriver()

    monkeypatch.setattr("main.create_driver", bad_driver)
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 6,
            "session_token": "t-6",
            "parameters": {},
            "device": {"udid": "u-6", "platform": "android"},
            "suites": _sleep_suite(0.01),
        }
    )
    await asyncio.wait_for(app.runtimes[6].task, timeout=5)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    # 清理异常不能覆盖原始 passed 结果
    assert results and results[0]["status"] == "passed"
    assert 6 not in app.runtimes


async def test_ws_send_failure_does_not_crash_done_callback():
    class BrokenClient:
        async def send(self, payload: dict) -> None:
            raise ConnectionError("WS 已断开")

    app = AgentApp({"driver": "mock"})
    app.client = BrokenClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 8,
            "session_token": "t-8",
            "parameters": {},
            "device": {"udid": "u-8", "platform": "android"},
            "suites": _sleep_suite(0.01),
        }
    )
    await asyncio.wait_for(app.runtimes[8].task, timeout=5)
    assert 8 not in app.runtimes  # 完成回调正常清理，未被异常打断


async def test_stop_and_completion_race_sends_single_terminal_result():
    """stop 与正常完成竞争：确保只发送一个终态。"""
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 10,
            "session_token": "t-10",
            "parameters": {},
            "device": {"udid": "u-10", "platform": "android"},
            "suites": _sleep_suite(0.01),
        }
    )
    await asyncio.sleep(0.001)
    await app.on_message({"type": "stop_test", "execution_id": 10})
    await asyncio.wait_for(app.runtimes[10].task, timeout=5)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert len(results) == 1
    assert results[0]["status"] in ("passed", "stopped")
    assert 10 not in app.runtimes


async def test_stop_all_executions_gathers_pending_tasks():
    """进程 shutdown：有活动任务时 stop_all_executions 取消并收敛，无 pending 残留。"""
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 11,
            "session_token": "t-11",
            "parameters": {},
            "device": {"udid": "u-11", "platform": "android"},
            "suites": _sleep_suite(60),
        }
    )
    pending = app.runtimes[11].task
    assert not pending.done()
    # 等任务进入执行体（否则取消发生在协程启动前，不产生 stopped 上报——语义等价）
    await asyncio.sleep(0.05)
    await app.stop_all_executions()
    assert pending.done()
    assert not app.runtimes
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "stopped"


async def test_stop_test_unknown_execution_is_noop():
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message({"type": "stop_test", "execution_id": 999})
    assert 999 not in app.runtimes


async def test_start_test_wrong_protocol_version_reports_error():
    """Step 10：start_test 携带不兼容 protocol_version → 上报明确 error，不启动执行。"""
    from executor.protocol import protocol_version

    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 31,
            "session_token": "t-31",
            "protocol_version": "999.0.0",
            "parameters": {},
            "device": {"udid": "u-31", "platform": "android"},
"suites": [],
        }
    )
    assert 31 not in app.runtimes  # 未创建运行时/任务
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "error"
    assert "协议版本不兼容" in (results[0].get("error_message") or "")
    assert protocol_version() in (results[0].get("error_message") or "")


async def test_start_test_matching_protocol_version_runs():
    """Step 10：protocol_version 匹配时正常执行。"""
    from executor.protocol import protocol_version

    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 32,
            "session_token": "t-32",
            "protocol_version": protocol_version(),
            "parameters": {},
            "device": {"udid": "u-32", "platform": "android"},
            "suites": _sleep_suite(0.01),
        }
    )
    assert 32 in app.runtimes
    await asyncio.wait_for(app.runtimes[32].task, timeout=2)


# ---------- Step 7.1：_run_execution 套件结果一次聚合（与顺序无关） ----------


def _multi_suites() -> list[dict]:
    return [
        {
            "execution_suite_id": 1001,
            "suite_id": None,
            "suite_name": "套件A",
            "suite_order": 1,
            "is_virtual": True,
            "setup_steps": [],
            "cases": [],
            "teardown_steps": [],
        },
        {
            "execution_suite_id": 1002,
            "suite_id": None,
            "suite_name": "套件B",
            "suite_order": 2,
            "is_virtual": True,
            "setup_steps": [],
            "cases": [],
            "teardown_steps": [],
        },
    ]


@pytest.mark.parametrize(
    "suite_results",
    [
        ("failed", "error"),
        ("error", "failed"),
        ("stopped", "error"),
        ("error", "stopped"),
    ],
)
async def test_run_execution_aggregates_suite_statuses_order_invariant(monkeypatch, suite_results):
    """[error, failed] 与 [failed, error] 等任意排列必须得到相同终态（error > failed > stopped）。"""
    from executor import TestRunner

    async def fake_run_suite(self, suite):
        return suite_results[int(suite["suite_order"]) - 1]

    monkeypatch.setattr(TestRunner, "run_suite", fake_run_suite)
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 41,
            "session_token": "t-41",
            "parameters": {},
            "device": {"udid": "u-41", "platform": "android"},
            "suites": _multi_suites(),
        }
    )
    await asyncio.wait_for(app.runtimes[41].task, timeout=2)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "error"


@pytest.mark.parametrize(
    ("suite_results", "expected"),
    [
        (("failed", "stopped"), "failed"),
        (("stopped", "failed"), "failed"),
        (("passed", "failed"), "failed"),
    ],
)
async def test_run_execution_aggregates_failed_over_stopped(monkeypatch, suite_results, expected):
    from executor import TestRunner

    async def fake_run_suite(self, suite):
        return suite_results[int(suite["suite_order"]) - 1]

    monkeypatch.setattr(TestRunner, "run_suite", fake_run_suite)
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 42,
            "session_token": "t-42",
            "parameters": {},
            "device": {"udid": "u-42", "platform": "android"},
            "suites": _multi_suites(),
        }
    )
    await asyncio.wait_for(app.runtimes[42].task, timeout=2)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == expected


async def test_run_execution_all_suites_skipped_maps_to_passed(monkeypatch):
    """全部套件 skipped/N/A 时顶层映射为 passed（权威口径 V1.1 §10.14，非循环默认值巧合）。"""
    from executor import TestRunner

    async def fake_run_suite(self, suite):
        return "skipped"

    monkeypatch.setattr(TestRunner, "run_suite", fake_run_suite)
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 43,
            "session_token": "t-43",
            "parameters": {},
            "device": {"udid": "u-43", "platform": "android"},
            "suites": _multi_suites(),
        }
    )
    await asyncio.wait_for(app.runtimes[43].task, timeout=2)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "passed"
