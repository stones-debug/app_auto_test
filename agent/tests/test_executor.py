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
    assert "swipe_to_find" in ACTION_REGISTRY
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


async def _run_and_capture(case, parameters=None, should_stop=None, driver=None):
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    driver = driver or MockDriver()
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


# ---------- Step 4：continue_on_failure 顶层契约 ----------


async def test_runner_continue_on_failure_true_keeps_going():
    """失败后继续=true：失败步骤后仍执行下一步（整体仍为 failed）。"""
    case = _make_case(
        steps=[
            {"order": 1, "action": "no_such_action", "params": {}},
            {"order": 2, "action": "input", "element_id": 1, "params": {"value": "admin"}},
        ],
    )
    case["steps_snapshot"][0]["continue_on_failure"] = True
    status, sent = await _run_and_capture(case)
    assert status == "failed"
    step_msgs = [m for m in sent if m["type"] == "step_result"]
    assert len(step_msgs) == 2  # 失败后继续执行第二步


async def test_runner_continue_on_failure_false_stops():
    """失败后继续=false（默认）：失败步骤后停止，不再执行后续步骤。"""
    case = _make_case(
        steps=[
            {"order": 1, "action": "no_such_action", "params": {}},
            {"order": 2, "action": "input", "element_id": 1, "params": {"value": "admin"}},
        ],
    )
    status, sent = await _run_and_capture(case)
    assert status == "failed"
    step_msgs = [m for m in sent if m["type"] == "step_result"]
    assert len(step_msgs) == 1


async def test_runner_continue_on_failure_not_read_from_params():
    """continue_on_failure 只从 Step 顶层读取，params 里的遗留值不生效。"""
    case = _make_case(
        steps=[
            {"order": 1, "action": "no_such_action", "params": {"continue_on_failure": True}},
            {"order": 2, "action": "input", "element_id": 1, "params": {"value": "admin"}},
        ],
    )
    status, sent = await _run_and_capture(case)
    assert status == "failed"
    step_msgs = [m for m in sent if m["type"] == "step_result"]
    assert len(step_msgs) == 1  # params 中的遗留字段不生效，仍停止


# ---------- Step 4：click 等待秒数契约 ----------


class RecordingDriver(MockDriver):
    """记录 find_element 收到的 wait_timeout。"""

    def __init__(self) -> None:
        super().__init__()
        self.wait_timeouts: list[int | None | float] = []

    def find_element(self, locator_type: str, locator_value: str, wait_timeout: int | None = None):
        self.wait_timeouts.append(wait_timeout)
        return super().find_element(locator_type, locator_value, wait_timeout)


async def test_click_passes_wait_timeout_to_driver():
    from executor.actions import ClickAction

    driver = RecordingDriver()
    context = ExecutionContext(driver, _make_case([]))
    result = await ClickAction().execute(driver, context, {"element_id": 2, "wait_timeout": 5})
    assert result["status"] == "passed"
    assert driver.wait_timeouts == [5]


async def test_click_default_wait_timeout_none_means_default():
    from executor.actions import ClickAction

    driver = RecordingDriver()
    context = ExecutionContext(driver, _make_case([]))
    await ClickAction().execute(driver, context, {"element_id": 2})
    assert driver.wait_timeouts == [None]  # None → AppiumDriver 侧用默认 10


async def test_appium_find_element_timeout_raises_element_not_found(monkeypatch):
    from selenium.common.exceptions import TimeoutException

    from executor import ElementNotFound
    from executor.appium_driver import AppiumDriver

    class MissingSession:
        def find_element(self, by, value):
            raise TimeoutException(f"no element {value}")

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = MissingSession()
    with pytest.raises(ElementNotFound, match="元素等待超时: id=login_btn \\(3s\\)"):
        driver.find_element("id", "login_btn", wait_timeout=3)


async def test_appium_find_element_zero_timeout_no_wait(monkeypatch):
    from executor.appium_driver import AppiumDriver

    class NullSession:
        def find_element(self, by, value):
            return f"element:{value}"

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = NullSession()
    assert driver.find_element("id", "zero_wait", wait_timeout=0) == "element:zero_wait"


async def test_appium_find_element_wait_success(monkeypatch):
    from executor.appium_driver import AppiumDriver

    class ReadySession:
        def find_element(self, by, value):
            return f"element:{value}"

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = ReadySession()
    found = driver.find_element("id", "soon", wait_timeout=5)
    assert found == "element:soon"


async def test_appium_nonstandard_id_uses_exact_resource_id_xpath_directly():
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.common.exceptions import NoSuchElementException

    from executor.appium_driver import AppiumDriver

    calls: list[tuple[str, str]] = []

    class UniAppSession:
        def find_element(self, by, value):
            calls.append((by, value))
            if by == AppiumBy.XPATH and value == (
                '//*[@resource-id="src-components-l-popup-input-port"]'
            ):
                return "port-input"
            raise NoSuchElementException(value)

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = UniAppSession()

    found = driver.find_element("id", "src-components-l-popup-input-port", wait_timeout=1)

    assert found == "port-input"
    assert calls == [
        (AppiumBy.XPATH, '//*[@resource-id="src-components-l-popup-input-port"]'),
    ]


async def test_appium_standard_android_id_keeps_native_id_strategy():
    from appium.webdriver.common.appiumby import AppiumBy

    from executor.appium_driver import AppiumDriver

    calls: list[tuple[str, str]] = []

    class AndroidSession:
        def find_element(self, by, value):
            calls.append((by, value))
            return "login-button"

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = AndroidSession()

    found = driver.find_element("id", "login_btn", wait_timeout=1)

    assert found == "login-button"
    assert calls == [(AppiumBy.ID, "login_btn")]


async def test_appium_bare_xpath_is_normalized_to_resource_id_xpath():
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.common.exceptions import NoSuchElementException

    from executor.appium_driver import AppiumDriver

    calls: list[tuple[str, str]] = []

    class UniAppSession:
        def find_element(self, by, value):
            calls.append((by, value))
            if by == AppiumBy.XPATH and value == (
                '//*[@resource-id="src-views-login-btn-setServerIp"]'
            ):
                return "server-button"
            raise NoSuchElementException(value)

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = UniAppSession()

    found = driver.find_element("xpath", "src-views-login-btn-setServerIp", wait_timeout=1)

    assert found == "server-button"
    assert calls == [
        (AppiumBy.XPATH, '//*[@resource-id="src-views-login-btn-setServerIp"]'),
    ]


async def test_appium_explicit_xpath_is_not_rewritten():
    from appium.webdriver.common.appiumby import AppiumBy

    from executor.appium_driver import AppiumDriver

    xpath = '//*[@resource-id="src-views-login-btn-setServerIp"]'

    class XPathSession:
        def find_element(self, by, value):
            assert (by, value) == (AppiumBy.XPATH, xpath)
            return "server-button"

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = XPathSession()

    assert driver.find_element("xpath", xpath, wait_timeout=1) == "server-button"


async def test_appium_input_uses_editable_descendant_of_uniapp_wrapper():
    from appium.webdriver.common.appiumby import AppiumBy

    from executor.appium_driver import AppiumDriver

    class Editable:
        def __init__(self) -> None:
            self.cleared = False
            self.value = ""

        def clear(self):
            self.cleared = True

        def send_keys(self, value):
            self.value = value

    editable = Editable()

    class Wrapper:
        def find_element(self, by, value):
            assert (by, value) == (AppiumBy.CLASS_NAME, "android.widget.EditText")
            return editable

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = object()

    driver.input(Wrapper(), "116.247.83.156")

    assert editable.cleared is True
    assert editable.value == "116.247.83.156"


async def test_appium_input_keeps_direct_editable_element_when_no_child():
    from selenium.common.exceptions import NoSuchElementException

    from executor.appium_driver import AppiumDriver

    class Editable:
        def __init__(self) -> None:
            self.cleared = False
            self.value = ""

        def find_element(self, by, value):
            raise NoSuchElementException(f"{by}={value}")

        def clear(self):
            self.cleared = True

        def send_keys(self, value):
            self.value = value

    editable = Editable()
    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = object()

    driver.input(editable, "9337")

    assert editable.cleared is True
    assert editable.value == "9337"


async def test_appium_input_reports_actionable_error_for_non_editable_element():
    from selenium.common.exceptions import InvalidElementStateException, NoSuchElementException

    from executor.appium_driver import AppiumDriver
    from executor.driver import DriverError

    class DisabledElement:
        def find_element(self, by, value):
            raise NoSuchElementException(f"{by}={value}")

        def clear(self):
            raise InvalidElementStateException("not editable")

        def send_keys(self, value):
            raise AssertionError(f"不应继续输入: {value}")

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = object()

    with pytest.raises(DriverError, match="定位到的控件不可编辑"):
        driver.input(DisabledElement(), "value")


async def test_appium_clear_uses_editable_descendant():
    from executor.appium_driver import AppiumDriver

    class Editable:
        cleared = False

        def clear(self):
            self.cleared = True

    editable = Editable()

    class Wrapper:
        def find_element(self, by, value):
            return editable

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = object()

    driver.clear(Wrapper())

    assert editable.cleared is True


async def test_regex_match_assertion():
    from executor.assertions import RegexMatchAssertion

    driver = MockDriver(initial_state={"greeting": "Hello 2026"})
    context = ExecutionContext(driver, _make_case([]))
    context.elements_snapshot = {"1": {"locator_type": "id", "locator_value": "greeting"}}
    result = await RegexMatchAssertion().verify(driver, context, {"element_id": 1, "pattern": r"\d{4}"})
    assert result["status"] == "passed"


async def test_sleep_action(monkeypatch):
    slept: list[float] = []

    class FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def time(self) -> float:
            return self.t

    clock = FakeClock()

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock.t += seconds  # 每片 sleep 推进虚拟时钟

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: clock)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    from executor.actions import SleepAction

    driver = MockDriver()
    context = ExecutionContext(driver, _make_case([]))
    result = await SleepAction().execute(driver, context, {"duration": 0.5})
    assert result["status"] == "passed"
    # 分片 sleep：总量等于时长，且每次不超过轮询间隔
    assert slept and abs(sum(slept) - 0.5) < 0.01
    assert all(s <= SleepAction._POLL + 0.001 for s in slept)


async def test_sleep_action_stops_promptly():
    """stop 信号可打断分片 sleep（收敛性：线程不残留至整段时长结束）。"""
    from executor.actions import SleepAction

    driver = MockDriver()
    context = ExecutionContext(driver, _make_case([]), should_stop=lambda: True)
    with pytest.raises(StopRequested):
        await SleepAction().execute(driver, context, {"duration": 60})


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

    ios = AppiumDriver(device={"udid": "iphone-x", "platform": "ios"})
    assert ios._device_caps()["platformName"] == "iOS"
    assert ios._device_caps()["automationName"] == "XCUITest"


async def test_appium_launch_uses_options_and_udid_wins(monkeypatch):
    """Appium 6：通过 options= 建连；设备 UDID/platformName 不能被通用配置覆盖。"""
    import appium.webdriver as appium_webdriver

    calls: list[dict] = []

    class FakeSession:
        session_id = "sess-1"

    class FakeDriver:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)
            self.session_id = "sess-1"
            self._options = kwargs.get("options")

    monkeypatch.setattr(appium_webdriver, "Remote", lambda *a, **kw: FakeDriver(**kw))

    from executor.appium_driver import AppiumDriver

    # Android：设备 UDID 优先，通用配置不能覆盖
    android = AppiumDriver(
        host="127.0.0.1",
        port=4723,
        capabilities={"appium:options": {"noReset": False}, "udid": "generic-01", "platformName": "iOS"},
        device={"udid": "emulator-5554", "platform": "android"},
        command_timeout=300,
    )
    android.launch_app("com.demo.app", "com.demo.MainActivity")
    assert "options" in calls[0] and "command_executor" in calls[0]
    options = calls[0]["options"]
    assert options.get_capability("udid") == "emulator-5554"  # 设备 UDID 覆盖通用配置
    assert options.get_capability("platformName") == "Android"
    assert options.get_capability("automationName").lower() == "uiAutomator2".lower()
    assert options.get_capability("appPackage") == "com.demo.app"
    assert options.get_capability("appActivity") == "com.demo.MainActivity"
    assert options.get_capability("newCommandTimeout") == 300

    # iOS：bundleId 映射，忽略 activity，且不写入 appActivity
    ios = AppiumDriver(
        capabilities={"udid": "generic-02"},
        device={"udid": "iphone-x", "platform": "ios"},
    )
    ios.launch_app("com.demo.ios.app", "ignored.Activity")
    ios_options = calls[1]["options"]
    assert ios_options.get_capability("udid") == "iphone-x"
    assert ios_options.get_capability("platformName") == "iOS"
    assert ios_options.get_capability("automationName") == "XCUITest"
    assert ios_options.get_capability("bundleId") == "com.demo.ios.app"
    assert ios_options.get_capability("appPackage") is None
    assert ios_options.get_capability("appActivity") is None


async def test_appium_no_activity_resolves_launcher_and_waits_for_target_package(monkeypatch):
    import appium.webdriver as appium_webdriver

    from devices import adb

    captured: list[dict] = []

    def fake_remote(*args, **kwargs):
        captured.append(kwargs)

        class D:
            session_id = "sess-2"

        return D()

    monkeypatch.setattr(appium_webdriver, "Remote", fake_remote)
    monkeypatch.setattr(
        adb,
        "resolve_launcher_activity",
        lambda udid, package: "io.dcloud.PandoraEntry",
    )

    from executor.appium_driver import AppiumDriver

    driver = AppiumDriver(device={"udid": "emulator-1", "platform": "android"})
    driver.launch_app("com.uniapp.testalias", None)
    assert "options" in captured[0]
    options = captured[0]["options"]
    assert options.get_capability("appActivity") == "io.dcloud.PandoraEntry"
    assert options.get_capability("appPackage") == "com.uniapp.testalias"
    assert options.get_capability("appWaitPackage") == "com.uniapp.testalias"
    assert options.get_capability("appWaitActivity") == "*"


async def test_appium_explicit_activity_skips_adb_resolution(monkeypatch):
    import appium.webdriver as appium_webdriver

    from devices import adb

    captured: list[dict] = []
    monkeypatch.setattr(
        appium_webdriver,
        "Remote",
        lambda *args, **kwargs: type("D", (), {"session_id": "sess-3"})(),
    )
    monkeypatch.setattr(
        adb,
        "resolve_launcher_activity",
        lambda *args: (_ for _ in ()).throw(AssertionError("显式 Activity 不应调用 ADB")),
    )

    from executor.appium_driver import AppiumDriver

    driver = AppiumDriver(device={"udid": "emulator-1", "platform": "android"})
    original_build = driver._build_options

    def capture_options(package, activity, no_reset):
        options = original_build(package, activity, no_reset)
        captured.append({"options": options})
        return options

    monkeypatch.setattr(driver, "_build_options", capture_options)
    driver.launch_app("com.demo.app", "  com.demo.MainActivity  ")

    assert captured[0]["options"].get_capability("appActivity") == "com.demo.MainActivity"
    assert captured[0]["options"].get_capability("appWaitActivity") is None


async def test_appium_activity_resolution_failure_is_actionable(monkeypatch):
    from devices import adb
    from executor.appium_driver import AppiumDriver
    from executor.driver import DriverError

    def fail(_udid, _package):
        raise adb.AdbError("设备 emulator-1 未安装应用 com.missing.app")

    monkeypatch.setattr(adb, "resolve_launcher_activity", fail)
    driver = AppiumDriver(device={"udid": "emulator-1", "platform": "android"})

    with pytest.raises(DriverError, match="显式填写 Activity"):
        driver.launch_app("com.missing.app", None)


# ---------- swipe_to_find：滑动查找元素 ----------


class _FoundAfterSwipesDriver(MockDriver):
    """前 after 次查找抛 ElementNotFound，之后才命中；记录每次滑动参数。"""

    def __init__(self, after: int = 2, initial_state: dict | None = None):
        super().__init__(initial_state)
        self.attempts = 0
        self.after = after
        self.swipes: list[tuple[str, int]] = []

    def find_element(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
        self.attempts += 1
        if self.attempts <= self.after:
            from executor import ElementNotFound

            raise ElementNotFound(f"尚未出现: {locator_value}")
        from executor.driver import MockElement

        return MockElement(locator_value)

    def swipe(self, direction: str, duration: int = 500) -> None:
        self.swipes.append((direction, duration))


async def test_swipe_to_find_found_after_swipes():
    from executor.actions import ACTION_REGISTRY

    action = ACTION_REGISTRY["swipe_to_find"]()
    driver = _FoundAfterSwipesDriver(after=3)
    context = ExecutionContext(driver, _make_case([]))
    result = await action.execute(
        driver, context, {"element_id": 1, "direction": "up", "max_swipes": 5, "duration": 500}
    )
    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 3
    assert driver.swipes == [("up", 500), ("up", 500), ("up", 500)]


async def test_swipe_to_find_found_without_swipe():
    from executor.actions import ACTION_REGISTRY

    action = ACTION_REGISTRY["swipe_to_find"]()
    driver = _FoundAfterSwipesDriver(after=0)
    context = ExecutionContext(driver, _make_case([]))
    result = await action.execute(
        driver, context, {"element_id": 1, "direction": "down", "max_swipes": 3}
    )
    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 0
    assert driver.swipes == []


async def test_swipe_to_find_exhausted_raises_with_message():
    from executor import ElementNotFound
    from executor.actions import ACTION_REGISTRY

    action = ACTION_REGISTRY["swipe_to_find"]()
    driver = _FoundAfterSwipesDriver(after=99)  # 永远找不到
    context = ExecutionContext(driver, _make_case([]))
    with pytest.raises(ElementNotFound, match="滑动 2 次后仍未找到元素"):
        await action.execute(driver, context, {"element_id": 1, "max_swipes": 2})


async def test_swipe_to_find_respects_stop():
    from executor.actions import ACTION_REGISTRY

    action = ACTION_REGISTRY["swipe_to_find"]()
    driver = _FoundAfterSwipesDriver(after=99)
    context = ExecutionContext(driver, _make_case([]), should_stop=lambda: True)
    with pytest.raises(StopRequested):
        await action.execute(driver, context, {"element_id": 1, "max_swipes": 5})


async def test_swipe_to_find_step_through_runner():
    """Runner 整链：滑动 3 次后找到元素 → 步骤 passed。"""
    case = _make_case(
        steps=[
            {"order": 1, "action": "swipe_to_find", "element_id": 1, "params": {"max_swipes": 5}},
        ],
    )
    status, sent = await _run_and_capture(case, driver=_FoundAfterSwipesDriver(after=3))
    assert status == "passed"
    assert sent[0]["status"] == "passed"
