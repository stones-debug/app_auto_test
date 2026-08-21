import asyncio
from pathlib import Path

import pytest

from executor import (
    ACTION_REGISTRY,
    ASSERTION_REGISTRY,
    ExecutionContext,
    MockDriver,
    StopRequested,
    TestRunner,
)


def _make_case(steps, assertions=None, elements=None) -> dict:
    return {
        "case_id": 1,
        "case_name": "测试用例",
        "steps_snapshot": steps,
        "assertions_snapshot": assertions or [],
        "elements_snapshot": elements or {
            "1": {"locator_type": "id", "locator_value": "username"},
            "2": {"locator_type": "id", "locator_value": "login_btn"},
            "3": {"locator_type": "id", "locator_value": "welcome"},
        },
    }


async def test_registries_loaded():
    assert "click" in ACTION_REGISTRY
    assert "input" in ACTION_REGISTRY
    assert "get_text" in ACTION_REGISTRY
    assert "sleep" in ACTION_REGISTRY
    assert "text_equals" in ASSERTION_REGISTRY
    assert "element_exists" in ASSERTION_REGISTRY
    assert "regex_match" in ASSERTION_REGISTRY


async def test_mock_driver_input_get_text():
    driver = MockDriver()
    context = ExecutionContext(driver, _make_case([]))
    element = context.find_element("1")
    driver.input(element, "admin")
    assert driver.get_text(element) == "admin"


async def test_context_renders_runtime_variables():
    driver = MockDriver()
    case = _make_case([], elements={
        "1": {"locator_type": "id", "locator_value": "${user_field}"},
    })
    context = ExecutionContext(driver, case, {"user_field": "dynamic_id"})
    element = context.find_element("1")
    assert element.locator_value == "dynamic_id"


async def test_context_missing_element_snapshot_raises():
    driver = MockDriver()
    context = ExecutionContext(driver, _make_case([]))
    from executor import ElementNotFound

    with pytest.raises(ElementNotFound):
        context.find_element("999")


async def _run_and_capture(case, parameters=None, should_stop=None):
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    driver = MockDriver()
    runner = TestRunner(driver, fake_send, 100, parameters, should_stop)
    status = await runner.run_case(case)
    return status, sent


async def test_runner_passing_flow():
    case = _make_case(
        steps=[
            {"order": 1, "action": "input", "element_id": 1, "params": {"value": "admin"}},
            {"order": 2, "action": "click", "element_id": 2, "params": {}},
        ],
        assertions=[
            {"order": 1, "type": "text_equals", "element_id": 1, "params": {"expected": "admin"}},
        ],
    )
    status, sent = await _run_and_capture(case)
    assert status == "passed"
    step_types = [m["type"] for m in sent]
    assert step_types.count("step_result") == 2
    assert sent[0]["status"] == "passed"
    assertion_msg = next(m for m in sent if m["type"] == "assertion_result")
    assert assertion_msg["assertions"][0]["status"] == "passed"


async def test_runner_fails_on_mismatch_assertion():
    case = _make_case(
        steps=[{"order": 1, "action": "input", "element_id": 1, "params": {"value": "admin"}}],
        assertions=[
            {"order": 1, "type": "text_equals", "element_id": 1, "params": {"expected": "wrong"}},
        ],
    )
    status, sent = await _run_and_capture(case)
    assert status == "failed"
    assertion_msg = next(m for m in sent if m["type"] == "assertion_result")
    assert assertion_msg["assertions"][0]["status"] == "failed"
    assert assertion_msg["assertions"][0]["actual"] == "admin"


async def test_runner_unknown_action_fails():
    case = _make_case(
        steps=[{"order": 1, "action": "no_such_action", "element_id": 1, "params": {}}],
    )
    status, sent = await _run_and_capture(case)
    assert status == "failed"
    assert sent[0]["status"] == "failed"
    assert "未知动作" in (sent[0]["error_message"] or "")


async def test_runner_stop_requested():
    case = _make_case(
        steps=[
            {"order": 1, "action": "sleep", "params": {"duration": 0.01}},
            {"order": 2, "action": "click", "element_id": 2, "params": {}},
        ],
    )
    with pytest.raises(StopRequested):
        await _run_and_capture(case, should_stop=lambda: True)


async def test_regex_match_assertion():
    from executor.assertions import RegexMatchAssertion

    driver = MockDriver(initial_state={"greeting": "Hello 2026"})
    context = ExecutionContext(driver, _make_case([]))
    context.elements_snapshot = {"1": {"locator_type": "id", "locator_value": "greeting"}}
    result = await RegexMatchAssertion().verify(driver, context, {"element_id": 1, "pattern": r"\d{4}"})
    assert result["status"] == "passed"


async def test_sleep_action(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    from executor.actions import SleepAction

    driver = MockDriver()
    context = ExecutionContext(driver, _make_case([]))
    result = await SleepAction().execute(driver, context, {"duration": 2})
    assert result["status"] == "passed"
    assert slept == [2]


# ---------- Windows 方案 §2：阻塞命令不阻塞事件循环 ----------


async def test_blocking_driver_action_does_not_block_event_loop():
    """慢驱动（time.sleep 模拟 Appium 阻塞命令）执行期间，事件循环仍必须运转。"""
    import time as _time

    class SlowDriver(MockDriver):
        def find_element(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
            _time.sleep(0.3)  # 模拟阻塞的 Appium 命令
            return super().find_element(locator_type, locator_value, wait_timeout)

    case = _make_case(
        steps=[{"order": 1, "action": "click", "element_id": 2, "params": {}}],
    )
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    runner = TestRunner(SlowDriver(), fake_send, 100)
    ticks: list[int] = []

    async def ticker():
        for _ in range(8):
            await asyncio.sleep(0.05)
            ticks.append(1)

    task = asyncio.create_task(runner.run_case(case))
    t = asyncio.create_task(ticker())
    await asyncio.wait_for(task, timeout=5)
    await t
    # 若驱动同步跑在主循环上，0.3s 的 sleep 期间 ticker 无法推进
    assert len(ticks) >= 3, f"事件循环疑似被阻塞，ticker 仅推进 {len(ticks)} 次"
    assert sent[0]["status"] == "passed"


# ---------- CR-07：截图上传接线 ----------


class FakeUploader:
    def __init__(self, result: str | None) -> None:
        self.result = result
        self.calls: list[tuple[int, str, str | None]] = []

    async def upload_screenshot(self, execution_id: int, path: str, session_token: str | None = None) -> str | None:
        self.calls.append((execution_id, path, session_token))
        return self.result


def _screenshot_case() -> dict:
    return _make_case(
        steps=[{"order": 1, "action": "screenshot", "params": {"filename": "ignored.png"}}],
        elements={"1": {"locator_type": "id", "locator_value": "x"}},
    )


async def test_runner_uploads_screenshot_and_reports_server_key():
    """CR-07：截图后立即上传，step_result 只携带服务端对象键。"""
    uploader = FakeUploader("execution_100/screenshots/abc123.png")
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    runner = TestRunner(
        MockDriver(), fake_send, 100, screenshots_dir=Path("."),
        session_token="sess", uploader=uploader,
    )
    status = await runner.run_case(_screenshot_case())
    assert status == "passed"
    step_msg = sent[0]
    assert step_msg["screenshot_path"] == "execution_100/screenshots/abc123.png"
    assert len(uploader.calls) == 1
    exec_id, local_path, token = uploader.calls[0]
    assert exec_id == 100
    assert token == "sess"
    # 上传的是 Agent 本地路径
    assert local_path.endswith(".png")


async def test_runner_upload_failure_keeps_local_path_out():
    """CR-07：上传失败不得把本地路径回传服务端，须记录明确错误。"""
    uploader = FakeUploader(None)
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    runner = TestRunner(
        MockDriver(), fake_send, 100, screenshots_dir=Path("."),
        session_token="sess", uploader=uploader,
    )
    status = await runner.run_case(_screenshot_case())
    assert status == "passed"
    step_msg = sent[0]
    assert step_msg["screenshot_path"] is None
    assert "上传失败" in (step_msg["error_message"] or "")


# ---------- CR-08：真实 Appium 驱动接线 ----------


async def test_create_driver_appium_uses_config_and_device():
    """CR-08：create_driver 用 config 的 host/port/capabilities 与设备信息构造 Appium 驱动。"""
    from executor import create_driver
    from executor.appium_driver import AppiumDriver

    driver = create_driver(
        "appium",
        config={
            "appium_host": "10.0.0.8",
            "appium_port": 4730,
            "appium_capabilities": {"appium:options": {"noReset": False}},
            "appium_command_timeout": 300,
        },
        device={"udid": "emulator-5554", "platform": "android"},
    )
    assert isinstance(driver, AppiumDriver)
    assert driver.command_executor == "http://10.0.0.8:4730"
    assert driver.command_timeout == 300

    caps = driver._build_caps("com.demo.app", "com.demo.MainActivity", True)
    assert caps["udid"] == "emulator-5554"
    assert caps["platformName"] == "Android"
    assert caps["automationName"] == "UiAutomator2"
    assert caps["appPackage"] == "com.demo.app"
    assert caps["newCommandTimeout"] == 300

    ios = AppiumDriver(device={"udid": "iphone-x", "platform": "ios"})
    assert ios._device_caps()["platformName"] == "iOS"
    assert ios._device_caps()["automationName"] == "XCUITest"
