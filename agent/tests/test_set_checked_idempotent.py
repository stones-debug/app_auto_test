import time

import pytest

from executor.actions import SetCheckedAction
from executor.context import ExecutionContext
from executor.driver import DriverError, MockDriver, StaleObjectException, StopRequested


def _context(driver, stop=None):
    return ExecutionContext(
        driver,
        {
            "elements_snapshot": {
                "1": {"locator_type": "id", "locator_value": "agree"},
            }
        },
        should_stop=stop,
    )


def _driver(checked=False):
    driver = MockDriver()
    driver.set_screen([
        {
            "id": "agree",
            "class_name": "android.widget.CheckBox",
            "checked": checked,
        }
    ])
    driver.click_count = 0
    return driver


class RecordingDriver(MockDriver):
    def __init__(self, *, checked=False):
        super().__init__()
        self.set_screen([
            {
                "id": "agree",
                "class_name": "android.widget.CheckBox",
                "checked": checked,
            }
        ])
        self.click_count = 0

    def click(self, element):
        self.click_count += 1
        super().click(element)


@pytest.mark.asyncio
async def test_already_correct_sends_zero_clicks():
    driver = RecordingDriver(checked=True)

    result = await SetCheckedAction().execute(
        driver, _context(driver), {"element_id": 1, "checked": True}
    )

    assert result == {"status": "passed", "expected": "checked", "changed": False}
    assert driver.click_count == 0


@pytest.mark.asyncio
async def test_normal_transition_sends_one_click_and_verifies_state():
    driver = RecordingDriver(checked=False)

    result = await SetCheckedAction().execute(
        driver, _context(driver), {"element_id": 1, "checked": True}
    )

    assert result["status"] == "passed"
    assert result["changed"] is True
    assert driver.click_count == 1


class ClickAppliedThenStaleDriver(RecordingDriver):
    def click(self, element):
        super().click(element)
        raise StaleObjectException("element became stale after click")


@pytest.mark.asyncio
async def test_click_applied_then_stale_is_confirmed_without_second_click():
    driver = ClickAppliedThenStaleDriver(checked=False)

    result = await SetCheckedAction().execute(
        driver, _context(driver), {"element_id": 1, "checked": True}
    )

    assert result["status"] == "passed"
    assert result["changed"] is True
    assert driver.click_count == 1


class ReadTimeout(Exception):
    pass


class ClickAppliedThenReadTimeoutDriver(RecordingDriver):
    def __init__(self):
        super().__init__(checked=False)
        self._read_timeout_once = False

    def click(self, element):
        super().click(element)
        self._read_timeout_once = True

    def is_checked(self, element):
        if self._read_timeout_once:
            self._read_timeout_once = False
            raise ReadTimeout("HTTP Read timed out")
        return super().is_checked(element)


@pytest.mark.asyncio
async def test_click_applied_then_read_timeout_is_confirmed_without_second_click():
    driver = ClickAppliedThenReadTimeoutDriver()

    result = await SetCheckedAction().execute(
        driver, _context(driver), {"element_id": 1, "checked": True}
    )

    assert result["status"] == "passed"
    assert result["changed"] is True
    assert driver.click_count == 1


class DelayedConvergenceDriver(RecordingDriver):
    def __init__(self, delayed_reads=2):
        super().__init__(checked=False)
        self.delayed_reads = delayed_reads

    def click(self, element):
        self.click_count += 1
        # The device applies the change asynchronously; retain the old state
        # for a few observations before exposing the target state.
        element._attributes["pending_checked"] = True

    def is_checked(self, element):
        pending = int(element._attributes.get("pending_reads", self.delayed_reads))
        if element._attributes.get("pending_checked") and pending > 0:
            element._attributes["pending_reads"] = pending - 1
            return False
        if element._attributes.get("pending_checked"):
            element._attributes["checked"] = True
        return super().is_checked(element)


@pytest.mark.asyncio
async def test_delayed_ui_convergence_is_polled_without_second_click():
    driver = DelayedConvergenceDriver(delayed_reads=2)

    result = await SetCheckedAction().execute(
        driver,
        _context(driver),
        {"element_id": 1, "checked": True, "wait_timeout": 1},
    )

    assert result["status"] == "passed"
    assert result["changed"] is True
    assert driver.click_count == 1


class NonConvergingDriver(RecordingDriver):
    def click(self, element):
        self.click_count += 1
    # The click is delivered but the control remains unchecked.


class ThirdAttemptConvergenceDriver(RecordingDriver):
    def click(self, element):
        self.click_count += 1
        if self.click_count >= 3:
            MockDriver.click(self, element)


@pytest.mark.asyncio
async def test_non_converging_control_gets_at_most_three_clean_clicks():
    driver = NonConvergingDriver(checked=False)

    with pytest.raises(DriverError, match="未收敛"):
        await SetCheckedAction().execute(
            driver,
            _context(driver),
            {"element_id": 1, "checked": True, "wait_timeout": 3.5},
        )

    assert driver.click_count == 3


@pytest.mark.asyncio
async def test_third_clean_click_can_finish_transition():
    driver = ThirdAttemptConvergenceDriver(checked=False)

    result = await SetCheckedAction().execute(
        driver,
        _context(driver),
        {"element_id": 1, "checked": True, "wait_timeout": 3.5},
    )

    assert result["status"] == "passed"
    assert result["changed"] is True
    assert driver.click_count == 3


class SlowInitialReadDriver(RecordingDriver):
    def __init__(self):
        super().__init__(checked=False)
        self._slow_once = True

    def is_checked(self, element):
        if self._slow_once:
            self._slow_once = False
            time.sleep(0.08)
        return super().is_checked(element)


@pytest.mark.asyncio
async def test_expired_deadline_after_initial_read_does_not_send_click():
    driver = SlowInitialReadDriver()

    with pytest.raises(DriverError, match="未发送点击"):
        await SetCheckedAction().execute(
            driver,
            _context(driver),
            {"element_id": 1, "checked": True, "wait_timeout": 0.05},
        )
    assert driver.click_count == 0


@pytest.mark.asyncio
async def test_no_convergence_fails_without_compensating_click():
    driver = NonConvergingDriver(checked=False)

    with pytest.raises(DriverError, match="未收敛"):
        await SetCheckedAction().execute(
            driver,
            _context(driver),
            {"element_id": 1, "checked": True, "wait_timeout": 0.25},
        )
    assert driver.click_count == 1


class InitialReadStaleDriver(RecordingDriver):
    def __init__(self):
        super().__init__(checked=False)
        self._stale_once = True

    def is_checked(self, element):
        if self._stale_once:
            self._stale_once = False
            raise StaleObjectException("initial read stale")
        return super().is_checked(element)


@pytest.mark.asyncio
async def test_initial_read_can_retry_but_click_remains_single():
    driver = InitialReadStaleDriver()

    result = await SetCheckedAction().execute(
        driver, _context(driver), {"element_id": 1, "checked": True}
    )

    assert result["status"] == "passed"
    assert result["changed"] is True
    assert driver.click_count == 1


class FatalSessionError(Exception):
    pass


class SessionDiesAfterClickDriver(RecordingDriver):
    def __init__(self):
        super().__init__(checked=False)
        self.session_dead = False

    def click(self, element):
        self.click_count += 1
        self.session_dead = True
        raise FatalSessionError("invalid session id")

    def is_checked(self, element):
        if self.session_dead:
            raise FatalSessionError("invalid session id")
        return super().is_checked(element)


@pytest.mark.asyncio
async def test_fatal_session_error_is_not_swallowed():
    driver = SessionDiesAfterClickDriver()

    with pytest.raises(FatalSessionError, match="invalid session id"):
        await SetCheckedAction().execute(
            driver, _context(driver), {"element_id": 1, "checked": True}
        )
    assert driver.click_count == 1


@pytest.mark.asyncio
async def test_stop_during_convergence_stops_without_second_click():
    driver = NonConvergingDriver(checked=False)
    calls = 0

    def stop():
        nonlocal calls
        calls += 1
        # with_stale_retry checks once before the initial read, then the action
        # checks again before sending the one click; stop on the next poll.
        return calls >= 3

    with pytest.raises(StopRequested):
        await SetCheckedAction().execute(
            driver,
            _context(driver, stop=stop),
            {"element_id": 1, "checked": True, "wait_timeout": 1},
        )
    assert driver.click_count == 1
