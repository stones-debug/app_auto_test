"""断言绝对截止时间贯穿驱动定位链路的回归测试。"""

import asyncio
import threading
import time

from executor import ExecutionContext, MockDriver, TestRunner
from executor.assertion_wait import verify_with_wait
from executor.assertions import TextEqualsAssertion, TextNotEqualsAssertion


class RecordingDriver(MockDriver):
    def __init__(self) -> None:
        super().__init__(initial_state={"username": "admin"})
        self.wait_timeouts: list[float | None] = []

    def find_element(self, locator_type: str, locator_value: str, wait_timeout=None):
        self.wait_timeouts.append(wait_timeout)
        return super().find_element(locator_type, locator_value, wait_timeout)


def _context(driver) -> ExecutionContext:
    return ExecutionContext(
        driver,
        {"elements_snapshot": {"1": {"locator_type": "id", "locator_value": "username"}}},
    )


async def test_assertion_passes_remaining_budget_to_element_lookup():
    driver = RecordingDriver()
    context = _context(driver)
    deadline = time.monotonic() + 0.5

    result = await TextEqualsAssertion().verify(
        driver,
        context,
        {"element_id": 1, "expected": "admin"},
        deadline=deadline,
    )

    assert result["status"] == "passed"
    assert driver.wait_timeouts
    assert 0 < driver.wait_timeouts[0] <= 0.5


async def test_text_not_equals_strict_comparison_and_trim_are_symmetric():
    driver = MockDriver(initial_state={"username": " admin "})
    context = _context(driver)

    result = await TextNotEqualsAssertion().verify(
        driver, context, {"element_id": 1, "expected": "admin", "trim": True}
    )
    assert result == {"status": "failed", "expected": "admin", "actual": "admin"}

    result = await TextNotEqualsAssertion().verify(
        driver, context, {"element_id": 1, "expected": "Admin", "trim": True}
    )
    assert result == {"status": "passed", "expected": "Admin", "actual": "admin"}


async def test_text_not_equals_does_not_pass_when_element_lookup_fails():
    from executor import ElementNotFound

    class MissingDriver(MockDriver):
        def find_element(self, locator_type, locator_value, wait_timeout=10):
            raise ElementNotFound(f"元素不存在: {locator_value}")

    driver = MissingDriver(initial_state={})
    context = _context(driver)

    try:
        await TextNotEqualsAssertion().verify(
            driver, context, {"element_id": 1, "expected": "anything"}
        )
    except ElementNotFound:
        pass
    else:
        raise AssertionError("元素不存在时文本不等于不能错误通过")


async def test_text_not_equals_equal_text_expires_as_failed():
    driver = MockDriver(initial_state={"username": "same"})
    context = _context(driver)
    result = await verify_with_wait(
        lambda deadline: TextNotEqualsAssertion().verify(
            driver, context, {"element_id": 1, "expected": "same"}, deadline=deadline
        ),
        max_wait_seconds=0,
    )
    assert result["status"] == "failed"
    assert result["actual"] == "same"


async def test_zero_wait_assertion_performs_one_immediate_lookup():
    driver = RecordingDriver()
    context = _context(driver)

    async def verify(deadline: float):
        return await TextEqualsAssertion().verify(
            driver,
            context,
            {"element_id": 1, "expected": "admin"},
            deadline=deadline,
        )

    result = await verify_with_wait(verify, max_wait_seconds=0)

    assert result["status"] == "passed"
    assert driver.wait_timeouts == [0.0]


async def test_zero_wait_assertion_waits_for_immediate_attempt_to_finish():
    started = threading.Event()

    def blocking_verify(_deadline: float):
        started.set()
        time.sleep(0.1)
        return {"status": "passed", "actual": "ready"}

    result = await verify_with_wait(
        lambda deadline: asyncio.to_thread(blocking_verify, deadline),
        max_wait_seconds=0,
    )

    assert started.is_set()
    assert result["status"] == "passed"
    assert result["attempt_count"] == 1


async def test_verify_with_wait_does_not_interrupt_driver_at_deadline():
    release = threading.Event()

    def blocking_verify(_deadline: float):
        while not release.wait(0.01):
            pass
        return {"status": "failed", "actual": ""}

    started = time.monotonic()
    result = await verify_with_wait(
        lambda deadline: asyncio.to_thread(blocking_verify, deadline),
        max_wait_seconds=0.05,
        on_interrupt=release.set,
    )

    assert time.monotonic() - started < 0.5
    assert result["status"] == "failed"
    assert result["attempt_count"] == 1
    assert "最大等待时间" in result["error_message"]
    assert not release.is_set()
    release.set()


async def test_verify_with_wait_preserves_completed_mismatch_after_retry_interval():
    attempts = 0

    def verify(_deadline: float):
        nonlocal attempts
        attempts += 1
        return {"status": "failed", "expected": "98", "actual": "98.0"}

    result = await verify_with_wait(
        verify,
        max_wait_seconds=0.06,
        interval_seconds=0.02,
    )

    assert attempts >= 2
    assert result["status"] == "failed"
    assert result["expected"] == "98"
    assert result["actual"] == "98.0"
    assert "最大等待时间" not in (result.get("error_message") or "")


async def test_runner_v3_preserves_text_mismatch_when_wait_expires(monkeypatch):
    import executor.test_runner as runner_module

    original_verify_with_wait = runner_module.verify_with_wait

    async def fast_verify_with_wait(verify, **kwargs):
        return await original_verify_with_wait(verify, interval_seconds=0.01, **kwargs)

    monkeypatch.setattr(runner_module, "verify_with_wait", fast_verify_with_wait)
    sent: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    runner = TestRunner(MockDriver(initial_state={"username": "98.0"}), send, execution_id=100)
    case = {
        "execution_case_id": 2001,
        "flow_snapshot": [{
            "execution_node_id": 3001, "kind": "assertion", "phase": "case_main", "order": 1,
            "type": "text_equals", "element_id": 1, "params": {"expected": "98"},
            "max_wait_seconds": 0.04,
        }],
        "elements_snapshot": {"1": {"locator_type": "id", "locator_value": "username"}},
    }

    assert await runner.run_case(case) == "failed"
    node_result = next(item for item in sent if item["type"] == "node_result")
    assert node_result["status"] == "failed"
    assert node_result["expected_value"] == "98"
    assert node_result["actual_value"] == "98.0"
    assert "断言数据不一致" in node_result["error_message"]
    assert "最大等待时间" not in node_result["error_message"]


class BlockingLookupDriver(RecordingDriver):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.interrupted = threading.Event()

    def find_element(self, locator_type: str, locator_value: str, wait_timeout=None):
        self.wait_timeouts.append(wait_timeout)
        self.release.wait(5)
        return super(RecordingDriver, self).find_element(locator_type, locator_value, wait_timeout)

    def interrupt(self) -> None:
        self.interrupted.set()
        self.release.set()


async def test_runner_v3_assertion_timeout_does_not_interrupt_driver():
    driver = BlockingLookupDriver()
    sent: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    runner = TestRunner(driver, send, execution_id=100)
    case = {
        "execution_case_id": 2001,
        "flow_snapshot": [
            {
                "execution_node_id": 3001,
                "kind": "assertion",
                "phase": "case_main",
                "order": 1,
                "type": "text_equals",
                "element_id": 1,
                "params": {"expected": "admin"},
                "max_wait_seconds": 0.05,
            }
        ],
        "elements_snapshot": {"1": {"locator_type": "id", "locator_value": "username"}},
    }

    started = time.monotonic()
    status = await runner.run_case(case)
    await asyncio.sleep(0.05)

    assert time.monotonic() - started < 0.5
    assert status == "failed"
    assert not driver.interrupted.is_set()
    node_result = next(item for item in sent if item["type"] == "node_result")
    assert node_result["status"] == "failed"
    driver.release.set()


async def test_runner_v3_node_error_is_not_downgraded_to_failed():
    sent: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    runner = TestRunner(MockDriver(), send, execution_id=100)
    case = {
        "execution_case_id": 2001,
        "flow_snapshot": [
            {
                "execution_node_id": 3001,
                "kind": "action",
                "phase": "case_main",
                "order": 1,
                "action": "unknown_action",
                "params": {},
            }
        ],
        "elements_snapshot": {},
    }

    status = await runner.run_case(case)

    assert status == "error"
    node_result = next(item for item in sent if item["type"] == "node_result")
    assert node_result["status"] == "error"


async def test_runner_v3_assertion_failure_reasons_are_explicit():
    async def send(payload: dict) -> None:
        sent.append(payload)

    # 普通值不匹配：最终节点失败应说明期望和实际值。
    sent: list[dict] = []
    runner = TestRunner(MockDriver(initial_state={"username": "admin"}), send, execution_id=100)
    mismatch_case = {
        "execution_case_id": 2001,
        "flow_snapshot": [{
            "execution_node_id": 3001, "kind": "assertion", "phase": "case_main", "order": 1,
            "type": "text_equals", "element_id": 1, "params": {"expected": "wrong"},
            "max_wait_seconds": 0,
        }],
        "elements_snapshot": {"1": {"locator_type": "id", "locator_value": "username"}},
    }
    assert await runner.run_case(mismatch_case) == "failed"
    mismatch = next(item for item in sent if item["type"] == "node_result")
    assert mismatch["status"] == "failed"
    assert "断言数据不一致" in mismatch["error_message"]
    assert "wrong" in mismatch["error_message"] and "admin" in mismatch["error_message"]

    # 元素未找到：定位异常使用专门前缀；不是数据不一致。
    sent.clear()
    missing_case = {
        "execution_case_id": 2001,
        "flow_snapshot": [{
            "execution_node_id": 3002, "kind": "assertion", "phase": "case_main", "order": 1,
            "type": "text_equals", "element_id": 99, "params": {"expected": "admin"},
            "max_wait_seconds": 0,
        }],
        "elements_snapshot": {},
    }
    assert await runner.run_case(missing_case) == "failed"
    missing = next(item for item in sent if item["type"] == "node_result")
    assert missing["status"] == "failed"
    assert missing["error_message"].startswith("断言元素未找到：")

    # not_exists 的预期语义保持 passed，不能被失败归一化污染。
    sent.clear()
    not_exists_case = {
        "execution_case_id": 2001,
        "flow_snapshot": [{
            "execution_node_id": 3003, "kind": "assertion", "phase": "case_main", "order": 1,
            "type": "element_exists", "element_id": 99, "params": {"expected": "not_exists"},
            "max_wait_seconds": 0,
        }],
        "elements_snapshot": {},
    }
    assert await runner.run_case(not_exists_case) == "passed"
    assert next(item for item in sent if item["type"] == "node_result")["error_message"] is None
