import pytest

from executor.actions import SetCheckedAction
from executor.appium_driver import AppiumDriver
from executor.context import ExecutionContext
from executor.driver import MockDriver, StaleObjectException


class _UnsupportedGesture(Exception):
    pass


class _GestureSession:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def execute_script(self, name, payload):
        self.calls.append((name, payload))
        if self.error is not None:
            raise self.error


class _Element:
    id = "native-remote-7"

    def __init__(self):
        self.click_calls = 0

    def click(self):
        self.click_calls += 1


def test_appium_checkable_click_uses_exact_remote_element_id():
    session = _GestureSession()
    driver = AppiumDriver(device={"platform": "android"})
    driver.driver = session
    element = _Element()

    driver.click_checkable(element)

    assert session.calls == [("mobile: clickGesture", {"elementId": "native-remote-7"})]
    assert element.click_calls == 0


def test_appium_checkable_click_falls_back_only_when_explicitly_unsupported():
    session = _GestureSession(_UnsupportedGesture("mobile: clickGesture is not supported"))
    driver = AppiumDriver(device={"platform": "android"})
    driver.driver = session
    element = _Element()

    driver.click_checkable(element)

    assert len(session.calls) == 1
    assert element.click_calls == 1


def test_appium_checkable_click_does_not_fallback_on_ambiguous_error():
    session = _GestureSession(TimeoutError("read timed out"))
    driver = AppiumDriver(device={"platform": "android"})
    driver.driver = session
    element = _Element()

    with pytest.raises(TimeoutError):
        driver.click_checkable(element)
    assert element.click_calls == 0


def _context(driver):
    return ExecutionContext(
        driver,
        {"elements_snapshot": {"1": {"locator_type": "id", "locator_value": "agree"}}},
    )


class _NoEffectDriver(MockDriver):
    def __init__(self):
        super().__init__()
        self.set_screen([{"id": "agree", "class_name": "android.widget.CheckBox", "checked": False}])
        self.click_count = 0

    def click(self, element):
        self.click_count += 1
        # Simulate a clean Android click that did not change the backing state.
        self._assert_fresh(element)


class _SecondClickConvergesDriver(_NoEffectDriver):
    def click(self, element):
        self.click_count += 1
        self._assert_fresh(element)
        if self.click_count == 2:
            element._attributes["checked"] = True


@pytest.mark.asyncio
async def test_clean_non_converging_click_retries_once_and_reports_changed():
    driver = _SecondClickConvergesDriver()
    result = await SetCheckedAction().execute(
        driver, _context(driver), {"element_id": 1, "checked": True, "wait_timeout": 2.0}
    )
    assert result == {"status": "passed", "expected": "checked", "changed": True}
    assert driver.click_count == 2


@pytest.mark.asyncio
async def test_clean_non_converging_click_gets_one_controlled_retry():
    driver = _NoEffectDriver()
    with pytest.raises(Exception, match="未收敛"):
        await SetCheckedAction().execute(
            driver, _context(driver), {"element_id": 1, "checked": True, "wait_timeout": 2.0}
        )
    assert driver.click_count == 2


@pytest.mark.asyncio
async def test_insufficient_remaining_budget_skips_second_click():
    driver = _NoEffectDriver()
    with pytest.raises(Exception, match="未收敛"):
        await SetCheckedAction().execute(
            driver, _context(driver), {"element_id": 1, "checked": True, "wait_timeout": 1.2}
        )
    assert driver.click_count == 1


class _UnstableObservationDriver(_NoEffectDriver):
    def __init__(self):
        super().__init__()
        self._reads = 0

    def is_checked(self, element):
        self._assert_fresh(element)
        self._reads += 1
        # Initial and pre-click reads are false; after the click, alternate
        # false/unknown so the two-read stability gate can never be met.
        return False if self._reads <= 3 or self._reads % 2 else None


@pytest.mark.asyncio
async def test_unknown_or_unstable_observation_never_retries():
    driver = _UnstableObservationDriver()
    with pytest.raises(Exception, match="未收敛"):
        await SetCheckedAction().execute(
            driver, _context(driver), {"element_id": 1, "checked": True, "wait_timeout": 2.0}
        )
    assert driver.click_count == 1


class _StaleClickDriver(_NoEffectDriver):
    def click(self, element):
        self.click_count += 1
        raise StaleObjectException("stale after click")


@pytest.mark.asyncio
async def test_ambiguous_first_click_never_gets_retry():
    driver = _StaleClickDriver()
    with pytest.raises(Exception, match="未收敛"):
        await SetCheckedAction().execute(
            driver, _context(driver), {"element_id": 1, "checked": True, "wait_timeout": 0.8}
        )
    assert driver.click_count == 1
