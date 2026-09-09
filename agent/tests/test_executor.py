import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from executor import (
    ACTION_REGISTRY,
    ASSERTION_REGISTRY,
    ExecutionContext,
    MockDriver,
    StopRequested,
    TestRunner,
)
from executor.driver import MockElement, StaleObjectException
from executor.status import aggregate_statuses
from executor.test_runner import _assertion_failure_message


def _make_case(steps, assertions=None, elements=None) -> dict:
    normalized_steps = [
        {"execution_step_id": 3000 + index, **step}
        for index, step in enumerate(steps, start=1)
    ]
    normalized_assertions = [
        {"execution_assertion_id": 4000 + index, **assertion}
        for index, assertion in enumerate(assertions or [], start=1)
    ]
    if normalized_assertions and normalized_steps:
        target = next(
            (step for step in reversed(normalized_steps) if step.get("phase") not in {"teardown", "case_teardown"}),
            normalized_steps[-1],
        )
        target["assertions"] = normalized_assertions
    return {
        "execution_case_id": 2001,
        "case_id": 1,
        "case_name": "测试用例",
        "steps_snapshot": normalized_steps,
        "elements_snapshot": elements or {
            "1": {"locator_type": "id", "locator_value": "username"},
            "2": {"locator_type": "id", "locator_value": "login_btn"},
            "3": {"locator_type": "id", "locator_value": "welcome"},
        },
    }


def _make_suite(cases, setup_steps=None, teardown_steps=None, suite_id=None, elements_snapshot=None) -> dict:
    normalized_setup = [
        {"execution_step_id": 5000 + index, **step}
        for index, step in enumerate(setup_steps or [], start=1)
    ]
    normalized_teardown = [
        {"execution_step_id": 6000 + index, **step}
        for index, step in enumerate(teardown_steps or [], start=1)
    ]
    return {
        "execution_suite_id": 1001,
        "suite_id": suite_id,
        "suite_name": "基础功能",
        "suite_order": 1,
        "is_virtual": suite_id is None,
        "elements_snapshot": elements_snapshot or {},
        "setup_steps": normalized_setup,
        "cases": cases,
        "teardown_steps": normalized_teardown,
    }


async def test_registries_loaded():
    assert "click" in ACTION_REGISTRY
    assert "input" in ACTION_REGISTRY
    assert "set_checked" in ACTION_REGISTRY
    assert "set_slider_value" in ACTION_REGISTRY
    assert "get_text" in ACTION_REGISTRY
    assert "sleep" in ACTION_REGISTRY
    assert "swipe_to_find" in ACTION_REGISTRY
    assert "text_equals" in ASSERTION_REGISTRY
    assert "text_not_equals" in ASSERTION_REGISTRY
    assert "checked" in ASSERTION_REGISTRY
    assert "element_exists" in ASSERTION_REGISTRY
    assert "regex_match" in ASSERTION_REGISTRY
    assert "number_compare" in ASSERTION_REGISTRY


async def test_mock_driver_input_get_text():
    driver = MockDriver()
    context = ExecutionContext(driver, _make_case([]))
    element = context.find_element("1")
    driver.input(element, "admin")
    assert driver.get_text(element) == "admin"


async def test_set_checked_only_clicks_when_state_differs():
    from executor.actions import SetCheckedAction

    class RecordingCheckboxDriver(MockDriver):
        def __init__(self):
            super().__init__()
            self.click_count = 0

        def click(self, element):
            self.click_count += 1
            super().click(element)

    driver = RecordingCheckboxDriver()
    driver.set_screen([
        {"id": "agree", "class_name": "android.widget.CheckBox", "checked": False},
    ])
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "id", "locator_value": "agree"}}),
    )
    action = SetCheckedAction()

    unchanged = await action.execute(driver, context, {"element_id": 1, "checked": False})
    assert unchanged == {
        "status": "passed",
        "expected": "unchecked",
        "changed": False,
    }
    assert driver.click_count == 0

    changed = await action.execute(driver, context, {"element_id": 1, "checked": True})
    assert changed["status"] == "passed"
    assert changed["changed"] is True
    assert driver.click_count == 1
    assert driver.is_checked(context.find_element("1")) is True

    still_checked = await action.execute(driver, context, {"element_id": 1, "checked": True})
    assert still_checked["changed"] is False
    assert driver.click_count == 1


async def test_checked_assertion_reports_current_state():
    from executor.assertions import CheckedAssertion

    driver = MockDriver()
    driver.set_screen([
        {"id": "agree", "class_name": "android.widget.CheckBox", "checked": True},
    ])
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "id", "locator_value": "agree"}}),
    )
    assertion = CheckedAssertion()

    passed = await assertion.verify(driver, context, {"element_id": 1, "checked": True})
    assert passed == {
        "status": "passed",
        "expected": "checked",
        "actual": "checked",
    }

    failed = await assertion.verify(driver, context, {"element_id": 1, "checked": False})
    assert failed == {
        "status": "failed",
        "expected": "unchecked",
        "actual": "checked",
    }


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


async def test_resource_id_input_and_clear_auto_select_edit_text():
    from executor import ElementNotFound
    from executor.actions import ClearAction, InputAction

    resource_id = "src-components-l-popup-input-ip"
    editable_locator = f"{resource_id}//android.widget.EditText"

    class MissingResourceIdDriver(MockDriver):
        def find_element(self, locator_type, locator_value, wait_timeout=10):
            if locator_type == "resource_id" and locator_value == resource_id:
                raise ElementNotFound(f"元素不存在: {locator_value}")
            return super().find_element(locator_type, locator_value, wait_timeout)

    driver = MissingResourceIdDriver()
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "resource_id", "locator_value": resource_id}}),
    )

    await InputAction().execute(driver, context, {"element_id": 1, "value": "116.247.83.156"})
    assert driver.state[editable_locator] == "116.247.83.156"

    await ClearAction().execute(driver, context, {"element_id": 1})
    assert driver.state[editable_locator] == ""


async def test_resource_id_input_and_clear_prefers_direct_edit_text():
    from executor.actions import ClearAction, InputAction

    resource_id = "src-components-l-popup-input-ip"
    driver = MockDriver()
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "resource_id", "locator_value": resource_id}}),
    )

    await InputAction().execute(driver, context, {"element_id": 1, "value": "116.247.83.156"})
    assert driver.state[resource_id] == "116.247.83.156"
    assert f"{resource_id}//android.widget.EditText" not in driver.state

    await ClearAction().execute(driver, context, {"element_id": 1})
    assert driver.state[resource_id] == ""


async def test_resource_id_input_and_clear_falls_back_when_target_not_editable():
    from executor.actions import ClearAction, InputAction
    from executor.driver import DriverError

    resource_id = "src-components-l-popup-input-ip"
    editable_locator = f"{resource_id}//android.widget.EditText"

    class NonEditableResourceIdDriver(MockDriver):
        def input(self, element, value, clear_first=True):
            if element.locator_value == resource_id:
                raise DriverError("目标不可编辑")
            return super().input(element, value, clear_first)

        def clear(self, element):
            if element.locator_value == resource_id:
                raise DriverError("目标不可编辑")
            return super().clear(element)

    driver = NonEditableResourceIdDriver()
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "resource_id", "locator_value": resource_id}}),
    )

    await InputAction().execute(driver, context, {"element_id": 1, "value": "116.247.83.156"})
    assert driver.state[editable_locator] == "116.247.83.156"

    await ClearAction().execute(driver, context, {"element_id": 1})
    assert driver.state[editable_locator] == ""


async def test_resource_id_non_editable_lookup_keeps_resource_node():
    resource_id = "src-views-login-btn-setServerIp"
    driver = MockDriver()
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "resource_id", "locator_value": resource_id}}),
    )

    element = context.find_element(1)

    assert element.locator_value == resource_id


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
    assert step_types.count("log") == 2
    assert sent[0]["status"] == "passed"
    log_messages = [m for m in sent if m["type"] == "log"]
    assert log_messages[0]["level"] == "INFO"
    assert "步骤 1 input 执行通过" in log_messages[0]["message"]
    assertion_msg = next(m for m in sent if m["type"] == "assertion_result")
    assert assertion_msg["assertions"][0]["status"] == "passed"


async def test_runner_v2_paces_consecutive_actions(monkeypatch):
    """V2 连续动作之间补足 500ms，等待使用 runner 的单调时钟。"""
    from executor import test_runner as runner_module

    class FakeClock:
        value = 100.0

        @classmethod
        def monotonic(cls):
            return cls.value

    action_times: list[float] = []
    sleeps: list[float] = []

    def fake_action(_action_cls, _driver, _context, _params):
        action_times.append(FakeClock.value)
        return {"status": "passed"}

    async def fake_sleep(duration):
        sleeps.append(duration)
        FakeClock.value += duration

    class PacingAction:
        async def execute(self, _driver, _context, _params):
            return {"status": "passed"}

    monkeypatch.setitem(ACTION_REGISTRY, "_pacing_test", PacingAction)
    monkeypatch.setattr(runner_module, "_run_action_in_thread", fake_action)
    monkeypatch.setattr(runner_module, "time", SimpleNamespace(monotonic=FakeClock.monotonic))
    monkeypatch.setattr(runner_module.asyncio, "sleep", fake_sleep)

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    case = _make_case([
        {"order": 1, "action": "_pacing_test", "params": {}},
        {"order": 2, "action": "_pacing_test", "params": {}},
    ])
    status = await TestRunner(MockDriver(), send, 100).run_case(case)

    assert status == "passed"
    assert action_times == [100.0, 100.5]
    assert sum(sleeps) == pytest.approx(0.5)


async def test_runner_v3_paces_actions_across_assertion(monkeypatch):
    """V3 中断言/上报耗时后，下一动作仍以最近动作完成时刻为基准。"""
    from executor import test_runner as runner_module

    class FakeClock:
        value = 200.0

        @classmethod
        def monotonic(cls):
            return cls.value

    action_times: list[float] = []
    sleeps: list[float] = []

    def fake_action(_action_cls, _driver, _context, _params):
        action_times.append(FakeClock.value)
        return {"status": "passed"}

    async def fake_sleep(duration):
        sleeps.append(duration)
        FakeClock.value += duration

    async def fake_verify_with_wait(*_args, **_kwargs):
        # 模拟断言与上报占用少于 500ms 的时间。
        FakeClock.value += 0.1
        return {"status": "passed", "expected": "ok", "actual": "ok"}

    class PacingAction:
        async def execute(self, _driver, _context, _params):
            return {"status": "passed"}

    monkeypatch.setitem(ACTION_REGISTRY, "_pacing_test_v3", PacingAction)
    monkeypatch.setattr(runner_module, "_run_action_in_thread", fake_action)
    monkeypatch.setattr(runner_module, "verify_with_wait", fake_verify_with_wait)
    monkeypatch.setattr(runner_module, "time", SimpleNamespace(monotonic=FakeClock.monotonic))
    monkeypatch.setattr(runner_module.asyncio, "sleep", fake_sleep)

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    case = {
        "execution_case_id": 2001,
        "case_id": 1,
        "case_name": "V3 节奏用例",
        "flow_snapshot": [
            {
                "execution_node_id": 3001, "kind": "action", "phase": "case_main",
                "order": 1, "action": "_pacing_test_v3", "params": {},
            },
            {
                "execution_node_id": 3002, "kind": "assertion", "phase": "case_main",
                "order": 2, "type": "text_equals", "params": {"expected": "ok"},
            },
            {
                "execution_node_id": 3003, "kind": "action", "phase": "case_main",
                "order": 3, "action": "_pacing_test_v3", "params": {},
            },
        ],
        "elements_snapshot": {},
    }
    status = await TestRunner(MockDriver(), send, 100).run_case(case)

    assert status == "passed"
    assert action_times == [200.0, 200.5]
    assert sum(sleeps) == pytest.approx(0.4)


async def test_runner_v3_continues_after_failed_assertion(monkeypatch):
    monkeypatch.setattr(TestRunner, "ACTION_MIN_INTERVAL_SECONDS", 0)
    driver = MockDriver()
    case = {
        "execution_case_id": 2001,
        "flow_snapshot": [
            {
                "execution_node_id": 3001, "kind": "assertion", "phase": "case_main", "order": 1,
                "type": "text_equals", "element_id": 1, "params": {"expected": "wrong"},
                "max_wait_seconds": 0,
            },
            {
                "execution_node_id": 3002, "kind": "action", "phase": "case_main", "order": 2,
                "action": "input", "element_id": 2, "params": {"value": "after"},
            },
        ],
        "elements_snapshot": {
            "1": {"locator_type": "id", "locator_value": "username"},
            "2": {"locator_type": "id", "locator_value": "target"},
        },
    }

    sent: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    assert await TestRunner(driver, send, 100).run_case(case) == "failed"
    assert driver.state["target"] == "after"
    node_results = [message for message in sent if message["type"] == "node_result"]
    assert [message["status"] for message in node_results] == ["failed", "passed"]


async def test_runner_v3_case_setup_failed_assertion_still_runs_case_main(monkeypatch):
    monkeypatch.setattr(TestRunner, "ACTION_MIN_INTERVAL_SECONDS", 0)
    driver = MockDriver()
    case = {
        "execution_case_id": 2001,
        "flow_snapshot": [
            {
                "execution_node_id": 3001, "kind": "assertion", "phase": "case_setup", "order": 1,
                "type": "text_equals", "element_id": 1, "params": {"expected": "wrong"},
                "max_wait_seconds": 0,
            },
            {
                "execution_node_id": 3002, "kind": "action", "phase": "case_main", "order": 1,
                "action": "input", "element_id": 2, "params": {"value": "main"},
            },
        ],
        "elements_snapshot": {
            "1": {"locator_type": "id", "locator_value": "username"},
            "2": {"locator_type": "id", "locator_value": "target"},
        },
    }

    sent: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    assert await TestRunner(driver, send, 100).run_case(case) == "failed"
    assert driver.state["target"] == "main"
    assert any(
        message["execution_node_id"] == 3002
        and message["status"] == "passed"
        for message in sent
        if message["type"] == "node_result"
    )


async def test_runner_v3_assertion_error_still_stops_following_nodes(monkeypatch):
    monkeypatch.setattr(TestRunner, "ACTION_MIN_INTERVAL_SECONDS", 0)
    driver = MockDriver()
    case = {
        "execution_case_id": 2001,
        "flow_snapshot": [
            {
                "execution_node_id": 3001, "kind": "assertion", "phase": "case_main", "order": 1,
                "type": "unknown_assertion", "params": {},
            },
            {
                "execution_node_id": 3002, "kind": "action", "phase": "case_main", "order": 2,
                "action": "input", "element_id": 1, "params": {"value": "must-not-run"},
            },
        ],
        "elements_snapshot": {"1": {"locator_type": "id", "locator_value": "target"}},
    }

    sent: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    assert await TestRunner(driver, send, 100).run_case(case) == "error"
    assert "target" not in driver.state
    assert [
        message["execution_node_id"]
        for message in sent
        if message["type"] == "node_result"
    ] == [3001]


async def test_runner_resolves_get_text_variable_in_following_nodes():
    """获取文本产生的变量应在后续动作和断言执行前解析。"""
    driver = MockDriver(initial_state={"username": "captured-value"})
    case = {
        "execution_case_id": 2001,
        "case_id": 1,
        "case_name": "运行时变量用例",
        "flow_snapshot": [
            {
                "execution_node_id": 3001, "kind": "action", "phase": "case_main", "order": 1,
                "action": "get_text", "element_id": 1,
                "params": {"variable_name": "captured_text"},
            },
            {
                "execution_node_id": 3002, "kind": "action", "phase": "case_main", "order": 2,
                "action": "input", "element_id": 2,
                "params": {"value": "${captured_text}"},
            },
            {
                "execution_node_id": 3003, "kind": "assertion", "phase": "case_main", "order": 3,
                "type": "text_equals", "element_id": 2,
                "params": {"expected": "${captured_text}"},
            },
        ],
        "elements_snapshot": {
            "1": {"locator_type": "id", "locator_value": "username"},
            "2": {"locator_type": "id", "locator_value": "target"},
        },
    }

    status, sent = await _run_and_capture(case, driver=driver)

    assert status == "passed"
    assert driver.state["target"] == "captured-value"
    assertion = next(message for message in sent if message["type"] == "node_result" and message["kind"] == "assertion")
    assert assertion["actual_value"] == "captured-value"


async def test_runner_executes_case_main_phase_steps():
    """协议 V2：后端快照使用 case_setup/case_main/case_teardown，Agent 必须归一化分桶并执行。"""
    case = _make_case(
        steps=[
            {"order": 1, "phase": "case_setup", "action": "input", "element_id": 1, "params": {"value": "pre"}},
            {"order": 2, "phase": "case_main", "action": "click", "element_id": 2, "params": {}},
            {"order": 3, "phase": "case_teardown", "action": "input", "element_id": 1, "params": {"value": "post"}},
        ],
        assertions=[
            {"order": 1, "type": "text_equals", "element_id": 1, "params": {"expected": "pre"}},
        ],
    )
    status, sent = await _run_and_capture(case)
    assert status == "passed"
    step_types = [m["type"] for m in sent]
    # 前置 + 主体 + 后置各一条 step_result，而非"仅送达断言后直接返回"
    assert step_types.count("step_result") == 3
    assert step_types.count("log") == 3
    assert [m["step_order"] for m in sent if m["type"] == "step_result"] == [1, 2, 3]
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
    assert "断言数据不一致" in (assertion_msg["assertions"][0]["error_message"] or "")
    step_result = next(m for m in sent if m["type"] == "step_result")
    assert "断言数据不一致" in (step_result["error_message"] or "")


async def test_runner_v2_assertion_element_not_found_is_classified_and_not_exists_passes():
    missing = _make_case(
        steps=[{"order": 1, "action": "input", "element_id": 1, "params": {"value": "admin"}}],
        assertions=[
            {"order": 1, "type": "text_equals", "element_id": 99, "params": {"expected": "admin"}},
        ],
    )
    status, sent = await _run_and_capture(missing)
    assert status == "failed"
    item = next(m for m in sent if m["type"] == "assertion_result")["assertions"][0]
    assert item["status"] == "failed"
    assert item["error_message"].startswith("断言元素未找到：")
    step_result = next(m for m in sent if m["type"] == "step_result")
    assert step_result["error_message"].startswith("断言元素未找到：")

    allowed_missing = _make_case(
        steps=[{"order": 1, "action": "input", "element_id": 1, "params": {"value": "admin"}}],
        assertions=[
            {"order": 1, "type": "element_exists", "element_id": 99, "params": {"expected": "not_exists"}},
        ],
    )
    passed, passed_sent = await _run_and_capture(allowed_missing)
    assert passed == "passed"
    passed_item = next(m for m in passed_sent if m["type"] == "assertion_result")["assertions"][0]
    assert passed_item["status"] == "passed"
    assert passed_item["error_message"] is None


def test_assertion_failure_message_preserves_falsy_expected_and_actual_values():
    empty_expected = _assertion_failure_message(
        {"status": "failed", "expected": "", "actual": False}
    )
    assert "期望=''" in empty_expected
    assert "实际=False" in empty_expected

    zero_expected = _assertion_failure_message(
        {"status": "failed", "expected": 0, "actual": ""}
    )
    assert "期望=0" in zero_expected
    assert "实际=''" in zero_expected


async def test_runner_unknown_action_fails():
    case = _make_case(
        steps=[{"order": 1, "action": "no_such_action", "element_id": 1, "params": {}}],
    )
    status, sent = await _run_and_capture(case)
    assert status == "failed"
    assert sent[0]["status"] == "failed"
    assert "未知动作" in (sent[0]["error_message"] or "")
    error_log = next(m for m in sent if m["type"] == "log")
    assert error_log["level"] == "ERROR"
    assert "未知动作" in error_log["message"]


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


async def test_runner_runs_teardown_after_main_failure():
    driver = MockDriver()
    case = _make_case(
        steps=[
            {"order": 1, "phase": "setup", "action": "input", "element_id": 1, "params": {"value": "ready"}},
            {"order": 2, "phase": "main", "action": "no_such_action", "params": {}},
            {"order": 3, "phase": "teardown", "action": "input", "element_id": 1, "params": {"value": "cleaned"}},
        ],
    )

    status, sent = await _run_and_capture(case, driver=driver)

    assert status == "failed"
    assert driver.state["username"] == "cleaned"
    step_msgs = [message for message in sent if message["type"] == "step_result"]
    assert [message["action"] for message in step_msgs] == ["input", "no_such_action", "input"]
    assert any("后置步骤 3" in message["message"] for message in sent if message["type"] == "log")


async def test_runner_setup_failure_skips_main_but_still_runs_teardown():
    driver = MockDriver()
    case = _make_case(
        steps=[
            {"order": 1, "phase": "setup", "action": "no_such_action", "params": {}},
            {"order": 2, "phase": "main", "action": "input", "element_id": 1, "params": {"value": "main"}},
            {"order": 3, "phase": "teardown", "action": "input", "element_id": 1, "params": {"value": "cleaned"}},
        ],
    )

    status, sent = await _run_and_capture(case, driver=driver)

    assert status == "failed"
    assert driver.state["username"] == "cleaned"
    step_msgs = [message for message in sent if message["type"] == "step_result"]
    assert [message["step_order"] for message in step_msgs] == [1, 3]


async def test_runner_runs_assertions_before_teardown_and_keeps_assertion_failure():
    """回归：执行顺序固定为主体 → 断言 → 后置，后置成功不得覆盖断言失败。"""
    driver = MockDriver()
    case = _make_case(
        steps=[
            {
                "order": 1,
                "phase": "main",
                "action": "input",
                "element_id": 1,
                "params": {"value": "admin"},
            },
            {
                "order": 2,
                "phase": "teardown",
                "action": "input",
                "element_id": 1,
                "params": {"value": "cleaned"},
            },
        ],
        assertions=[
            {
                "order": 1,
                "type": "text_equals",
                "element_id": 1,
                "params": {"expected": "wrong"},
            },
        ],
    )

    status, sent = await _run_and_capture(case, driver=driver)

    result_messages = [
        message for message in sent if message["type"] in {"step_result", "assertion_result"}
    ]
    assert [message["type"] for message in result_messages] == [
        "step_result",
        "assertion_result",
        "step_result",
    ]
    assert result_messages[1]["assertions"][0]["status"] == "failed"
    assert driver.state["username"] == "cleaned"
    assert status == "failed"


async def test_runner_v2_continues_after_failed_step_assertion(monkeypatch):
    monkeypatch.setattr(TestRunner, "ACTION_MIN_INTERVAL_SECONDS", 0)
    driver = MockDriver()
    case = _make_case(
        steps=[
            {"order": 1, "action": "input", "element_id": 1, "params": {"value": "admin"}},
        ],
        assertions=[
            {"order": 1, "type": "text_equals", "element_id": 1, "params": {"expected": "wrong"}},
        ],
    )
    case["steps_snapshot"].append({
        "execution_step_id": 3002,
        "order": 2,
        "action": "input",
        "element_id": 2,
        "params": {"value": "after"},
    })

    status, sent = await _run_and_capture(case, driver=driver)

    assert status == "failed"
    assert driver.state["login_btn"] == "after"
    assert [message["step_order"] for message in sent if message["type"] == "step_result"] == [1, 2]


async def test_runner_assertion_uses_snapshot_order_and_injected_id():
    """协议 V2：断言按快照自带 order（非从 1 重编号）上报，并透传注入的 execution_assertion_id。

    回归：后端预建断言 assertion_order 从 len(steps) 起（≥步骤数），Agent 却从 1 重编号，
    导致 _upsert_assertion 匹配不到预建行而插入新断言（重复）、原行被 skipped（统计错误）。
    """
    driver = MockDriver()
    case = _make_case(
        steps=[{"order": 1, "phase": "case_main", "action": "input", "element_id": 1, "params": {"value": "admin"}}],
        assertions=[
            {
                "order": 2,
                "execution_assertion_id": 501,
                "type": "text_equals",
                "element_id": 1,
                "params": {"expected": "admin"},
            },
        ],
    )

    status, sent = await _run_and_capture(case, driver=driver)

    assert status == "passed"
    assertion_msg = next(m for m in sent if m["type"] == "assertion_result")
    item = assertion_msg["assertions"][0]
    # 断言 order 应沿用快照值 2（而非遍历序号 1），execution_assertion_id 透传
    assert item["assertion_order"] == 2
    assert item["execution_assertion_id"] == 501


async def test_runner_current_screen_mode_skips_launch_app_action():
    driver = MockDriver()
    driver.attach_to_current_app()
    case = _make_case(
        steps=[
            {"order": 1, "action": "launch_app", "params": {"package": "com.demo"}},
            {"order": 2, "action": "input", "element_id": 1, "params": {"value": "direct"}},
        ],
    )

    status, sent = await _run_and_capture(
        case,
        parameters={"attach_to_current_app": True},
        driver=driver,
    )

    assert status == "passed"
    assert driver.state["username"] == "direct"
    launch_result = next(
        message
        for message in sent
        if message["type"] == "step_result" and message["action"] == "launch_app"
    )
    assert "跳过启动 APP" in launch_result["actual_value"]


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


async def test_click_logs_element_rect_and_expected_calculated_center(caplog):
    from executor.actions import ClickAction

    caplog.set_level(logging.INFO, logger="agent.actions")
    driver = MockDriver()
    context = ExecutionContext(driver, _make_case([]))

    result = await ClickAction().execute(driver, context, {"element_id": 2})

    assert result["status"] == "passed"
    messages = [record.getMessage() for record in caplog.records]
    before = next(message for message in messages if '"click_phase": "before"' in message)
    assert '"click_method": "element.click"' in before
    assert '"rect": {"height": 2000, "width": 1000, "x": 0, "y": 0}' in before
    assert '"expected_calculated_click_point": {"x": 500.0, "y": 1000.0}' in before
    assert '"actual_touch_point": "unknown: element.click does not expose the Appium touch coordinates"' in before


async def test_click_diagnostic_failure_does_not_hide_click_error():
    from executor.actions import ClickAction

    class FailingClickDriver(MockDriver):
        def click(self, _element) -> None:
            raise RuntimeError("click transport failure")

    driver = FailingClickDriver()
    context = ExecutionContext(driver, _make_case([]))

    with pytest.raises(RuntimeError, match="click transport failure"):
        await ClickAction().execute(driver, context, {"element_id": 2})


async def test_click_attribute_diagnostic_failure_does_not_block_click():
    from executor.actions import ClickAction

    class BrokenAttributeElement:
        id = "remote-1"

        def get_attribute(self, _name):
            raise RuntimeError("attribute unavailable")

    class BrokenAttributeDriver(MockDriver):
        def find_element(self, locator_type, locator_value, wait_timeout=10):
            _ = locator_type, locator_value, wait_timeout
            return BrokenAttributeElement()

        def click(self, _element) -> None:
            return None

    driver = BrokenAttributeDriver()
    context = ExecutionContext(driver, _make_case([]))

    result = await ClickAction().execute(driver, context, {"element_id": 2})

    assert result["status"] == "passed"


class StaleClickDriver(RecordingDriver):
    """模拟 UI 重绘：旧元素 click stale，重新定位后的新元素可点击。"""

    def __init__(self, stale_failures: int, error: Exception) -> None:
        super().__init__()
        self.stale_failures = stale_failures
        self.error = error
        self.find_count = 0
        self.click_count = 0
        self.clicked_elements = []

    def find_element(self, locator_type: str, locator_value: str, wait_timeout: int | None = None):
        self.wait_timeouts.append(wait_timeout)
        self.find_count += 1
        return {"generation": self.find_count, "locator": locator_value}

    def click(self, element) -> None:
        self.click_count += 1
        self.clicked_elements.append(element)
        if self.click_count <= self.stale_failures:
            raise self.error


async def test_click_refinds_element_after_uiautomator_stale_object(monkeypatch):
    from selenium.common.exceptions import WebDriverException

    from executor.actions import ClickAction

    driver = StaleClickDriver(
        stale_failures=1,
        error=WebDriverException(
            "androidx.test.uiautomator.StaleObjectException; "
            "io.appium.uiautomator2.common.exceptions.StaleElementReferenceException"
        ),
    )
    context = ExecutionContext(driver, _make_case([]))
    monkeypatch.setattr(ClickAction, "_STALE_RETRY_DELAYS", (0, 0))

    result = await ClickAction().execute(
        driver,
        context,
        {"element_id": 2, "wait_timeout": 3},
    )

    assert result["status"] == "passed"
    assert driver.find_count == 2
    assert driver.click_count == 2
    assert driver.clicked_elements == [
        {"generation": 1, "locator": "login_btn"},
        {"generation": 2, "locator": "login_btn"},
    ]
    assert driver.wait_timeouts == [3, 3]


async def test_click_does_not_retry_non_stale_error(monkeypatch):
    from executor.actions import ClickAction

    driver = StaleClickDriver(stale_failures=1, error=RuntimeError("设备连接已断开"))
    context = ExecutionContext(driver, _make_case([]))
    monkeypatch.setattr(ClickAction, "_STALE_RETRY_DELAYS", (0, 0))

    with pytest.raises(RuntimeError, match="设备连接已断开"):
        await ClickAction().execute(driver, context, {"element_id": 2})

    assert driver.find_count == 1
    assert driver.click_count == 1


async def test_click_reports_clear_error_after_stale_retries_exhausted(monkeypatch):
    from selenium.common.exceptions import StaleElementReferenceException

    from executor.actions import ClickAction
    from executor.driver import DriverError

    driver = StaleClickDriver(
        stale_failures=3,
        error=StaleElementReferenceException("stale element reference"),
    )
    context = ExecutionContext(driver, _make_case([]))
    monkeypatch.setattr(ClickAction, "_STALE_RETRY_DELAYS", (0, 0))

    with pytest.raises(DriverError, match="重新定位并重试 2 次"):
        await ClickAction().execute(driver, context, {"element_id": 2})

    assert driver.find_count == 3
    assert driver.click_count == 3


async def test_click_stale_retry_honors_stop_request(monkeypatch):
    from selenium.common.exceptions import StaleElementReferenceException

    from executor.actions import ClickAction

    stop_checks = 0

    def should_stop() -> bool:
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks >= 2

    driver = StaleClickDriver(
        stale_failures=1,
        error=StaleElementReferenceException("stale element reference"),
    )
    context = ExecutionContext(driver, _make_case([]), should_stop=should_stop)
    monkeypatch.setattr(ClickAction, "_STALE_RETRY_DELAYS", (0, 0))

    with pytest.raises(StopRequested):
        await ClickAction().execute(driver, context, {"element_id": 2})

    assert driver.find_count == 1
    assert driver.click_count == 1


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


async def test_appium_http_timeout_has_safe_minimum_and_restores_default():
    from types import SimpleNamespace

    from executor.appium_driver import AppiumDriver

    client_config = SimpleNamespace(timeout=30.0)
    session = SimpleNamespace(
        command_executor=SimpleNamespace(_client_config=client_config),
    )
    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = session
    driver._remember_http_timeout_default()

    assert driver._effective_http_timeout(0.05) == AppiumDriver.HTTP_SHORT_TIMEOUT_MIN
    assert driver._effective_http_timeout(15) == 15.0
    assert driver._effective_http_timeout(300) == 30.0

    driver.set_command_timeout(0.05)
    assert client_config.timeout == AppiumDriver.HTTP_SHORT_TIMEOUT_MIN

    driver.set_command_timeout(None)
    assert client_config.timeout == 30.0


async def test_appium_http_timeout_can_be_configured_but_is_clamped():
    from executor.appium_driver import AppiumDriver

    assert AppiumDriver(http_request_timeout=10).http_request_timeout == 10.0
    assert AppiumDriver(http_request_timeout=1).http_request_timeout == 5.0
    assert AppiumDriver(http_request_timeout=300).http_request_timeout == 30.0


async def test_appium_immediate_find_uses_http_timeout_and_restores_default():
    from types import SimpleNamespace

    from executor.appium_driver import AppiumDriver

    client_config = SimpleNamespace(timeout=12.0)

    class ImmediateSession:
        command_executor = SimpleNamespace(_client_config=client_config)

        def find_element(self, by, value):
            assert client_config.timeout == AppiumDriver.HTTP_IMMEDIATE_TIMEOUT
            return f"element:{value}"

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = ImmediateSession()
    driver._remember_http_timeout_default()

    assert driver.find_element("id", "now", wait_timeout=0) == "element:now"
    assert client_config.timeout == 12.0


async def test_appium_http_timeout_restores_after_request_error():
    from types import SimpleNamespace

    from executor.appium_driver import AppiumDriver

    client_config = SimpleNamespace(timeout=12.0)
    session = SimpleNamespace(
        command_executor=SimpleNamespace(_client_config=client_config),
    )
    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = session

    def fail_request():
        raise RuntimeError("request failed")

    with pytest.raises(RuntimeError, match="request failed"):
        driver.run_with_http_timeout(15, fail_request)
    assert client_config.timeout == 12.0


async def test_appium_http_timeout_is_serialized_across_threads():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace

    from executor.appium_driver import AppiumDriver

    client_config = SimpleNamespace(timeout=12.0)
    session = SimpleNamespace(
        command_executor=SimpleNamespace(_client_config=client_config),
    )
    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = session
    first_started = Event()
    release_first = Event()
    second_entered = Event()
    observed: list[float] = []

    def first_request():
        first_started.set()
        assert client_config.timeout == 15.0
        assert not second_entered.wait(0.1)
        assert release_first.wait(2)
        return "first"

    def second_request():
        second_entered.set()
        observed.append(client_config.timeout)
        return "second"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(driver.run_with_http_timeout, 15, first_request)
        assert first_started.wait(2)
        second_future = pool.submit(driver.run_with_http_timeout, 20, second_request)
        assert not second_entered.wait(0.1)
        release_first.set()
        assert first_future.result() == "first"
        assert second_future.result() == "second"

    assert observed == [20.0]
    assert client_config.timeout == 12.0


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


@pytest.mark.parametrize(
    ("locator_value", "expected_xpath"),
    [
        (
            "src-views-login-btn-setServerIp",
            '//*[@resource-id="src-views-login-btn-setServerIp"]',
        ),
        (
            "src-components-l-popup-input-ip//android.widget.EditText",
            '//*[@resource-id="src-components-l-popup-input-ip"]//android.widget.EditText',
        ),
    ],
)
async def test_appium_resource_id_is_converted_to_exact_xpath(locator_value, expected_xpath):
    from appium.webdriver.common.appiumby import AppiumBy

    from executor.appium_driver import AppiumDriver

    class ResourceIdSession:
        def find_element(self, by, value):
            assert (by, value) == (AppiumBy.XPATH, expected_xpath)
            return "resource-element"

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = ResourceIdSession()

    assert driver.find_element("resource_id", locator_value, wait_timeout=1) == "resource-element"


async def test_appium_input_uses_selected_editable_element_directly():
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
    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = object()

    driver.input(editable, "9337")

    assert editable.cleared is True
    assert editable.value == "9337"


class _InputStateElement:
    """无 sleep 的 Appium input 状态机测试替身。"""

    id = "input-1"

    def __init__(self, initial: str = "15", mode: str = "normal") -> None:
        self.value = initial
        self.initial = initial
        self.mode = mode
        self.clear_calls = 0
        self.send_calls = 0
        self.pending_clear_reads = 0
        self.late_refill_reads = 0
        self.target_value = ""

    def clear(self):
        self.clear_calls += 1
        if self.mode == "delayed" and self.clear_calls == 1:
            self.pending_clear_reads = 2
            return
        self.value = ""

    def send_keys(self, value):
        self.send_calls += 1
        self.target_value = value
        if self.mode == "refill" and self.send_calls == 1:
            self.value = self.initial + value
        elif self.mode == "late_refill" and self.send_calls == 1:
            self.value = value
            self.late_refill_reads = 3
        elif self.mode == "mismatch" and self.send_calls == 1:
            self.value = "wrong"
        elif self.mode == "persistent_mismatch":
            self.value = "wrong"
        else:
            self.value += value

    def get_attribute(self, name):
        if name == "value":
            if self.pending_clear_reads:
                self.pending_clear_reads -= 1
                if self.pending_clear_reads == 0:
                    self.value = ""
            if self.late_refill_reads:
                self.late_refill_reads -= 1
                if self.late_refill_reads == 0:
                    self.value = self.initial + self.target_value
            return self.value
        if name in {"password", "inputType"}:
            return "true" if self.mode == "password" and name == "password" else ""
        return ""

    @property
    def text(self):
        return self.value


class _UnreadableInputStateElement(_InputStateElement):
    def get_attribute(self, _name):
        raise RuntimeError("attribute unavailable")

    @property
    def text(self):
        raise RuntimeError("text unavailable")


def _appium_input_driver():
    from executor.appium_driver import AppiumDriver

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = object()
    return driver


@pytest.fixture
def fast_appium_input(monkeypatch):
    from executor.appium_driver import AppiumDriver

    monkeypatch.setattr(AppiumDriver, "INPUT_CLEAR_SETTLE_TIMEOUT", 0.02)
    monkeypatch.setattr(AppiumDriver, "INPUT_CLEAR_POLL_INTERVAL", 0)
    monkeypatch.setattr(AppiumDriver, "INPUT_VERIFY_SETTLE_TIMEOUT", 0.02)
    monkeypatch.setattr(AppiumDriver, "INPUT_VERIFY_POLL_INTERVAL", 0)
    return _appium_input_driver()


async def test_appium_input_clear_confirms_and_sends_once(fast_appium_input, caplog):
    element = _InputStateElement()
    sensitive_value = "secret-input-42"

    with caplog.at_level(logging.INFO, logger="agent.appium"):
        fast_appium_input.input(element, sensitive_value)

    assert (element.clear_calls, element.send_calls, element.value) == (
        1,
        1,
        sensitive_value,
    )
    assert all(sensitive_value not in record.getMessage() for record in caplog.records)
    assert all(sensitive_value not in repr(record.args) for record in caplog.records)


async def test_appium_input_replaces_value_after_async_old_value_refill(fast_appium_input):
    element = _InputStateElement(mode="refill")

    fast_appium_input.input(element, "15")

    assert element.value == "15"
    assert (element.clear_calls, element.send_calls) == (2, 2)


async def test_appium_input_catches_late_refill_after_initial_matching_reads(
    fast_appium_input,
):
    element = _InputStateElement(mode="late_refill")

    fast_appium_input.input(element, "15")

    assert element.value == "15"
    assert (element.clear_calls, element.send_calls) == (2, 2)


async def test_appium_input_waits_for_delayed_clear_without_extra_replace(fast_appium_input):
    element = _InputStateElement(mode="delayed")

    fast_appium_input.input(element, "15")

    assert element.value == "15"
    assert (element.clear_calls, element.send_calls) == (1, 1)


async def test_appium_input_replaces_once_after_first_final_value_mismatch(fast_appium_input):
    element = _InputStateElement(mode="mismatch")

    fast_appium_input.input(element, "15")

    assert element.value == "15"
    assert (element.clear_calls, element.send_calls) == (2, 2)


async def test_appium_input_raises_after_persistent_final_value_mismatch(fast_appium_input):
    from executor.driver import DriverError

    element = _InputStateElement(mode="persistent_mismatch")

    with pytest.raises(DriverError, match="清空并替换后控件值仍与目标不一致"):
        fast_appium_input.input(element, "15")

    assert (element.clear_calls, element.send_calls) == (2, 2)


async def test_appium_input_clear_false_preserves_append_semantics(fast_appium_input):
    element = _InputStateElement()

    fast_appium_input.input(element, "20", clear_first=False)

    assert element.value == "1520"
    assert (element.clear_calls, element.send_calls) == (0, 1)


async def test_appium_input_skips_unsafe_value_verification(fast_appium_input):
    unreadable = _UnreadableInputStateElement()
    fast_appium_input.input(unreadable, "15")

    password = _InputStateElement(initial="secret", mode="password")
    fast_appium_input.input(password, "15")
    assert password.send_calls == 1

    multiline = _InputStateElement()
    fast_appium_input.input(multiline, "line\nvalue")
    assert multiline.value == "line\nvalue"


async def test_appium_input_reports_actionable_error_for_non_editable_element():
    from selenium.common.exceptions import InvalidElementStateException

    from executor.appium_driver import AppiumDriver
    from executor.driver import DriverError

    class DisabledElement:
        def clear(self):
            raise InvalidElementStateException("not editable")

        def send_keys(self, value):
            raise AssertionError(f"不应继续输入: {value}")

    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = object()

    with pytest.raises(DriverError, match="定位到的控件不可编辑"):
        driver.input(DisabledElement(), "value")


async def test_appium_clear_uses_selected_element_directly():
    from executor.appium_driver import AppiumDriver

    class Editable:
        cleared = False

        def clear(self):
            self.cleared = True

    editable = Editable()
    driver = AppiumDriver(device={"udid": "u-1", "platform": "android"})
    driver.driver = object()

    driver.clear(editable)

    assert editable.cleared is True


async def test_regex_match_assertion():
    from executor.assertions import RegexMatchAssertion

    driver = MockDriver(initial_state={"greeting": "Hello 2026"})
    context = ExecutionContext(driver, _make_case([]))
    context.elements_snapshot = {"1": {"locator_type": "id", "locator_value": "greeting"}}
    result = await RegexMatchAssertion().verify(driver, context, {"element_id": 1, "pattern": r"\d{4}"})
    assert result["status"] == "passed"


@pytest.mark.parametrize(
    ("operator", "expected", "passed"),
    [
        (">", "97", True),
        (">", "98", False),
        (">=", "98", True),
        (">=", "99", False),
        ("<", "99", True),
        ("<", "98", False),
        ("<=", "98.0", True),
        ("<=", "97", False),
        ("==", "98", True),
        ("==", "98.1", False),
        ("!=", "97", True),
        ("!=", "98.0", False),
    ],
)
async def test_number_compare_assertion_uses_decimal(operator, expected, passed):
    from executor.assertions import NumberCompareAssertion

    driver = MockDriver(initial_state={"value": " 98.0 "})
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "id", "locator_value": "value"}}),
    )
    result = await NumberCompareAssertion().verify(
        driver, context, {"element_id": 1, "operator": operator, "expected": expected}
    )
    assert result["status"] == ("passed" if passed else "failed")
    assert result["expected"] == f"{operator} {expected}"
    assert result["actual"] == " 98.0 "


@pytest.mark.parametrize(
    ("actual", "expected", "operator", "message"),
    [
        ("not-a-number", "1", ">", "实际文本无法解析为数字"),
        ("1", "not-a-number", ">", "目标值无法解析为数字"),
        ("1", "1", "~", "不支持的数字比较符"),
    ],
)
async def test_number_compare_rejects_invalid_inputs(actual, expected, operator, message):
    from executor.assertions import NumberCompareAssertion

    driver = MockDriver(initial_state={"value": actual})
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "id", "locator_value": "value"}}),
    )
    with pytest.raises(ValueError, match=message):
        await NumberCompareAssertion().verify(
            driver, context, {"element_id": 1, "operator": operator, "expected": expected}
        )


async def test_number_compare_supports_variable_and_scientific_notation():
    from executor.assertions import NumberCompareAssertion

    driver = MockDriver(initial_state={"value": "-1.25e2"})
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "id", "locator_value": "value"}}),
        variables={"threshold": "-125"},
    )
    result = await NumberCompareAssertion().verify(
        driver,
        context,
        {
            "element_id": 1,
            "operator": "==",
            "expected": context.render("${threshold}"),
        },
    )
    assert result["status"] == "passed"


async def test_runner_reports_number_compare_mismatch_with_rendered_variable():
    sent: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    runner = TestRunner(
        MockDriver(initial_state={"value": "98.0"}),
        send,
        execution_id=100,
        parameters={"variables": {"threshold": "99"}},
    )
    case = {
        "execution_case_id": 2001,
        "flow_snapshot": [{
            "execution_node_id": 3001, "kind": "assertion", "phase": "case_main", "order": 1,
            "type": "number_compare", "element_id": 1,
            "params": {"operator": "==", "expected": "${threshold}"},
            "max_wait_seconds": 0,
        }],
        "elements_snapshot": {"1": {"locator_type": "id", "locator_value": "value"}},
    }

    assert await runner.run_case(case) == "failed"
    node_result = next(item for item in sent if item["type"] == "node_result")
    assert node_result["status"] == "failed"
    assert node_result["expected_value"] == "== 99"
    assert node_result["actual_value"] == "98.0"
    assert "断言数据不一致" in node_result["error_message"]


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


async def test_runner_uploads_screenshot_and_reports_server_key(tmp_path):
    """CR-07：截图后立即上传，step_result 只携带服务端对象键。"""
    uploader = FakeUploader("execution_100/screenshots/abc123.png")
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    runner = TestRunner(
        MockDriver(), fake_send, 100, screenshots_dir=tmp_path,
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


async def test_runner_upload_failure_keeps_local_path_out(tmp_path):
    """CR-07：上传失败不得把本地路径回传服务端，须记录明确错误。"""
    uploader = FakeUploader(None)
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    runner = TestRunner(
        MockDriver(), fake_send, 100, screenshots_dir=tmp_path,
        session_token="sess", uploader=uploader,
    )
    status = await runner.run_case(_screenshot_case())
    assert status == "passed"
    step_msg = sent[0]
    assert step_msg["screenshot_path"] is None
    assert "上传失败" in (step_msg["error_message"] or "")


async def test_runner_auto_screenshot_on_v2_action_failure(tmp_path):
    uploader = FakeUploader("execution_100/screenshots/failure.png")
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    case = _make_case([{"order": 1, "action": "missing_action", "params": {}}])
    status = await TestRunner(
        MockDriver(), fake_send, 100, screenshots_dir=tmp_path, uploader=uploader
    ).run_case(case)

    assert status == "failed"
    step_result = next(message for message in sent if message["type"] == "step_result")
    assert step_result["screenshot_path"] == "execution_100/screenshots/failure.png"
    assert "missing_action" in step_result["error_message"]
    assert len(uploader.calls) == 1
    assert str(tmp_path) in uploader.calls[0][1]
    assert uploader.calls[0][1] != step_result["screenshot_path"]


async def test_runner_auto_screenshot_on_v2_assertion_failure_once_per_step(tmp_path):
    uploader = FakeUploader("execution_100/screenshots/assertion.png")
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    case = _make_case(
        [{"order": 1, "action": "input", "element_id": 1, "params": {"value": "actual"}}],
        assertions=[
            {"order": 1, "type": "text_equals", "element_id": 1, "params": {"expected": "one"}},
            {"order": 2, "type": "text_equals", "element_id": 1, "params": {"expected": "two"}},
        ],
    )
    status = await TestRunner(
        MockDriver(), fake_send, 100, screenshots_dir=tmp_path, uploader=uploader
    ).run_case(case)

    assert status == "failed"
    step_result = next(message for message in sent if message["type"] == "step_result")
    assert step_result["screenshot_path"] == "execution_100/screenshots/assertion.png"
    assert len(uploader.calls) == 1


async def test_runner_auto_screenshot_on_v2_missing_assertion_element(tmp_path):
    uploader = FakeUploader("execution_100/screenshots/missing.png")
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    case = _make_case(
        [{"order": 1, "action": "input", "element_id": 1, "params": {"value": "actual"}}],
        assertions=[
            {"order": 1, "type": "text_equals", "element_id": 999, "params": {"expected": "one"}},
        ],
    )
    await TestRunner(
        MockDriver(), fake_send, 100, screenshots_dir=tmp_path, uploader=uploader
    ).run_case(case)

    step_result = next(message for message in sent if message["type"] == "step_result")
    assert step_result["screenshot_path"] == "execution_100/screenshots/missing.png"
    assert "断言元素未找到" in step_result["error_message"]
    assert len(uploader.calls) == 1


@pytest.mark.parametrize(
    ("kind", "status", "node", "expected_error"),
    [
        ("action", "error", {"action": "missing_action"}, "未知动作"),
        ("assertion", "failed", {"type": "text_equals", "element_id": 1, "params": {"expected": "wrong"}}, "断言数据不一致"),
        ("assertion", "error", {"type": "missing_assertion", "params": {}}, "未知断言"),
    ],
)
async def test_runner_auto_screenshot_on_v3_failed_or_error_node(
    tmp_path, kind, status, node, expected_error
):
    uploader = FakeUploader(f"execution_100/screenshots/{kind}-{status}.png")
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    flow_node = {
        "execution_node_id": 3001,
        "kind": kind,
        "phase": "case_main",
        "order": 1,
        **node,
    }
    case = {
        "execution_case_id": 2001,
        "flow_snapshot": [flow_node],
        "elements_snapshot": {"1": {"locator_type": "id", "locator_value": "value"}},
    }
    if kind == "assertion" and node.get("type") == "text_equals":
        driver = MockDriver(initial_state={"value": "actual"})
    else:
        driver = MockDriver()
    result = await TestRunner(
        driver, fake_send, 100, screenshots_dir=tmp_path, uploader=uploader
    ).run_case(case)

    assert result == status
    node_result = next(message for message in sent if message["type"] == "node_result")
    assert node_result["status"] == status
    assert node_result["screenshot_path"] == f"execution_100/screenshots/{kind}-{status}.png"
    assert expected_error in (node_result["error_message"] or "")
    assert len(uploader.calls) == 1


async def test_runner_auto_screenshot_failure_preserves_original_error(tmp_path):
    uploader = FakeUploader(None)
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    case = _make_case([{"order": 1, "action": "missing_action", "params": {}}])
    await TestRunner(
        MockDriver(), fake_send, 100, screenshots_dir=tmp_path, uploader=uploader
    ).run_case(case)

    step_result = next(message for message in sent if message["type"] == "step_result")
    assert step_result["screenshot_path"] is None
    assert "未知动作" in step_result["error_message"]
    assert str(tmp_path) not in str(step_result)


async def test_runner_auto_screenshot_generation_failure_preserves_result(tmp_path):
    class FailingScreenshotDriver(MockDriver):
        def screenshot(self, path: str) -> None:
            raise OSError(path)

    uploader = FakeUploader("execution_100/screenshots/should-not-exist.png")
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    case = _make_case([{"order": 1, "action": "missing_action", "params": {}}])
    await TestRunner(
        FailingScreenshotDriver(), fake_send, 100, screenshots_dir=tmp_path, uploader=uploader
    ).run_case(case)

    step_result = next(message for message in sent if message["type"] == "step_result")
    assert step_result["screenshot_path"] is None
    assert "未知动作" in step_result["error_message"]
    assert uploader.calls == []


async def test_runner_does_not_auto_screenshot_when_stopped(tmp_path):
    uploader = FakeUploader("execution_100/screenshots/should-not-exist.png")
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    case = _make_case([{"order": 1, "action": "missing_action", "params": {}}])
    with pytest.raises(StopRequested):
        await TestRunner(
            MockDriver(), fake_send, 100, screenshots_dir=tmp_path, uploader=uploader,
            should_stop=lambda: True,
        ).run_case(case)
    assert uploader.calls == []


async def test_runner_does_not_auto_screenshot_when_cancelled(monkeypatch, tmp_path):
    class CancelAction:
        async def execute(self, _driver, _context, _params):
            raise asyncio.CancelledError()

    monkeypatch.setitem(ACTION_REGISTRY, "_cancel_for_screenshot_test", CancelAction)
    uploader = FakeUploader("execution_100/screenshots/should-not-exist.png")
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    case = _make_case([{"order": 1, "action": "_cancel_for_screenshot_test", "params": {}}])
    with pytest.raises(asyncio.CancelledError):
        await TestRunner(
            MockDriver(), fake_send, 100, screenshots_dir=tmp_path, uploader=uploader,
        ).run_case(case)
    assert uploader.calls == []


def test_save_screenshot_with_none_creates_png_in_nested_directory(tmp_path):
    screenshots_dir = tmp_path / "nested" / "screenshots"
    driver = MockDriver()
    context = ExecutionContext(driver, {}, screenshots_dir=screenshots_dir)

    path = context.save_screenshot(None)

    target = Path(path)
    assert target.parent == screenshots_dir
    assert target.suffix == ".png"
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert driver.screenshots == [path]


def test_save_screenshot_reports_when_driver_does_not_create_file(tmp_path):
    class NoopScreenshotDriver(MockDriver):
        def screenshot(self, path: str) -> None:
            self.screenshots.append(path)

    context = ExecutionContext(NoopScreenshotDriver(), {}, screenshots_dir=tmp_path)

    with pytest.raises(RuntimeError, match="截图失败：驱动未生成截图文件"):
        context.save_screenshot(None)


def test_save_screenshot_wraps_driver_file_error(tmp_path):
    class FailingScreenshotDriver(MockDriver):
        def screenshot(self, path: str) -> None:
            raise FileNotFoundError(path)

    context = ExecutionContext(FailingScreenshotDriver(), {}, screenshots_dir=tmp_path)

    with pytest.raises(RuntimeError, match="截图失败：无法生成截图文件") as error:
        context.save_screenshot(None)
    assert "Errno 2" not in str(error.value)


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
            "appium_http_request_timeout": 15,
        },
        device={"udid": "emulator-5554", "platform": "android"},
    )
    assert isinstance(driver, AppiumDriver)
    assert driver.command_executor == "http://10.0.0.8:4730"
    assert driver.command_timeout == 300
    assert driver.http_request_timeout == 15.0

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


async def test_appium_attach_current_screen_omits_app_capabilities(monkeypatch):
    import appium.webdriver as appium_webdriver

    calls: list[dict] = []

    class FakeDriver:
        session_id = "current-session"

    def fake_remote(*args, **kwargs):
        calls.append(kwargs)
        return FakeDriver()

    monkeypatch.setattr(appium_webdriver, "Remote", fake_remote)

    from executor.appium_driver import AppiumDriver

    driver = AppiumDriver(
        capabilities={
            "appPackage": "com.configured",
            "appActivity": ".MainActivity",
            "appium:options": {"appPackage": "com.nested", "appActivity": ".Nested"},
        },
        device={"udid": "emulator-5554", "platform": "android"},
    )
    driver.attach_to_current_app()

    options = calls[0]["options"]
    assert options.get_capability("udid") == "emulator-5554"
    assert options.get_capability("appPackage") is None
    assert options.get_capability("appActivity") is None
    assert options.get_capability("noReset") is True
    assert options.get_capability("dontStopAppOnReset") is True


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


def test_appium_swipe_percent_is_centered_and_default_is_unchanged():
    from executor.appium_driver import AppiumDriver

    class Session:
        def __init__(self):
            self.swipes = []

        def get_window_size(self):
            return {"width": 1080, "height": 2400}

        def swipe(self, *args):
            self.swipes.append(args)

    session = Session()
    driver = AppiumDriver(device={"platform": "android"})
    driver.driver = session
    driver.swipe("up", duration=300, percent=0.2)
    driver.swipe("down", duration=300, percent=0.2)
    driver.swipe("up", duration=300)

    assert session.swipes == [
        (540, 1440, 540, 960, 300),
        (540, 960, 540, 1440, 300),
        (540, 1920, 540, 480, 300),
    ]


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

    def swipe(
        self, direction: str, duration: int = 500, percent: float | None = None
    ) -> None:
        self.swipes.append((direction, duration))


async def test_swipe_to_find_found_after_swipes():
    from executor.actions import ACTION_REGISTRY

    action = ACTION_REGISTRY["swipe_to_find"]()
    driver = _FoundAfterSwipesDriver(after=3)
    context = ExecutionContext(driver, _make_case([]))
    result = await action.execute(
        driver, context, {
            "element_id": 1, "direction": "up", "max_swipes": 5,
            "duration": 500, "percent": 0.2, "settle_ms": 0,
        }
    )
    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 3
    assert driver.swipes == [("up", 500), ("up", 500), ("up", 500)]


async def test_swipe_to_find_logs_found_geometry_and_swipe_coordinates(caplog):
    from executor.actions import SwipeToFindAction

    caplog.set_level(logging.DEBUG, logger="agent.actions")
    action = SwipeToFindAction()
    driver = _FoundAfterSwipesDriver(after=1)
    context = ExecutionContext(driver, _make_case([]))

    result = await action.execute(
        driver, context, {
            "element_id": 1, "direction": "up", "max_swipes": 2,
            "duration": 300, "percent": 0.2, "settle_ms": 0,
        }
    )

    assert result["found_after_swipes"] == 1
    messages = [record.getMessage() for record in caplog.records]
    found = next(message for message in messages if '"event": "swipe_to_find_found"' in message)
    assert '"found_after_swipes": 1' in found
    assert '"rect": {"height": 2000, "width": 1000, "x": 0, "y": 0}' in found
    assert '"element_center_point": {"x": 500.0, "y": 1000.0}' in found
    assert "expected_calculated_click_point" not in found
    assert "actual_touch_point" not in found
    assert "click_method" not in found
    assert '"touch_point": "not applicable: swipe_to_find only locates and does not click"' in found
    swipe = next(message for message in messages if '"event": "swipe_to_find_swipe_before"' in message)
    assert '"start_y": 1200' in swipe
    assert '"end_y": 800' in swipe
    assert '"duration_ms": 300' in swipe


async def test_swipe_to_find_orders_swipe_settle_then_find(monkeypatch):
    from executor.actions import SwipeToFindAction

    class OrderedDriver(_FoundAfterSwipesDriver):
        def __init__(self):
            super().__init__(after=2)
            self.events: list[str] = []

        def find_element(self, locator_type, locator_value, wait_timeout=10):
            self.events.append("find")
            return super().find_element(locator_type, locator_value, wait_timeout)

        def swipe(self, direction, duration=500, percent=None):
            self.events.append("swipe")
            super().swipe(direction, duration, percent)

    driver = OrderedDriver()
    action = SwipeToFindAction()
    context = ExecutionContext(driver, _make_case([]))
    settle_calls: list[float] = []

    async def fake_settle(_context, seconds):
        settle_calls.append(seconds)
        driver.events.append("settle")

    monkeypatch.setattr(action, "_wait_for_settle", fake_settle)
    result = await action.execute(
        driver,
        context,
        {
            "element_id": 1,
            "max_swipes": 2,
            "percent": 0.2,
            "settle_ms": 120,
        },
    )

    assert result["status"] == "passed"
    assert driver.events == ["find", "swipe", "settle", "find", "swipe", "settle", "find"]
    assert settle_calls == [0.12, 0.12]


async def test_swipe_to_find_settle_honors_stop(monkeypatch):
    from executor.actions import SwipeToFindAction

    driver = _FoundAfterSwipesDriver(after=99)
    stopped = False

    def should_stop():
        return stopped

    action = SwipeToFindAction()
    context = ExecutionContext(driver, _make_case([]), should_stop=should_stop)
    original_settle = action._wait_for_settle

    async def stop_after_swipe(_context, _seconds):
        nonlocal stopped
        stopped = True
        await original_settle(_context, 0)

    monkeypatch.setattr(action, "_wait_for_settle", stop_after_swipe)
    # The helper checks should_stop before every sleep; a real settle must
    # therefore be interruptible immediately after the swipe.
    with pytest.raises(StopRequested):
        await action.execute(
            driver, context, {"element_id": 1, "max_swipes": 1, "settle_ms": 500}
        )


def test_swipe_to_find_params_validate_new_bounds():
    from executor.actions import SwipeToFindAction

    assert SwipeToFindAction._bounded_percent(0.05) == 0.05
    assert SwipeToFindAction._bounded_percent(0.95) == 0.95
    assert SwipeToFindAction._bounded_settle_ms(0) == 0
    assert SwipeToFindAction._bounded_settle_ms(5000) == 5000
    with pytest.raises(Exception, match="percent"):
        SwipeToFindAction._bounded_percent(0.04)
    with pytest.raises(Exception, match="settle_ms"):
        SwipeToFindAction._bounded_settle_ms(5001)


async def test_swipe_to_find_found_without_swipe():
    from executor.actions import ACTION_REGISTRY

    action = ACTION_REGISTRY["swipe_to_find"]()
    driver = _FoundAfterSwipesDriver(after=0)
    context = ExecutionContext(driver, _make_case([]))
    result = await action.execute(
        driver, context, {
            "element_id": 1, "direction": "down", "max_swipes": 3, "settle_ms": 0,
        }
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
        await action.execute(driver, context, {
            "element_id": 1, "max_swipes": 2, "settle_ms": 0,
        })


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
            {
                "order": 1, "action": "swipe_to_find", "element_id": 1,
                "params": {"max_swipes": 5, "settle_ms": 0},
            },
        ],
    )
    status, sent = await _run_and_capture(case, driver=_FoundAfterSwipesDriver(after=3))
    assert status == "passed"
    assert sent[0]["status"] == "passed"


class _StaleOnceElementSwipeDriver(MockDriver):
    """第一次控件内滑动前刷新页面，验证动作会重新定位后再执行。"""

    def __init__(self):
        super().__init__()
        self.staled = False

    def swipe_in_element(self, element, direction: str, percent: float) -> None:
        if not self.staled:
            self.staled = True
            self.refresh()
        super().swipe_in_element(element, direction, percent)


async def test_swipe_in_element_uses_target_element_and_retries_stale_element():
    from executor.actions import SwipeInElementAction

    driver = _StaleOnceElementSwipeDriver()
    context = ExecutionContext(driver, _make_case([]))

    result = await SwipeInElementAction().execute(
        driver,
        context,
        {"element_id": 1, "direction": "down", "percent": 0.4, "wait_timeout": 0},
    )

    assert result["status"] == "passed"
    assert driver.element_swipes == [("username", "down", 0.4)]
    assert driver.swipes == [("down", 500)]


async def test_swipe_in_region_scales_percentages_to_current_window():
    from executor.actions import SwipeInRegionAction

    driver = MockDriver()
    context = ExecutionContext(driver, _make_case([]))

    result = await SwipeInRegionAction().execute(
        driver,
        context,
        {
            "left_percent": 10,
            "top_percent": 20,
            "width_percent": 30,
            "height_percent": 40,
            "direction": "left",
            "percent": 0.5,
        },
    )

    assert result["status"] == "passed"
    assert driver.region_swipes == [(100, 400, 300, 800, "left", 0.5)]


def _slider_screen(text="4", track_width=1000, slider_x=490, track_height=40, slider_y=205):
    return [{
        "id": "track", "bounds": {"x": 100, "y": 200, "width": track_width, "height": track_height},
        "children": [{
            "id": "slider-parent", "bounds": {"x": 100, "y": 200, "width": track_width, "height": track_height},
            "children": [{"id": "slider", "text": text,
                          "bounds": {"x": slider_x, "y": slider_y, "width": 20, "height": 30}}],
        }],
    }]


def _slider_case_params(**overrides):
    params = {
        "element_id": 1, "min_value": 0, "max_value": 10,
        "target_value": 8, "settle_ms": 0,
    }
    params.update(overrides)
    return params


def _slider_context(driver):
    return ExecutionContext(driver, _make_case([], elements={
        "1": {"locator_type": "id", "locator_value": "slider"},
    }))


async def test_set_slider_value_drags_from_current_value_and_verifies_result():
    from executor.actions import SetSliderValueAction

    class SliderDriver(MockDriver):
        def drag_coordinate(self, start_x, start_y, end_x, end_y, duration_ms):
            super().drag_coordinate(start_x, start_y, end_x, end_y, duration_ms)
            self.state["slider"] = "8"

    driver = SliderDriver({"slider": "4"})
    driver.set_screen(_slider_screen(slider_x=490))
    context = ExecutionContext(
        driver,
        _make_case(
            [],
            elements={
                "1": {"locator_type": "id", "locator_value": "slider"},
            },
        ),
    )

    result = await SetSliderValueAction().execute(
        driver,
        context,
        {
            "element_id": 1,
            "min_value": 0,
            "max_value": 10,
            "target_value": 8,
            "duration_ms": 300,
            "settle_ms": 0,
        },
    )

    assert result == {
        "status": "passed",
        "actual_value": "8",
        "target_value": 8.0,
        "changed": True,
        "verified": True,
        "interaction": "drag",
        "adjustments": 0,
    }
    assert driver.coordinate_drags == [(499, 219, 899, 219, 300)]
    assert driver.coordinate_taps == []


async def test_set_slider_value_drags_left_using_track_span():
    from executor.actions import SetSliderValueAction

    class LeftSliderDriver(MockDriver):
        def drag_coordinate(self, *args):
            super().drag_coordinate(*args)
            self.state["slider"] = "0"

    driver = LeftSliderDriver({"slider": "6"})
    driver.set_screen(_slider_screen(text="6", slider_x=688))
    result = await SetSliderValueAction().execute(
        driver, _slider_context(driver), _slider_case_params(target_value=0)
    )
    assert result["status"] == "passed"
    assert driver.coordinate_drags == [(697, 219, 100, 219, 300)]
    assert driver.coordinate_taps == []


async def test_set_slider_value_thin_track_allows_oversized_thumb_and_uses_endpoints():
    from executor.actions import SetSliderValueAction

    class ThinTrackDriver(MockDriver):
        def drag_coordinate(self, *args):
            super().drag_coordinate(*args)
            self.state["slider"] = "10"

    driver = ThinTrackDriver({"slider": "0"})
    driver.set_screen(_slider_screen(text="0", slider_x=91, track_height=4, slider_y=189))
    result = await SetSliderValueAction().execute(
        driver, _slider_context(driver), _slider_case_params(target_value=10)
    )
    assert result["status"] == "passed"
    assert driver.coordinate_drags == [(100, 203, 1099, 203, 300)]


async def test_set_slider_value_rejects_button_center_outside_thin_track():
    from executor import ElementNotFound
    from executor.actions import SetSliderValueAction

    driver = MockDriver({"slider": "4"})
    driver.set_screen(_slider_screen(slider_x=1200))
    with pytest.raises(ElementNotFound, match="有效范围"):
        await SetSliderValueAction().execute(driver, _slider_context(driver), _slider_case_params())

    driver.set_screen(_slider_screen(track_height=4, slider_y=300))
    with pytest.raises(ElementNotFound, match="有效范围"):
        await SetSliderValueAction().execute(driver, _slider_context(driver), _slider_case_params())


async def test_set_slider_value_rejects_missing_current_value_without_tap():
    from executor import DriverError
    from executor.actions import SetSliderValueAction

    driver = MockDriver()
    driver.set_screen(
        [{"id": "slider", "bounds": {"x": 0, "y": 20, "width": 100, "height": 20}}]
    )
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "id", "locator_value": "slider"}}),
    )

    with pytest.raises(DriverError, match="自身文本"):
        await SetSliderValueAction().execute(
            driver, context, {"element_id": 1, "min_value": 0, "max_value": 10, "target_value": 5}
        )
    assert driver.coordinate_taps == []


async def test_set_slider_value_rejects_invalid_runtime_range():
    from executor import DriverError
    from executor.actions import SetSliderValueAction

    with pytest.raises(DriverError, match="目标值必须在"):
        await SetSliderValueAction().execute(
            MockDriver(),
            ExecutionContext(MockDriver(), _make_case([])),
            {"element_id": 1, "min_value": 52, "max_value": 100, "target_value": 40},
        )


async def test_set_slider_value_does_not_treat_range_text_as_current_value():
    from executor import DriverError
    from executor.actions import SetSliderValueAction

    driver = MockDriver()
    driver.set_screen([
        {
            "id": "slider-row",
            "bounds": {"x": 20, "y": 100, "width": 200, "height": 40},
            # 行容器文本包含当前值与范围；自动解析不能取第一个数字。
            "text": "4 52~100",
        },
    ])
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "id", "locator_value": "slider-row"}}),
    )

    with pytest.raises(DriverError, match="自身文本"):
        await SetSliderValueAction().execute(
            driver, context, {"element_id": 1, "min_value": 52, "max_value": 100, "target_value": 76}
        )
    assert driver.coordinate_drags == []
    assert driver.coordinate_taps == []


async def test_set_slider_value_rejects_empty_slider_text():
    from executor import DriverError
    from executor.actions import SetSliderValueAction

    class NoReadDriver(MockDriver):
        def get_text(self, element):
            return ""

        def get_attribute(self, element, attribute):
            return ""

    driver = NoReadDriver()
    driver.set_screen([{"id": "slider", "bounds": {"x": 0, "y": 0, "width": 101, "height": 20}}])
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "id", "locator_value": "slider"}}),
    )
    with pytest.raises(DriverError, match="自身文本"):
        await SetSliderValueAction().execute(
            driver, context, {"element_id": 1, "min_value": 0, "max_value": 10, "target_value": 5}
        )
    assert driver.coordinate_taps == []


async def test_set_slider_value_does_not_drag_when_already_at_target():
    from executor.actions import SetSliderValueAction

    driver = MockDriver({"slider": "8"})
    driver.set_screen(_slider_screen(slider_x=490))
    context = _slider_context(driver)
    result = await SetSliderValueAction().execute(
        driver,
        context,
        {
            "element_id": 1,
            "min_value": 0,
            "max_value": 10,
            "target_value": 8,
            "settle_ms": 0,
        },
    )
    assert result["status"] == "passed"
    assert result["interaction"] == "noop"
    assert result["changed"] is False
    assert driver.coordinate_drags == []
    assert driver.coordinate_taps == []


async def test_set_slider_value_relocates_and_recomputes_track_for_correction(monkeypatch):
    from executor.actions import SetSliderValueAction

    class CorrectingDriver(MockDriver):
        def __init__(self):
            super().__init__({"slider": "4"})
            self.drag_count = 0

        def drag_coordinate(self, *args):
            super().drag_coordinate(*args)
            self.drag_count += 1
            if self.drag_count == 1:
                self.state["slider"] = "7"
                self.set_screen(_slider_screen(text="7", track_width=600, slider_x=300))
            else:
                self.state["slider"] = "8"

    driver = CorrectingDriver()
    driver.set_screen(_slider_screen(slider_x=490))

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    result = await SetSliderValueAction().execute(
        driver, _slider_context(driver), _slider_case_params(max_adjustments=2)
    )
    assert result["adjustments"] == 1
    assert driver.coordinate_drags[1] == (309, 219, 369, 219, 300)


async def test_set_slider_value_settle_wait_honors_stop_request(monkeypatch):
    from executor.actions import SetSliderValueAction

    stopped = False
    sleep_calls: list[float] = []

    async def interruptible_sleep(seconds: float):
        nonlocal stopped
        sleep_calls.append(seconds)
        stopped = True

    monkeypatch.setattr(asyncio, "sleep", interruptible_sleep)
    driver = MockDriver({"slider": "4"})
    driver.set_screen(_slider_screen())
    context = _slider_context(driver)
    context.should_stop = lambda: stopped

    with pytest.raises(StopRequested):
        await SetSliderValueAction().execute(
            driver,
            context,
            {
                "element_id": 1,
                "min_value": 0,
                "max_value": 10,
                "target_value": 8,
                "settle_ms": 300,
            },
        )
    assert driver.coordinate_taps == []
    # settle 等待按不超过 50ms 的片段运行，睡眠期间切换 stop 后立即退出。
    assert sleep_calls == [0.05]


async def test_set_slider_value_propagates_real_value_read_errors():
    from executor import DriverError
    from executor.actions import SetSliderValueAction

    class BrokenReadDriver(MockDriver):
        def get_text(self, element):
            raise DriverError("Appium session disconnected")

    driver = BrokenReadDriver()
    driver.set_screen([{"id": "slider", "bounds": {"x": 0, "y": 0, "width": 101, "height": 20}}])
    context = ExecutionContext(
        driver,
        _make_case([], elements={"1": {"locator_type": "id", "locator_value": "slider"}}),
    )
    with pytest.raises(DriverError, match="session disconnected"):
        await SetSliderValueAction().execute(
            driver,
            context,
            {
                "element_id": 1,
                "min_value": 0,
                "max_value": 10,
                "target_value": 8,
                "verify_value": False,
                "settle_ms": 0,
            },
        )
    assert driver.coordinate_taps == []
    assert driver.coordinate_drags == []


async def test_set_slider_value_exhausted_adjustments_reports_correction_count():
    from executor import DriverError
    from executor.actions import SetSliderValueAction

    class InaccurateDriver(MockDriver):
        def drag_coordinate(self, start_x, start_y, end_x, end_y, duration_ms):
            super().drag_coordinate(start_x, start_y, end_x, end_y, duration_ms)
            self.state["slider"] = "7"

    driver = InaccurateDriver({"slider": "2"})
    driver.set_screen(_slider_screen(text="2", slider_x=290))
    context = _slider_context(driver)
    with pytest.raises(DriverError, match="已修正 1 次"):
        await SetSliderValueAction().execute(
            driver,
            context,
            {
                "element_id": 1,
                "min_value": 0,
                "max_value": 10,
                "target_value": 8,
                "max_adjustments": 1,
                "settle_ms": 0,
            },
        )
    assert len(driver.coordinate_drags) == 2


def test_appium_limited_swipes_use_uiautomator2_gesture_payloads():
    from executor.appium_driver import AppiumDriver

    class _Appium:
        def __init__(self):
            self.calls = []
            self.swipes = []

        def execute_script(self, name, payload):
            self.calls.append((name, payload))

        def get_window_size(self):
            return {"width": 1080, "height": 2400}

        def execute(self, command, payload):
            self.calls.append((command, payload))

        def swipe(self, *args):
            self.swipes.append(args)

    class _Element:
        id = "native-element-id"

    driver = AppiumDriver(device={"udid": "android-1", "platform": "android"})
    fake = _Appium()
    driver.driver = fake
    driver.swipe_in_element(_Element(), "up", 0.3)
    driver.swipe_in_region(100, 200, 300, 400, "down", 0.5)
    driver.drag_coordinate(10, 20, 300, 20, 300)

    assert fake.calls[:2] == [
        ("mobile: swipeGesture", {"elementId": "native-element-id", "direction": "up", "percent": 0.3}),
        (
            "mobile: swipeGesture",
            {"left": 100, "top": 200, "width": 300, "height": 400, "direction": "down", "percent": 0.5},
        ),
    ]
    assert fake.swipes == []
    assert fake.calls[2][0] == "actions"
    actions = fake.calls[2][1]["actions"][0]["actions"]
    assert [(item["type"], item.get("x"), item.get("y")) for item in actions] == [
        ("pointerMove", 10, 20),
        ("pointerDown", None, None),
        ("pointerMove", 300, 20),
        ("pointerUp", None, None),
    ]
    assert actions[2]["duration"] == 300


def test_appium_limited_swipes_reject_ios():
    from executor.appium_driver import AppiumDriver
    from executor.driver import DriverError

    driver = AppiumDriver(device={"udid": "ios-1", "platform": "ios"})
    with pytest.raises(DriverError, match="仅支持 Android"):
        driver.swipe_in_region(0, 0, 100, 100, "up", 0.3)


# ---------- Step 2：套件级执行（协议 V2） ----------


async def _run_suite_and_capture(suite, parameters=None, driver=None):
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    driver = driver or MockDriver()
    runner = TestRunner(driver, fake_send, 100, parameters)
    status = await runner.run_suite(suite)
    return status, sent


def _suite_case(*, execution_case_id=2001, steps=None, assertions=None):
    normalized_steps = [
        {"execution_step_id": 7000 + index, **step}
        for index, step in enumerate(
            steps or [{"order": 1, "action": "sleep", "params": {"duration": 0.01}}],
            start=1,
        )
    ]
    normalized_assertions = [
        {"execution_assertion_id": 8000 + index, **assertion}
        for index, assertion in enumerate(assertions or [], start=1)
    ]
    if normalized_assertions and normalized_steps:
        target = next(
            (step for step in reversed(normalized_steps) if step.get("phase") not in {"teardown", "case_teardown"}),
            normalized_steps[-1],
        )
        target["assertions"] = normalized_assertions
    return {
        "execution_case_id": execution_case_id,
        "case_id": 1,
        "case_name": "用例",
        "case_order": 1,
        "module_name": None,
        "steps_snapshot": normalized_steps,
        "elements_snapshot": {
            "1": {"locator_type": "id", "locator_value": "username"},
            "2": {"locator_type": "id", "locator_value": "login_btn"},
        },
    }


async def test_run_suite_passing_reports_nested_structure():
    """V2：run_suite 上报 suite_status(running/terminal)、case_status(running/terminal)。"""
    suite = _make_suite([_suite_case()], setup_steps=[], teardown_steps=[])
    status, sent = await _run_suite_and_capture(suite)
    assert status == "passed"
    suite_statuses = [m for m in sent if m["type"] == "suite_status"]
    assert suite_statuses[0]["execution_suite_id"] == 1001
    assert [m["status"] for m in suite_statuses] == ["running", "passed"]
    case_statuses = [m for m in sent if m["type"] == "case_status"]
    assert [m["status"] for m in case_statuses] == ["running", "passed"]
    step_msg = next(m for m in sent if m["type"] == "step_result")
    assert step_msg["execution_case_id"] == 2001
    assert "execution_suite_id" not in step_msg


async def test_run_suite_uses_execution_nodes_for_suite_setup_and_teardown():
    """套件前后置必须通过统一节点上报，不能再发送旧 step_result。"""
    suite = _make_suite([_suite_case()], setup_steps=[], teardown_steps=[])
    suite["setup_nodes"] = [
        {"execution_node_id": 5101, "kind": "action", "phase": "suite_setup",
         "order": 1, "action": "sleep", "params": {"duration": 0}}
    ]
    suite["teardown_nodes"] = [
        {"execution_node_id": 6101, "kind": "action", "phase": "suite_teardown",
         "order": 1, "action": "sleep", "params": {"duration": 0}}
    ]

    status, sent = await _run_suite_and_capture(suite)

    assert status == "passed"
    suite_nodes = [
        message for message in sent
        if message.get("execution_node_id") in {5101, 6101}
    ]
    assert [message["type"] for message in suite_nodes] == [
        "node_started", "node_result", "node_started", "node_result"
    ]
    assert [message["execution_suite_id"] for message in suite_nodes] == [1001] * 4
    assert [message["status"] for message in suite_nodes if message["type"] == "node_result"] == [
        "passed", "passed"
    ]
    assert not [
        message for message in sent
        if message.get("type") == "step_result"
        and message.get("phase") in {"suite_setup", "suite_teardown"}
    ]


async def test_run_suite_continues_after_failed_suite_setup_assertion(monkeypatch):
    monkeypatch.setattr(TestRunner, "ACTION_MIN_INTERVAL_SECONDS", 0)
    driver = MockDriver()
    suite = _make_suite(
        [_suite_case(steps=[{"order": 1, "action": "input", "element_id": 2, "params": {"value": "case"}}])],
        setup_steps=[],
        teardown_steps=[],
        elements_snapshot={
            "1": {"locator_type": "id", "locator_value": "username"},
            "2": {"locator_type": "id", "locator_value": "target"},
        },
    )
    suite["setup_nodes"] = [
        {
            "execution_node_id": 5101, "kind": "assertion", "phase": "suite_setup", "order": 1,
            "type": "text_equals", "element_id": 1, "params": {"expected": "wrong"},
            "max_wait_seconds": 0,
        },
        {
            "execution_node_id": 5102, "kind": "action", "phase": "suite_setup", "order": 2,
            "action": "input", "element_id": 2, "params": {"value": "setup"},
        },
    ]
    suite["teardown_nodes"] = []

    status, sent = await _run_suite_and_capture(suite, driver=driver)

    assert status == "failed"
    assert driver.state["target"] == "setup"
    assert driver.state["login_btn"] == "case"
    setup_results = [
        message for message in sent
        if message["type"] == "node_result" and message.get("execution_suite_id") == 1001
    ]
    assert [message["execution_node_id"] for message in setup_results] == [5101, 5102]
    assert [
        message["status"]
        for message in sent
        if message["type"] == "case_status"
    ] == ["running", "passed"]


async def test_run_suite_preserves_node_error_status():
    """套件节点 error 必须聚合为套件 error，而不是普通 failed。"""
    suite = _make_suite([_suite_case()], setup_steps=[], teardown_steps=[])
    suite["setup_nodes"] = [
        {"execution_node_id": 5101, "kind": "action", "phase": "suite_setup",
         "order": 1, "action": "unknown_action", "params": {}}
    ]
    suite["teardown_nodes"] = []

    status, sent = await _run_suite_and_capture(suite)

    assert status == "error"
    assert [message["status"] for message in sent if message["type"] == "suite_status"] == [
        "running", "error"
    ]
    assert [message["status"] for message in sent if message["type"] == "node_result"] == [
        "error"
    ]
    assert [message["status"] for message in sent if message["type"] == "case_status"] == [
        "skipped"
    ]


async def test_run_suite_setup_failure_skips_cases_runs_teardown():
    """V2：suite_setup 失败 → 用例 skipped，仍执行 suite_teardown，套件终态 failed。"""
    driver = MockDriver()
    suite = _make_suite(
        [_suite_case()],
        setup_steps=[
            {"execution_step_id": 5001, "action": "no_such_action", "order": 1, "params": {}},
        ],
        teardown_steps=[
            {"execution_step_id": 5009, "action": "sleep", "order": 1, "params": {"duration": 0.01}},
        ],
    )
    status, sent = await _run_suite_and_capture(suite, driver=driver)
    assert status == "failed"
    suite_statuses = [m for m in sent if m["type"] == "suite_status"]
    assert [m["status"] for m in suite_statuses] == ["running", "failed"]
    case_statuses = [m for m in sent if m["type"] == "case_status"]
    assert [m["status"] for m in case_statuses] == ["skipped"]
    step_msgs = [m for m in sent if m["type"] == "step_result"]
    # 套件前置 + 套件后置；用例步骤未执行
    assert [m["execution_suite_id"] for m in step_msgs] == [1001, 1001]
    assert [m["execution_step_id"] for m in step_msgs] == [5001, 5009]
    assert [m["phase"] for m in step_msgs] == ["suite_setup", "suite_teardown"]
    assert [m["action"] for m in step_msgs] == ["no_such_action", "sleep"]


async def test_run_suite_setup_element_uses_suite_elements_snapshot():
    """V2：套件前后置步通过套件自身 elements_snapshot 解析元素（不再恒为空表）。

    回归：Worker 未下发套件 elements_snapshot → Agent 以空元素表执行套件步，
    元素查找失败（ElementNotFound）。assert setup 点击成功并不抛元素缺失。
    """
    driver = MockDriver()
    suite = _make_suite(
        [_suite_case()],
        setup_steps=[
            {
                "execution_step_id": 5001,
                "action": "click",
                "element_id": "9",
                "order": 1,
                "params": {},
            },
        ],
        teardown_steps=[],
        elements_snapshot={"9": {"locator_type": "id", "locator_value": "svc_btn"}},
    )
    status, sent = await _run_suite_and_capture(suite, driver=driver)
    assert status == "passed"
    setup_step = next(m for m in sent if m["type"] == "step_result")
    assert setup_step["action"] == "click"
    assert setup_step["status"] == "passed"


async def test_run_suite_step_result_carries_execution_step_id_for_suite_steps():
    """V2：套件步上报携带 execution_step_id，且执行顺序为 setUp → cases → teardown。"""
    suite = _make_suite(
        [_suite_case()],
        setup_steps=[
            {"execution_step_id": 5001, "action": "launch_app", "order": 1, "params": {"package": "com.demo"}},
        ],
        teardown_steps=[
            {"execution_step_id": 5009, "action": "close_app", "order": 1, "params": {}},
        ],
    )
    status, sent = await _run_suite_and_capture(suite)
    assert status == "passed"
    step_msg = next(m for m in sent if m["type"] == "step_result" and m["phase"] == "suite_setup")
    assert step_msg["execution_step_id"] == 5001
    assert step_msg["execution_suite_id"] == 1001
    assert "execution_case_id" not in step_msg
    teardown_msg = next(m for m in sent if m["type"] == "step_result" and m["phase"] == "suite_teardown")
    assert teardown_msg["execution_step_id"] == 5009


async def test_run_suite_case_failure_makes_suite_failed():
    """V2：任一名用例失败 → 套件终态 failed，但其余用例仍执行。"""
    suite = _make_suite(
        [
            _suite_case(execution_case_id=2001, steps=[
                {"order": 1, "action": "input", "element_id": 1, "params": {"value": "admin"}},
            ], assertions=[
                {"order": 1, "type": "text_equals", "element_id": 1, "params": {"expected": "wrong"}},
            ]),
            _suite_case(
                execution_case_id=2002,
                steps=[{"order": 1, "action": "sleep", "params": {"duration": 0.01}}],
            ),
        ],
    )
    status, sent = await _run_suite_and_capture(suite)
    assert status == "failed"
    assert [m["status"] for m in sent if m["type"] == "suite_status"] == ["running", "failed"]
    case_statuses = [m for m in sent if m["type"] == "case_status"]
    # 2001 → running, failed；2002 → running, passed
    assert [(m["execution_case_id"], m["status"]) for m in case_statuses] == [
        (2001, "running"),
        (2001, "failed"),
        (2002, "running"),
        (2002, "passed"),
    ]


async def test_run_suite_empty_cases_terminates_skipped():
    """V2：套件 entirely 无 cases（健壮性）→ 无失败时终态 skipped。"""
    suite = _make_suite([], setup_steps=[], teardown_steps=[])
    status, sent = await _run_suite_and_capture(suite)
    assert status == "skipped"
    assert [m["status"] for m in sent if m["type"] == "suite_status"] == ["running", "skipped"]


async def test_run_suite_stop_raises_stop_requested():
    """V2：套件执行中停止 → StopRequested 上抛（由 _run_execution 收敛）。"""
    suite = _make_suite(
        [_suite_case(steps=[{"order": 1, "action": "sleep", "params": {"duration": 0.01}}])],
    )
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    runner = TestRunner(MockDriver(), fake_send, 100, should_stop=lambda: True)
    with pytest.raises(StopRequested):
        await runner.run_suite(suite)

# ---------- Step 12：列表内双向滑动查找文字并点击 ----------


class _ListScrollDriver(MockDriver):
    """为容器元素提供几何 bounds；按方向精确控制滚动页面与边界。

    swipe_in_region 由 Action 调用，不返回边界值。
    测试通过 up_pages/down_pages 回调按方向滑动次数返回新屏幕，
    由滑动前后的页面签名变化判断是否到达边界。
    """

    def __init__(self, container_bounds, container_locator="list"):
        super().__init__()
        self.container_bounds = container_bounds
        self.container_locator = container_locator
        self.clicked: list[str] = []
        self.up_pages = None
        self.down_pages = None
        self.up_count = 0
        self.down_count = 0
        self.parent_calls = 0
        self.target_queries: list[tuple[str, str, int | float | None]] = []

    def find_element(self, locator_type, locator_value, wait_timeout=10):
        el = super().find_element(locator_type, locator_value, wait_timeout)
        if locator_value == self.container_locator:
            el._bounds = dict(self.container_bounds)
        return el

    def click(self, element):
        self._assert_fresh(element)
        self.clicked.append(element.text or element.get_attribute("id"))

    def get_parent_element(self, element, wait_timeout=10):
        self._assert_fresh(element)
        self.parent_calls += 1
        parent = MockElement("synthetic-parent", generation=self._screen_generation)
        parent._bounds = dict(self.container_bounds)
        return parent

    def _apply_direction_page(self, direction: str) -> None:
        if direction == "up":
            self.up_count += 1
            if self.up_pages is not None:
                screen = self.up_pages(self.up_count)
                if screen is not None:
                    self.set_screen(screen)
        else:
            self.down_count += 1
            if self.down_pages is not None:
                screen = self.down_pages(self.down_count)
                if screen is not None:
                    self.set_screen(screen)

    def find_elements_in_element(self, element, locator_type, locator_value, wait_timeout=0):
        if locator_value != "new UiSelector()":
            self.target_queries.append((locator_type, locator_value, wait_timeout))
        return super().find_elements_in_element(element, locator_type, locator_value, wait_timeout)

    def swipe_in_element(self, element, direction: str, percent: float, speed=None) -> None:
        self._assert_fresh(element)
        self._apply_direction_page(direction)
        self.element_swipes.append((element.locator_value, direction, percent))
        self.element_swipe_speeds.append(speed)
        self.swipe(direction)

    def swipe_in_region(
        self, left: int, top: int, width: int, height: int,
        direction: str, percent: float, speed=None,
    ) -> None:
        self._apply_direction_page(direction)
        self.region_swipes.append((left, top, width, height, direction, percent))
        self.region_swipe_speeds.append(speed)
        self.swipe(direction)

    def swipe_coordinate(self, start_x, start_y, end_x, end_y, duration_ms) -> None:
        direction = "up" if end_y < start_y else "down"
        self._apply_direction_page(direction)
        super().swipe_coordinate(start_x, start_y, end_x, end_y, duration_ms)


def _find_text_params(**overrides) -> dict:
    params = {
        "element_id": 1,
        "target_text": "系统时间",
        "match_mode": "equals",
        "preferred_direction": "up",
        "max_swipes_per_direction": 8,
        "percent": 0.3,
        "container_wait_timeout": 10,
        "settle_ms": 0,
    }
    params.update(overrides)
    return params


def _list_case(params, elements=None) -> dict:
    elements = elements or {"1": {"locator_type": "id", "locator_value": "list"}}
    return _make_case(
        [{"order": 1, "action": "swipe_in_element_find_text_click", "element_id": 1, "params": params}],
        elements=elements,
    )


def _run_find_text(driver, params, variables=None, should_stop=None):
    from executor.actions import ACTION_REGISTRY

    action = ACTION_REGISTRY["swipe_in_element_find_text_click"]()
    context = ExecutionContext(driver, _list_case(params), variables, should_stop=should_stop)
    return action, context


_FULL_SCREEN = {"x": 0, "y": 0, "width": 1000, "height": 2000}


async def test_find_text_click_found_without_swipe():
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    action, context = _run_find_text(driver, _find_text_params())
    result = await action.execute(driver, context, _find_text_params())
    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 0
    assert driver.clicked == ["系统时间"]
    assert driver.region_swipes == []


@pytest.mark.parametrize(
    ("match_mode", "target_text", "expected_selector"),
    [
        ("equals", "系统时间", './*[@text="系统时间"]'),
        ("contains", "系统", './*[contains(@text,"系统")]'),
    ],
)
async def test_find_text_click_uses_direct_child_relative_xpath(
    match_mode, target_text, expected_selector
):
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    params = _find_text_params(match_mode=match_mode, target_text=target_text)
    action, context = _run_find_text(driver, params)

    result = await action.execute(driver, context, params)

    assert result["status"] == "passed"
    assert driver.target_queries
    assert all(locator_type == "xpath" for locator_type, _value, _wait in driver.target_queries)
    assert all(locator_value == expected_selector for _type, locator_value, _wait in driver.target_queries)
    assert all(locator_value.startswith("./*") for _type, locator_value, _wait in driver.target_queries)
    assert driver.region_swipes == []


async def test_find_text_click_only_matches_direct_child_in_current_list():
    driver = _IdRecordingScrollDriver(_FULL_SCREEN)
    driver.set_screen([
        {
            "id": "target-list",
            "bounds": _FULL_SCREEN,
            "children": [
                {
                    "id": "direct-target",
                    "text": "目标",
                    "bounds": {"x": 100, "y": 500, "width": 300, "height": 80},
                },
                {
                    "id": "row",
                    "bounds": {"x": 100, "y": 700, "width": 300, "height": 80},
                    "children": [
                        {
                            "id": "nested-target",
                            "text": "目标",
                            "bounds": {"x": 100, "y": 700, "width": 300, "height": 80},
                        }
                    ],
                },
            ],
        },
        {
            "id": "other-list",
            "bounds": _FULL_SCREEN,
            "children": [{"id": "other-target", "text": "目标", "bounds": {"x": 100, "y": 600, "width": 300, "height": 80}}],
        },
    ])
    params = _find_text_params(target_text="目标")
    action = ACTION_REGISTRY["swipe_in_element_find_text_click"]()
    context = ExecutionContext(
        driver,
        _list_case(params, elements={"1": {"locator_type": "id", "locator_value": "target-list"}}),
    )

    result = await action.execute(driver, context, params)

    assert result["status"] == "passed"
    assert driver.last_clicked_id == "direct-target"
    assert driver.region_swipes == []


async def test_find_text_click_xpath_literal_handles_quotes_backslash_and_newline():
    target_text = 'a"b\'c\\\nline'
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "special", "text": target_text, "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    params = _find_text_params(target_text=target_text)
    action, context = _run_find_text(driver, params)

    result = await action.execute(driver, context, params)

    assert result["status"] == "passed"
    assert driver.clicked == [target_text]
    assert driver.target_queries
    assert driver.target_queries[0][0] == "xpath"
    assert driver.target_queries[0][1].startswith("./*[@text=concat(")
    assert "\\" in driver.target_queries[0][1]
    assert "\n" in driver.target_queries[0][1]


async def test_find_text_click_logs_empty_query_and_final_hit(caplog):
    caplog.set_level(logging.INFO, logger="agent.actions")
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i1", "text": "其他", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    driver.up_pages = lambda count: [
        {"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}
    ] if count == 1 else None

    params = _find_text_params(max_swipes_per_direction=1)
    action, context = _run_find_text(driver, params)
    result = await action.execute(driver, context, params)

    messages = [record.getMessage() for record in caplog.records]
    assert result["status"] == "passed"
    assert any("列表目标查询返回空" in message and "query_kind=target" in message for message in messages)
    assert any("strategy=xpath" in message and "./*[@text=\"系统时间\"]" in message for message in messages)
    assert any("列表目标实际点击" in message and "expected_click_point=" in message for message in messages)
    assert any("列表文字查询未命中" in message and "next_scroll_direction=up" in message for message in messages)


async def test_find_text_click_logs_candidate_rejection_and_caps_long_list(caplog):
    caplog.set_level(logging.INFO, logger="agent.actions")
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([
        {
            "id": f"target-{index}",
            "text": "系统时间",
            "bounds": {"x": 100, "y": 500 + index * 100, "width": 200, "height": 50},
        }
        for index in range(25)
    ])
    action, context = _run_find_text(driver, _find_text_params())
    action._diagnostic_target_text = "系统时间"
    action._diagnostic_match_mode = "equals"
    action._diagnostic_phase = "forward"
    action._diagnostic_round = 1
    container = context.find_element(1, wait_timeout=0)
    container._bounds = dict(_FULL_SCREEN)

    target, direction = action._find_target_in_container(
        driver, container, _FULL_SCREEN, './*[@text="系统时间"]'
    )

    messages = [record.getMessage() for record in caplog.records]
    assert target is not None
    assert direction is None
    assert any("safe_clickable=False" in message for message in messages)
    assert any("列表目标候选日志省略" in message and "omitted=5" in message for message in messages)
    assert sum("列表目标候选:" in message for message in messages) == 20


def test_find_text_diagnostics_do_not_read_remote_candidate_attributes():
    class CountingElement(MockElement):
        def __init__(self, locator_value, rect):
            super().__init__(locator_value, {"text": "目标"})
            self.rect_value = rect
            self.displayed_calls = 0
            self.enabled_calls = 0
            self.attribute_calls = 0

        def is_displayed(self):
            self.displayed_calls += 1
            return True

        def is_enabled(self):
            self.enabled_calls += 1
            return True

        def get_attribute(self, name):
            self.attribute_calls += 1
            raise AssertionError(f"unexpected diagnostic attribute read: {name}")

    class CountingDriver:
        def __init__(self, elements):
            self.elements = elements
            self.find_elements_calls = 0
            self.rect_calls = 0
            self.attribute_calls = 0

        def find_elements_in_element(self, element, locator_type, locator_value, wait_timeout=0):
            self.find_elements_calls += 1
            return self.elements

        def get_element_rect(self, element):
            self.rect_calls += 1
            return element.rect_value

        def get_attribute(self, element, name):
            self.attribute_calls += 1
            raise AssertionError(f"unexpected driver attribute read: {name}")

    elements = [
        CountingElement("first", {"x": 100, "y": 400, "width": 200, "height": 50}),
        CountingElement("second", {"x": 100, "y": 700, "width": 200, "height": 50}),
    ]
    driver = CountingDriver(elements)
    action = ACTION_REGISTRY["swipe_in_element_find_text_click"]()

    target, direction = action._find_target_in_container(
        driver, MockElement("list"), _FULL_SCREEN, './*[@text="目标"]'
    )

    assert target is elements[1]
    assert direction is None
    assert driver.find_elements_calls == 1
    assert driver.attribute_calls == 0
    assert driver.rect_calls == 4  # 一次安全判定 + 一次中心选择，每个候选各一次
    assert all(element.displayed_calls == 1 for element in elements)
    assert all(element.enabled_calls == 1 for element in elements)
    assert all(element.attribute_calls == 0 for element in elements)


class _FindTextQueryErrorDriver(_ListScrollDriver):
    def find_elements_in_element(self, element, locator_type, locator_value, wait_timeout=0):
        raise RuntimeError("synthetic target query failure")


async def test_find_text_click_logs_query_exception_without_swallowing(caplog):
    caplog.set_level(logging.WARNING, logger="agent.actions")
    driver = _FindTextQueryErrorDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i1", "text": "其他", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    params = _find_text_params(max_swipes_per_direction=1)
    action, context = _run_find_text(driver, params)

    with pytest.raises(RuntimeError, match="synthetic target query failure"):
        await action.execute(driver, context, params)

    assert any(
        "列表目标查询异常" in record.getMessage()
        and "exception_type=RuntimeError" in record.getMessage()
        and "synthetic target query failure" in record.getMessage()
        for record in caplog.records
    )


async def test_find_text_click_contains_match():
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "t1", "text": "前缀系统时间后缀", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    action, context = _run_find_text(driver, _find_text_params(match_mode="contains"))
    result = await action.execute(driver, context, _find_text_params(match_mode="contains"))
    assert result["status"] == "passed"
    assert driver.clicked == ["前缀系统时间后缀"]


async def test_find_text_click_scroll_preferred_direction():
    driver = _ListScrollDriver(_FULL_SCREEN)
    # 初始页无目标；上滑 1 次后出现目标
    driver.set_screen([{"id": "i1", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    target_page = [{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.up_pages = lambda count: target_page if count >= 1 else None
    action, context = _run_find_text(driver, _find_text_params())
    result = await action.execute(driver, context, _find_text_params())
    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 1
    assert driver.up_count == 1
    assert driver.down_count == 0


async def test_find_text_click_scroll_preferred_fails_reverse_crosses_start():
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "起点项", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    driver.up_pages = lambda count: [{"id": f"u{count}", "text": f"上页{count}", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    # 下滑：2 次回到起点，再 1 次越过起点出现目标
    target_page = [{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.down_pages = lambda count: target_page if count >= 3 else [
        {"id": f"d{count}", "text": f"下页{count}", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}
    ]
    params = _find_text_params(max_swipes_per_direction=2)
    action, context = _run_find_text(driver, params)
    result = await action.execute(driver, context, params)
    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 5  # 2 上滑（含边界）+ 3 下滑（2 返起点 + 1 越过）
    assert driver.up_count == 2
    assert driver.down_count == 3


async def test_find_text_click_reverse_boundary_last_screen_still_queried():
    """回归：反向最后一次手势到达边界，但该次手势后的页面仍需查询。

    此时新页面已经出现且含目标，修复后应先查询新页面，再根据签名变化决定是否结束。
    """
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    # 首选方向首次上滑后页面不变，视为到达边界，无目标
    # 反向首次下滑后目标出现在最后一屏
    target_page = [{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.down_pages = lambda count: target_page if count >= 1 else None
    params = _find_text_params(max_swipes_per_direction=1)
    action, context = _run_find_text(driver, params)
    result = await action.execute(driver, context, params)
    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 2  # 1 上滑（边界）+ 1 下滑（边界）后命中最后一屏
    assert driver.up_count == 1
    assert driver.down_count == 1
    assert driver.clicked == ["系统时间"]


async def test_find_text_click_preferred_boundary_switches_to_reverse():
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    # 上滑一次后页面不变，视为到达边界
    target_page = [{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.down_pages = lambda count: target_page if count >= 2 else [
        {"id": "d1", "text": "下页1", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}
    ]
    params = _find_text_params(max_swipes_per_direction=1)
    action, context = _run_find_text(driver, params)
    result = await action.execute(driver, context, params)
    assert result["status"] == "passed"
    assert driver.up_count == 1
    assert driver.down_count == 2


async def test_find_text_click_ignores_exhausted_direction_hint():
    """页面签名不变时仍执行完首方向预算，再按固定次数反向。"""
    driver = _ListScrollDriver(_FULL_SCREEN)
    offscreen = [{"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": 2200, "width": 200, "height": 50}}]
    visible = [{"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.set_screen(offscreen)
    driver.up_pages = lambda _count: None
    driver.down_pages = lambda count: visible if count == 1 else None
    action, context = _run_find_text(driver, _find_text_params(max_swipes_per_direction=2))

    result = await action.execute(driver, context, _find_text_params(max_swipes_per_direction=2))

    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 3
    assert driver.up_count == 2
    assert driver.down_count == 1
    assert driver.coordinate_swipes == [
        (500, 1300, 500, 700, 300),
        (500, 1300, 500, 700, 300),
        (500, 700, 500, 1300, 300),
    ]


async def test_find_text_click_locks_first_direction_before_reversing(caplog):
    """屏外目标的 hint 变化不能让两个方向在首阶段交替，首阶段结束后才反向。"""
    caplog.set_level(logging.INFO, logger="agent.actions")
    driver = _ListScrollDriver(_FULL_SCREEN)
    offscreen_below = {"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": 2200, "width": 200, "height": 50}}
    offscreen_above = {"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": -200, "width": 200, "height": 50}}
    visible_target = {"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}
    driver.set_screen([offscreen_below, {"id": "start", "text": "起点", "bounds": {"x": 100, "y": 600, "width": 200, "height": 50}}])
    driver.up_pages = lambda count: [
        offscreen_above if count == 1 else offscreen_below,
        {"id": f"up-{count}", "text": f"上页{count}", "bounds": {"x": 100, "y": 600, "width": 200, "height": 50}},
    ]
    driver.down_pages = lambda count: [
        visible_target,
        {"id": f"down-{count}", "text": f"下页{count}", "bounds": {"x": 100, "y": 600, "width": 200, "height": 50}},
    ]
    params = _find_text_params(max_swipes_per_direction=2)
    action, context = _run_find_text(driver, params)

    result = await action.execute(driver, context, params)

    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 3
    assert driver.coordinate_swipes == [
        (500, 1300, 500, 700, 300),
        (500, 1300, 500, 700, 300),
        (500, 700, 500, 1300, 300),
    ]
    messages = [record.getMessage() for record in caplog.records]
    assert any("执行列表滑动: phase=forward direction=up" in message for message in messages)
    assert any("切换反向阶段: from=up to=down" in message for message in messages)
    assert any("执行列表滑动: phase=reverse direction=down" in message for message in messages)


class _StaleMovesTargetDriver(_ListScrollDriver):
    def __init__(self, container_bounds):
        super().__init__(container_bounds)
        self.stale_once = True

    def click(self, element):
        if self.stale_once:
            self.stale_once = False
            self.set_screen([
                {"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": 2200, "width": 200, "height": 50}},
            ])
            raise StaleObjectException("元素已失效")
        super().click(element)


async def test_find_text_click_stale_relocates_and_rechecks_viewport():
    """点击 stale 后目标移出视口时，不得沿用旧方向直接点击。"""
    driver = _StaleMovesTargetDriver(_FULL_SCREEN)
    initial = [{"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    visible = [{"id": "target", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.set_screen(initial)
    driver.up_pages = lambda count: visible if count == 1 else None
    params = _find_text_params(max_swipes_per_direction=2)
    action, context = _run_find_text(driver, params)

    result = await action.execute(driver, context, params)

    assert result["status"] == "passed"
    assert driver.up_count == 1
    assert driver.clicked == ["系统时间"]


async def test_find_text_click_both_directions_exhaust_raises():
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    driver.up_pages = lambda count: [{"id": f"u{count}", "text": f"上页{count}", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.down_pages = lambda count: [{"id": f"d{count}", "text": "下页", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    from executor import ElementNotFound

    action, context = _run_find_text(driver, _find_text_params(max_swipes_per_direction=2))
    with pytest.raises(ElementNotFound, match="系统时间"):
        await action.execute(driver, context, _find_text_params(max_swipes_per_direction=2))
    assert driver.up_count == 2
    assert driver.down_count == 4
    assert len(driver.coordinate_swipes) == 6


class _SwipeGestureNoResultDriver(_ListScrollDriver):
    """模拟 swipeGesture 不返回边界值，但手势会改变页面。"""

    def swipe_in_region(
        self, left: int, top: int, width: int, height: int,
        direction: str, percent: float, speed=None,
    ) -> None:
        super().swipe_in_region(left, top, width, height, direction, percent, speed)


async def test_find_text_click_uses_configured_budget_and_reverses_direction():
    """无论页面签名是否变化，首方向和反向都按固定预算执行。"""
    driver = _SwipeGestureNoResultDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    target_page = [{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.up_pages = lambda count: None
    driver.down_pages = lambda count: target_page if count >= 1 else None
    params = _find_text_params(max_swipes_per_direction=3)
    action, context = _run_find_text(driver, params)

    result = await action.execute(driver, context, params)

    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 4
    assert driver.up_count == 3
    assert driver.down_count == 1
    assert driver.coordinate_swipes == [
        (500, 1300, 500, 700, 300),
        (500, 1300, 500, 700, 300),
        (500, 1300, 500, 700, 300),
        (500, 700, 500, 1300, 300),
    ]


async def test_find_text_click_honors_down_preferred_direction():
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    target_page = [{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.down_pages = lambda count: target_page if count >= 2 else [
        {"id": "d1", "text": "下页1", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}
    ]
    params = _find_text_params(preferred_direction="down")
    action, context = _run_find_text(driver, params)

    result = await action.execute(driver, context, params)

    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 2
    assert driver.down_count == 2
    assert driver.up_count == 0


async def test_find_text_click_reverses_after_preferred_budget_exhausted():
    """首选方向未到边界但次数耗尽时，也必须切换到反方向。"""
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    driver.up_pages = lambda count: [
        {"id": f"u{count}", "text": f"上页{count}", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}
    ]
    target_page = [{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.down_pages = lambda count: target_page if count >= 1 else None
    params = _find_text_params(max_swipes_per_direction=2)
    action, context = _run_find_text(driver, params)

    result = await action.execute(driver, context, params)

    assert result["status"] == "passed"
    assert driver.up_count == 2
    assert driver.down_count == 1


class _StaleOnceScrollDriver(_ListScrollDriver):
    """首次滚动时模拟页面重绘导致容器失效，验证动作会重新定位容器再滚动。"""

    def __init__(self, container_bounds):
        super().__init__(container_bounds)
        self.staled = False
        self.scroll_attempts = 0

    def swipe_coordinate(self, start_x, start_y, end_x, end_y, duration_ms) -> None:
        self.scroll_attempts += 1
        if not self.staled:
            self.staled = True
            # 模拟滚动手势执行时页面重绘、元素失效：此句柄已过期
            from executor.driver import StaleObjectException

            raise StaleObjectException("元素已失效")
        super().swipe_coordinate(start_x, start_y, end_x, end_y, duration_ms)


async def test_find_text_click_scroll_stale_retries_relocates_container():
    """滚动过程因页面重绘抛 stale 时，应重新定位容器再滚动，而不是直接失败。"""
    driver = _StaleOnceScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    target_page = [{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.up_pages = lambda count: target_page if count >= 1 else None
    action, context = _run_find_text(driver, _find_text_params())
    result = await action.execute(driver, context, _find_text_params())
    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 1  # stale 重试一次后成功滚动 1 次并命中
    assert driver.up_count == 1
    assert driver.down_count == 0
    assert driver.scroll_attempts == 2  # 首次抛 stale，第二次成功


class _SignatureStaleDriver(_ListScrollDriver):
    def __init__(self, container_bounds):
        super().__init__(container_bounds)
        self.signature_stale_once = True

    def find_elements_in_element(self, element, locator_type, locator_value, wait_timeout=0):
        if locator_value == "new UiSelector()" and self.signature_stale_once:
            self.signature_stale_once = False
            raise StaleObjectException("列表子节点已失效")
        return super().find_elements_in_element(element, locator_type, locator_value, wait_timeout)


async def test_find_text_click_list_signature_stale_retries_and_continues():
    """列表局部签名读取遇到 stale 时应重新观测，不能直接终止动作。"""
    driver = _SignatureStaleDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    target_page = [{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}]
    driver.up_pages = lambda count: target_page if count == 1 else None
    action, context = _run_find_text(driver, _find_text_params())

    result = await action.execute(driver, context, _find_text_params())

    assert result["status"] == "passed"
    assert result["found_after_swipes"] == 1
    assert driver.clicked == ["系统时间"]


async def test_find_text_click_ignores_text_outside_container():
    # 容器只在屏幕中段可见；页面上方有同名文字但中心点在容器外，不应被点击
    container_bounds = {"x": 0, "y": 400, "width": 1000, "height": 1000}
    driver = _ListScrollDriver(container_bounds)
    driver.set_screen([
        {"id": "outside", "text": "系统时间", "bounds": {"x": 100, "y": 100, "width": 200, "height": 50}},
        {"id": "inside", "text": "系统时间", "bounds": {"x": 100, "y": 600, "width": 200, "height": 50}},
    ])
    action, context = _run_find_text(driver, _find_text_params())
    result = await action.execute(driver, context, _find_text_params())
    assert result["status"] == "passed"
    assert driver.clicked == ["系统时间"]


async def test_find_text_click_does_not_match_same_text_in_other_list():
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([
        {
            "id": "target-list",
            "class_name": "android.widget.ListView",
            "bounds": _FULL_SCREEN,
            "children": [{"id": "other", "text": "后门", "bounds": {"x": 100, "y": 500, "width": 300, "height": 80}}],
        },
        {
            "id": "other-list",
            "class_name": "android.widget.ListView",
            "bounds": _FULL_SCREEN,
            "children": [{"id": "same", "text": "后门", "bounds": {"x": 100, "y": 600, "width": 300, "height": 80}}],
        },
    ])
    params = _find_text_params(target_text="后门")
    from executor.actions import ACTION_REGISTRY

    action = ACTION_REGISTRY["swipe_in_element_find_text_click"]()
    context = ExecutionContext(
        driver,
        _list_case(params, elements={"1": {"locator_type": "id", "locator_value": "target-list"}}),
    )
    result = await action.execute(driver, context, params)

    assert result["status"] == "passed"
    assert driver.clicked == ["后门"]
    assert driver.region_swipes == []


class _IdRecordingScrollDriver(_ListScrollDriver):
    def click(self, element):
        self._assert_fresh(element)
        self.last_clicked_id = element.locator_value
        self.clicked.append(element.text or element.locator_value)


async def test_find_text_click_multiple_matches_picks_nearest_center_id():
    driver = _IdRecordingScrollDriver(_FULL_SCREEN)
    driver.set_screen([
        {"id": "far", "text": "选择", "bounds": {"x": 100, "y": 200, "width": 200, "height": 50}},
        {"id": "near", "text": "选择", "bounds": {"x": 400, "y": 700, "width": 200, "height": 50}},
    ])
    action, context = _run_find_text(driver, _find_text_params(target_text="选择"))
    result = await action.execute(driver, context, _find_text_params(target_text="选择"))
    assert result["status"] == "passed"
    assert driver.last_clicked_id == "near"


async def test_find_text_click_renders_variable_and_escapes():
    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "t1", "text": '含"引号"和\\反斜杠', "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    action, context = _run_find_text(
        driver,
        _find_text_params(target_text="${word}", match_mode="contains"),
        variables={"word": '含"引号"和\\反斜杠'},
    )
    result = await action.execute(driver, context, _find_text_params(target_text="${word}", match_mode="contains"))
    assert result["status"] == "passed"
    assert driver.clicked == ['含"引号"和\\反斜杠']


async def test_find_text_click_stop_requested():
    from executor import StopRequested

    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "i0", "text": "项目一", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    action, context = _run_find_text(driver, _find_text_params(), should_stop=lambda: True)
    with pytest.raises(StopRequested):
        await action.execute(driver, context, _find_text_params())


async def test_find_text_click_stale_retries_relocates():
    from executor.driver import StaleObjectException

    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])
    first = True

    def _click(el):
        nonlocal first
        if first:
            first = False
            raise StaleObjectException("元素已失效")
        _ListScrollDriver.click(driver, el)

    driver.click = _click
    action, context = _run_find_text(driver, _find_text_params())
    result = await action.execute(driver, context, _find_text_params())
    assert result["status"] == "passed"
    assert driver.clicked == ["系统时间"]
def test_appium_scroll_in_element_returns_boolean(monkeypatch):
    from executor.appium_driver import AppiumDriver

    class _Appium:
        def __init__(self, result):
            self.result = result
            self.calls = []

        def execute_script(self, name, payload):
            self.calls.append((name, payload))
            return self.result

    class _Element:
        id = "native-element-id"

    driver = AppiumDriver(device={"udid": "android-1", "platform": "android"})
    fake = _Appium(True)
    driver.driver = fake
    assert driver.scroll_in_element(_Element(), "down", 0.5) is True
    assert fake.calls == [("mobile: scrollGesture", {"elementId": "native-element-id", "direction": "down", "percent": 0.5})]

    fake.result = False
    assert driver.scroll_in_element(_Element(), "up", 0.3) is False


def test_appium_get_element_rect_returns_w3c_rect():
    from executor.appium_driver import AppiumDriver

    class _Element:
        rect = {"x": 10, "y": 20, "width": 300, "height": 400}

    driver = AppiumDriver(device={"udid": "android-1", "platform": "android"})
    driver.driver = object()  # 仅需 _ensure 不抛（实际上会抛，因为 driver 非 None）
    rect = driver.get_element_rect(_Element())
    assert rect == {"x": 10, "y": 20, "width": 300, "height": 400}


def test_mock_get_element_rect_defaults_to_window_size():
    from executor.driver import MockDriver, MockElement

    driver = MockDriver()
    el = MockElement("container")
    assert driver.get_element_rect(el) == {"x": 0, "y": 0, "width": 1000, "height": 2000}


def test_mock_scroll_in_element_records_and_returns_can_continue():
    from executor.driver import MockDriver, MockElement

    driver = MockDriver()
    el = MockElement("list")
    assert driver.scroll_in_element(el, "up", 0.3) is True
    assert driver.element_scrolls == [("list", "up", 0.3, True)]
    assert driver.swipes == [("up", 500)]

    driver.scroll_can_continue = False
    assert driver.scroll_in_element(el, "down", 0.5) is False
    assert driver.element_scrolls[-1] == ("list", "down", 0.5, False)


async def test_find_text_click_stale_exhausted_raises_hard_failure():
    from executor import ElementStaleRetryExhausted
    from executor.driver import StaleObjectException

    driver = _ListScrollDriver(_FULL_SCREEN)
    driver.set_screen([{"id": "t1", "text": "系统时间", "bounds": {"x": 100, "y": 500, "width": 200, "height": 50}}])

    def _always_stale(el):
        raise StaleObjectException("元素已失效")

    driver.click = _always_stale
    action, context = _run_find_text(driver, _find_text_params())
    with pytest.raises(ElementStaleRetryExhausted):
        await action.execute(driver, context, _find_text_params())
    # 不因点击 stale 耗尽而无休止滑动
    assert driver.up_count == 0
    assert driver.down_count == 0


async def test_step_result_missing_step_order_raises_protocol_error():
    """Step 6.2：快照缺少 step_order 时必须明确报协议错误，不能静默发送 None。"""
    case = _make_case(
        steps=[{"action": "input", "element_id": 1, "params": {"value": "admin"}}],
    )
    with pytest.raises(ValueError, match="缺少有效 step_order"):
        await _run_and_capture(case)


async def test_step_result_invalid_step_order_raises_protocol_error():
    """Step 6.2：step_order 为 0/负数/非数字时明确报协议错误。"""
    for bad_order in (0, -1, "abc"):
        case = _make_case(
            steps=[
                {
                    "step_order": bad_order,
                    "action": "input",
                    "element_id": 1,
                    "params": {"value": "admin"},
                },
            ],
        )
        with pytest.raises(ValueError, match="step_order 非法"):
            await _run_and_capture(case)


async def test_step_result_accepts_string_step_order():
    """step_order 为数字字符串时可正常转换为 int 并上报。"""
    case = _make_case(
        steps=[{"order": "2", "action": "input", "element_id": 1, "params": {"value": "admin"}}],
    )
    status, sent = await _run_and_capture(case)
    assert status == "passed"
    step_msgs = [m for m in sent if m["type"] == "step_result"]
    assert step_msgs[0]["step_order"] == 2


# ---------- Step 7.1：终态聚合表驱动测试（executor.status.aggregate_statuses） ----------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "skipped"),
        (["passed"], "passed"),
        (["skipped"], "skipped"),
        (["passed", "skipped"], "skipped"),
        (["error", "failed"], "error"),
        (["failed", "error"], "error"),
        (["failed", "stopped"], "failed"),
        (["stopped", "failed"], "failed"),
        (["stopped", "skipped"], "stopped"),
        (["skipped", "stopped"], "stopped"),
        (["passed", "passed"], "passed"),
        (["error"], "error"),
        (["failed"], "failed"),
        (["stopped"], "stopped"),
        # 未知状态不允许静默视为 passed（fail-closed）
        (["running"], "error"),
        (["pending"], "error"),
        (["passed", "running"], "error"),
        # cancelled 在聚合边界归一为 stopped
        (["cancelled"], "stopped"),
        (["passed", "cancelled"], "stopped"),
    ],
)
def test_aggregate_statuses_priority_table(statuses, expected):
    assert aggregate_statuses(statuses) == expected


@pytest.mark.parametrize(
    "statuses",
    [
        ["error", "failed", "stopped", "skipped", "passed"],
        ["passed", "skipped", "stopped", "failed", "error"],
        ["skipped", "error", "passed", "stopped", "failed"],
        ["stopped", "passed", "failed", "skipped", "error"],
        ["failed", "stopped", "error", "passed", "skipped"],
    ],
)
def test_aggregate_statuses_order_invariant(statuses):
    """多套件结果的任意排列得到相同终态。"""
    assert aggregate_statuses(statuses) == "error"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["failed", "error", "stopped"], "error"),
        (["stopped", "failed", "skipped"], "failed"),
        (["skipped", "stopped", "passed"], "stopped"),
        (["passed", "skipped", "failed"], "failed"),
    ],
)
def test_aggregate_statuses_partial_permutations(statuses, expected):
    import itertools

    for perm in itertools.permutations(statuses):
        assert aggregate_statuses(list(perm)) == expected
