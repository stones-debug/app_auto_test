import asyncio

import pytest

from executor import ExecutionContext, MockDriver, StopRequested
from executor.actions import WaitElementStableAction
from executor.driver import ElementNotFound, StaleObjectException


def _context(driver, *, should_stop=None):
    case = {
        "elements_snapshot": {"1": {"locator_type": "id", "locator_value": "target"}},
    }
    return ExecutionContext(driver, case, should_stop=should_stop)


@pytest.mark.asyncio
async def test_wait_element_stable_relocates_each_sample_and_passes_after_stable_reads(monkeypatch):
    driver = MockDriver()
    driver.set_screen([
        {
            "id": "target",
            "displayed": True,
            "enabled": True,
            "bounds": {"x": 10, "y": 20, "width": 100, "height": 40},
        }
    ])
    context = _context(driver)
    find_calls = 0
    original_find = context.find_element

    def find_element(*args, **kwargs):
        nonlocal find_calls
        find_calls += 1
        return original_find(*args, **kwargs)

    context.find_element = find_element
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    result = await WaitElementStableAction().execute(
        driver,
        context,
        {
            "element_id": 1,
            "wait_timeout": 1,
            "stable_duration_ms": 0,
            "stable_reads": 3,
        },
    )

    assert result["status"] == "passed"
    assert find_calls >= 3


@pytest.mark.asyncio
async def test_wait_element_stable_resets_window_when_rect_changes(monkeypatch):
    driver = MockDriver()
    driver.set_screen([
        {
            "id": "target",
            "displayed": True,
            "enabled": True,
            "bounds": {"x": 10, "y": 20, "width": 100, "height": 40},
        }
    ])
    context = _context(driver)
    rects = iter([
        {"x": 10, "y": 20, "width": 100, "height": 40},
        {"x": 12, "y": 20, "width": 100, "height": 40},
        {"x": 10, "y": 20, "width": 100, "height": 40},
        {"x": 10, "y": 20, "width": 100, "height": 40},
    ])
    monkeypatch.setattr(driver, "get_element_rect", lambda _element: next(rects))
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    result = await WaitElementStableAction().execute(
        driver,
        context,
        {"element_id": 1, "wait_timeout": 1, "stable_duration_ms": 0, "stable_reads": 2},
    )

    assert result["status"] == "passed"
    assert result["attempts"] == 4


@pytest.mark.asyncio
async def test_wait_element_stable_timeout_includes_diagnostics_without_source():
    driver = MockDriver()
    driver.set_screen([{"id": "target", "displayed": "unknown", "enabled": True}])
    context = _context(driver)

    with pytest.raises(ElementNotFound) as caught:
        await WaitElementStableAction().execute(
            driver,
            context,
            {
                "element_id": 1,
                "wait_timeout": 0,
                "stable_duration_ms": 500,
                "stable_reads": 2,
            },
        )

    message = str(caught.value)
    assert "element_id=1" in message
    assert "stable_duration_ms" in message
    assert "last_state" in message
    assert "last_rect" in message
    assert "unknown" not in message  # only the normalized reason is retained
    assert "page source" not in message.lower()


@pytest.mark.asyncio
async def test_wait_element_stable_honors_stop_during_wait():
    driver = MockDriver()
    driver.set_screen([
        {
            "id": "target",
            "displayed": True,
            "enabled": True,
            "bounds": {"x": 10, "y": 20, "width": 100, "height": 40},
        }
    ])
    context = _context(driver, should_stop=lambda: True)

    with pytest.raises(StopRequested):
        await WaitElementStableAction().execute(
            driver,
            context,
            {"element_id": 1, "wait_timeout": 1},
        )


@pytest.mark.asyncio
async def test_wait_element_stable_prefers_appium_element_state_methods(monkeypatch):
    driver = MockDriver()
    driver.set_screen([{"id": "target", "bounds": {"x": 1, "y": 2, "width": 30, "height": 20}}])
    element = driver.find_element("id", "target")
    element.is_displayed = lambda: True
    element.is_enabled = lambda: True
    monkeypatch.setattr(driver, "get_attribute", lambda *_args: pytest.fail("should not fallback"))

    result = await WaitElementStableAction().execute(
        driver,
        _context(driver),
        {"element_id": 1, "stable_reads": 1, "stable_duration_ms": 0},
    )

    assert result["status"] == "passed"
    assert result["attempts"] == 1


@pytest.mark.asyncio
async def test_wait_element_stable_falls_back_to_attributes_when_methods_are_unavailable():
    driver = MockDriver()
    driver.set_screen([
        {
            "id": "target",
            "displayed": "true",
            "enabled": "true",
            "bounds": {"x": 1, "y": 2, "width": 30, "height": 20},
        }
    ])

    result = await WaitElementStableAction().execute(
        driver,
        _context(driver),
        {"element_id": 1, "stable_reads": 1, "stable_duration_ms": 0},
    )

    assert result["status"] == "passed"


@pytest.mark.asyncio
async def test_wait_element_stable_ignores_optional_visibility_and_enabled_states():
    driver = MockDriver()
    driver.set_screen([
        {
            "id": "target",
            "displayed": "unknown",
            "enabled": False,
            "bounds": {"x": 1, "y": 2, "width": 30, "height": 20},
        }
    ])

    result = await WaitElementStableAction().execute(
        driver,
        _context(driver),
        {
            "element_id": 1,
            "stable_reads": 1,
            "stable_duration_ms": 0,
            "require_displayed": False,
            "require_enabled": False,
        },
    )

    assert result["status"] == "passed"


@pytest.mark.asyncio
async def test_wait_element_stable_retries_missing_element_then_succeeds():
    class AppearingDriver(MockDriver):
        def __init__(self):
            super().__init__()
            self.find_calls = 0

        def find_element(self, locator_type, locator_value, wait_timeout=10):
            self.find_calls += 1
            if self.find_calls == 1:
                raise ElementNotFound("not ready")
            return super().find_element(locator_type, locator_value, wait_timeout)

    driver = AppearingDriver()
    driver.set_screen([
        {
            "id": "target",
            "displayed": True,
            "enabled": True,
            "bounds": {"x": 1, "y": 2, "width": 30, "height": 20},
        }
    ])

    result = await WaitElementStableAction().execute(
        driver,
        _context(driver),
        {"element_id": 1, "stable_reads": 1, "stable_duration_ms": 0},
    )

    assert result["status"] == "passed"
    assert result["attempts"] == 2


@pytest.mark.asyncio
async def test_wait_element_stable_recovers_from_stale_observation():
    class FlakyRectDriver(MockDriver):
        def __init__(self):
            super().__init__()
            self.rect_calls = 0

        def get_element_rect(self, element):
            self.rect_calls += 1
            if self.rect_calls == 1:
                raise StaleObjectException("stale element reference")
            return super().get_element_rect(element)

    driver = FlakyRectDriver()
    driver.set_screen([
        {
            "id": "target",
            "displayed": True,
            "enabled": True,
            "bounds": {"x": 1, "y": 2, "width": 30, "height": 20},
        }
    ])

    result = await WaitElementStableAction().execute(
        driver,
        _context(driver),
        {"element_id": 1, "stable_reads": 1, "stable_duration_ms": 0},
    )

    assert result["status"] == "passed"
    assert result["attempts"] == 2


@pytest.mark.asyncio
async def test_wait_element_stable_stable_reads_one_duration_zero_is_immediate():
    driver = MockDriver()
    driver.set_screen([
        {
            "id": "target",
            "displayed": True,
            "enabled": True,
            "bounds": {"x": 1, "y": 2, "width": 30, "height": 20},
        }
    ])

    result = await WaitElementStableAction().execute(
        driver,
        _context(driver),
        {"element_id": 1, "stable_reads": 1, "stable_duration_ms": 0},
    )

    assert result == {"status": "passed", "attempts": 1}
