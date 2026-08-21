import asyncio

import pytest

from main import AgentApp, http_origin


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)


def _sleep_case(duration: float = 5) -> list[dict]:
    return [
        {
            "case_id": 1,
            "case_name": "睡眠用例",
            "steps_snapshot": [{"order": 1, "action": "sleep", "params": {"duration": duration}}],
            "assertions_snapshot": [],
            "elements_snapshot": {},
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
            "cases": _sleep_case(0.01),
        }
    )
    assert 1 in app.runtimes
    runtime = app.runtimes[1]
    assert runtime.cancel_event is not None
    await asyncio.wait_for(runtime.task, timeout=2)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "passed"
    assert 1 not in app.runtimes  # 完成回调清理


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
            "cases": _sleep_case(30),  # 长 sleep，等待 stop 打断
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
        "cases": _sleep_case(0.01),
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
                "cases": _sleep_case(duration),
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
            "cases": _sleep_case(0.01),
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
            "cases": _sleep_case(0.01),
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
            "cases": _sleep_case(0.01),
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
            "cases": _sleep_case(0.01),
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
            "cases": _sleep_case(0.01),
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
            "cases": _sleep_case(0.01),
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
            "cases": _sleep_case(60),
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
