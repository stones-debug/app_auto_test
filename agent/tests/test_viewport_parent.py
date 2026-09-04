from typing import Any, cast

import pytest

from executor import ElementNotFound, ExecutionContext, MockDriver
from executor.driver import StaleObjectException

WINDOW = {"x": 0, "y": 0, "width": 1000, "height": 2000}


class RecordingMockDriver(MockDriver):
    def __init__(self):
        super().__init__()
        self.clicked: list[str] = []

    def click(self, element):
        self._assert_fresh(element)
        self.clicked.append(element.text or element.locator_value)


def _context(driver, params, elements=None):
    case = {
        "elements_snapshot": elements or {"1": {"locator_type": "id", "locator_value": "list"}},
    }
    return ExecutionContext(driver, case), params


async def _run(driver, params, elements=None):
    from executor.actions import ACTION_REGISTRY

    context, params = _context(driver, params, elements)
    return await ACTION_REGISTRY["swipe_in_element_find_text_click"]().execute(
        driver, context, params
    )


def _params(**overrides):
    result = {
        "element_id": 1,
        "target_text": "目标",
        "max_swipes_per_direction": 1,
        "settle_ms": 0,
    }
    result.update(overrides)
    return result


def _screen(target_y=900, *, parent_bounds=None, target_bounds=None, list_height=1400):
    parent_bounds = parent_bounds or {"x": 0, "y": 200, "width": 1000, "height": 1000}
    target_bounds = target_bounds or {"x": 200, "y": target_y, "width": 300, "height": 80}
    root = {
        "id": "parent",
        "bounds": parent_bounds,
        "children": [{
            "id": "list",
            "class_name": "android.widget.ListView",
            "bounds": {"x": 100, "y": 300, "width": 800, "height": list_height},
            "children": [{
                "id": "target",
                "text": "目标",
                "bounds": target_bounds,
            }],
        }],
    }
    return [root]


async def test_parent_viewport_clicks_inside_parent():
    driver = RecordingMockDriver()
    driver.set_screen(_screen(target_y=900))
    result = await _run(driver, _params())

    assert result["status"] == "passed"
    assert driver.clicked == ["目标"]
    assert driver.region_swipes == []


async def test_legacy_viewport_parameters_cannot_change_parent_behavior():
    driver = RecordingMockDriver()
    driver.set_screen(_screen(
        target_y=700,
        parent_bounds={"x": 0, "y": 0, "width": 1000, "height": 1800},
        target_bounds={"x": 20, "y": 700, "width": 300, "height": 80},
    ))
    result = await _run(driver, _params(viewport_mode="self", viewport_element_id=999))

    assert result["status"] == "passed"
    assert driver.clicked == ["目标"]


async def test_parent_viewport_is_used_for_swipe_region_after_target_moves():
    driver = RecordingMockDriver()
    driver.set_screen(_screen(target_y=1750))
    driver.set_scroll_callback(lambda count: _screen(target_y=900) if count == 1 else None)
    result = await _run(driver, _params())

    assert result["status"] == "passed"
    assert driver.clicked == ["目标"]
    assert driver.coordinate_swipes == [(500, 1150, 500, 850, 300)]
    assert driver.element_swipes == []
    assert driver.region_swipes == []


async def test_parent_viewport_down_swipe_uses_exact_parent_region():
    driver = RecordingMockDriver()
    parent = {"x": 0, "y": 400, "width": 1000, "height": 800}
    driver.set_screen(_screen(target_y=100, parent_bounds=parent))
    driver.set_scroll_callback(lambda count: _screen(target_y=700, parent_bounds=parent) if count == 1 else None)

    result = await _run(
        driver,
        _params(preferred_direction="down"),
    )

    assert result["status"] == "passed"
    assert driver.clicked == ["目标"]
    assert driver.element_swipes == []
    assert driver.coordinate_swipes == [(500, 680, 500, 920, 300)]
    assert driver.region_swipes == []


async def test_up_then_reverse_down_uses_direction_specific_gestures():
    driver = RecordingMockDriver()
    parent = {"x": 0, "y": 200, "width": 1000, "height": 1000}
    driver.set_screen(_screen(target_y=1600, parent_bounds=parent))
    driver.set_scroll_callback(lambda count: _screen(target_y=900, parent_bounds=parent) if count >= 2 else None)

    result = await _run(driver, _params(max_swipes_per_direction=1))

    assert result["status"] == "passed"
    assert driver.coordinate_swipes == [
        (500, 1150, 500, 850, 300),
        (500, 550, 500, 850, 300),
    ]
    assert driver.element_swipes == []
    assert driver.region_swipes == []


async def test_down_then_reverse_up_uses_direction_specific_gestures():
    driver = RecordingMockDriver()
    parent = {"x": 0, "y": 200, "width": 1000, "height": 1000}
    driver.set_screen(_screen(target_y=100, parent_bounds=parent))
    driver.set_scroll_callback(lambda count: _screen(target_y=900, parent_bounds=parent) if count >= 2 else None)

    result = await _run(driver, _params(preferred_direction="down", max_swipes_per_direction=1))

    assert result["status"] == "passed"
    assert driver.coordinate_swipes == [
        (500, 550, 500, 850, 300),
        (500, 1150, 500, 850, 300),
    ]
    assert driver.element_swipes == []
    assert driver.region_swipes == []


async def test_up_effective_percent_tracks_dynamic_list_height():
    def run_for_list_height(list_height):
        driver = RecordingMockDriver()
        parent = {"x": 0, "y": 200, "width": 1000, "height": 1000}
        driver.set_screen(_screen(target_y=1600, parent_bounds=parent, list_height=list_height))
        driver.set_scroll_callback(lambda count: _screen(
            target_y=900, parent_bounds=parent, list_height=list_height
        ) if count == 1 else None)
        return driver

    small = run_for_list_height(1000)
    await _run(small, _params())
    large = run_for_list_height(2000)
    await _run(large, _params())

    assert small.coordinate_swipes == [(500, 950, 500, 650, 300)]
    assert large.coordinate_swipes == [(500, 1300, 500, 1000, 300)]


async def test_duration_defaults_to_300_and_is_passed_to_coordinate_gesture():
    driver = RecordingMockDriver()
    parent = {"x": 0, "y": 200, "width": 1000, "height": 1000}
    driver.set_screen(_screen(target_y=1600, parent_bounds=parent))
    driver.set_scroll_callback(lambda count: _screen(target_y=900, parent_bounds=parent) if count == 1 else None)

    await _run(driver, _params())
    assert driver.coordinate_swipes == [(500, 1150, 500, 850, 300)]

    driver = RecordingMockDriver()
    driver.set_screen(_screen(target_y=1600, parent_bounds=parent))
    driver.set_scroll_callback(lambda count: _screen(target_y=900, parent_bounds=parent) if count == 1 else None)
    await _run(driver, _params(duration_ms=600))
    assert driver.coordinate_swipes == [(500, 1150, 500, 850, 600)]


async def test_duration_changes_only_w3c_duration_not_coordinates():
    parent = {"x": 0, "y": 200, "width": 1000, "height": 1000}

    def make_driver():
        driver = RecordingMockDriver()
        driver.set_screen(_screen(target_y=1600, parent_bounds=parent))
        driver.set_scroll_callback(
            lambda count: _screen(target_y=900, parent_bounds=parent) if count == 1 else None
        )
        return driver

    short = make_driver()
    long = make_driver()
    await _run(short, _params(duration_ms=300))
    await _run(long, _params(duration_ms=3000))

    assert short.coordinate_swipes[0][:4] == long.coordinate_swipes[0][:4]
    assert short.coordinate_swipes[0][4] == 300
    assert long.coordinate_swipes[0][4] == 3000


async def test_swipe_distance_is_clamped_to_small_visible_region_without_overflow():
    driver = RecordingMockDriver()
    parent = {"x": 50, "y": 100, "width": 20, "height": 30}
    driver.set_screen(_screen(
        target_y=10,
        parent_bounds=parent,
        target_bounds={"x": 55, "y": 10, "width": 10, "height": 5},
        list_height=30,
    ))
    driver.set_scroll_callback(lambda count: _screen(
        target_y=110,
        parent_bounds=parent,
        target_bounds={"x": 55, "y": 110, "width": 10, "height": 5},
        list_height=30,
    ) if count == 1 else None)

    await _run(driver, _params(percent=10.0, preferred_direction="down"))
    start_x, start_y, end_x, end_y, duration = driver.coordinate_swipes[0]
    assert (start_x, end_x) == (60, 60)
    assert 100 <= start_y <= end_y <= 130
    assert end_y - start_y == 13  # closed pixel interval minus 8px safety margin on each side
    assert duration == 300


def test_typical_792px_region_centers_track_away_from_navigation_edge():
    from executor.actions import _vertical_swipe_points

    region = {"x": 100, "y": 200, "width": 500, "height": 792}
    start_x, start_y, end_x, end_y, actual = _vertical_swipe_points(region, "up", 600)

    assert (start_x, end_x) == (350, 350)
    assert (start_y, end_y) == (896, 296)
    assert actual == 600
    assert start_y < region["y"] + region["height"] - 8
    assert abs((start_y + end_y) / 2 - (region["y"] + region["height"] / 2)) <= 0.5

    max_start_x, max_start_y, max_end_x, max_end_y, max_actual = _vertical_swipe_points(
        region, "down", 9999
    )
    assert (max_start_x, max_end_x) == (350, 350)
    assert (max_start_y, max_end_y) == (240, 951)
    assert max_actual == 711  # safe_bottom - safe_top, using closed pixel bounds


def test_two_pixel_region_clamps_to_one_pixel_without_overflow():
    from executor.actions import _vertical_swipe_points

    points = _vertical_swipe_points({"x": 10, "y": 20, "width": 10, "height": 2}, "down", 100)

    assert points == (15, 20, 15, 21, 1)


def test_appium_coordinate_swipe_calls_driver_swipe_with_exact_duration():
    from executor.appium_driver import AppiumDriver

    class Appium:
        def __init__(self):
            self.calls = []

        def swipe(self, *args):
            self.calls.append(args)

    appium = Appium()
    driver = AppiumDriver(device={"platform": "android"})
    driver.driver = cast(Any, appium)
    driver.swipe_coordinate(10, 100, 10, 20, 3000)

    assert appium.calls == [(10, 100, 10, 20, 3000)]


def test_mock_coordinate_swipe_infers_direction_from_dominant_axis():
    driver = MockDriver()
    driver.swipe_coordinate(100, 100, 20, 95, 300)
    driver.swipe_coordinate(20, 100, 25, 180, 300)

    assert driver.swipes == [("left", 300), ("down", 300)]


async def test_duration_must_be_positive():
    driver = RecordingMockDriver()
    driver.set_screen(_screen(target_y=900))
    with pytest.raises(Exception, match="duration_ms"):
        await _run(driver, _params(duration_ms=0))


def test_appium_swipe_gestures_accept_optional_speed():
    from executor.appium_driver import AppiumDriver

    class Appium:
        def __init__(self):
            self.calls = []

        def execute_script(self, name, payload):
            self.calls.append((name, payload))

    class Element:
        id = "list-element"

    appium = Appium()
    driver = AppiumDriver(device={"platform": "android"})
    driver.driver = cast(Any, appium)
    driver.swipe_in_element(Element(), "up", 0.15, speed=1000)
    driver.swipe_in_region(0, 200, 1000, 1000, "down", 0.3, speed=1000)

    assert appium.calls == [
        ("mobile: swipeGesture", {
            "elementId": "list-element", "direction": "up", "percent": 0.15, "speed": 1000,
        }),
        ("mobile: swipeGesture", {
            "left": 0, "top": 200, "width": 1000, "height": 1000,
            "direction": "down", "percent": 0.3, "speed": 1000,
        }),
    ]


async def test_parent_viewport_is_reacquired_after_stale():
    class StaleParentDriver(RecordingMockDriver):
        def __init__(self):
            super().__init__()
            self.parent_calls = 0

        def get_parent_element(self, element, wait_timeout=10):
            self.parent_calls += 1
            if self.parent_calls == 1:
                raise StaleObjectException("parent stale")
            return super().get_parent_element(element, wait_timeout)

    driver = StaleParentDriver()
    parent = {"x": 0, "y": 400, "width": 1000, "height": 800}
    driver.set_screen(_screen(target_y=100, parent_bounds=parent))
    driver.set_scroll_callback(lambda count: _screen(target_y=700, parent_bounds=parent) if count == 1 else None)
    result = await _run(driver, _params(preferred_direction="down"))

    assert result["status"] == "passed"
    assert driver.parent_calls >= 3
    assert driver.element_swipes == []
    assert driver.coordinate_swipes == [(500, 680, 500, 920, 300)]


async def test_parent_viewport_missing_is_explicit_error():
    driver = RecordingMockDriver()
    driver.set_screen([{"id": "list", "bounds": WINDOW, "children": []}])
    with pytest.raises(ElementNotFound, match="没有直接父元素"):
        await _run(driver, _params())


async def test_target_inside_parent_but_outside_list_bounds_is_clicked():
    driver = RecordingMockDriver()
    driver.set_screen(_screen(
        target_y=700,
        parent_bounds={"x": 0, "y": 0, "width": 1000, "height": 1800},
        target_bounds={"x": 20, "y": 700, "width": 300, "height": 80},
    ))
    result = await _run(driver, _params())

    assert result["status"] == "passed"
    assert driver.clicked == ["目标"]


async def test_parent_region_is_clipped_to_window():
    driver = RecordingMockDriver()
    driver.set_screen(_screen(
        target_y=-200,
        parent_bounds={"x": -100, "y": -50, "width": 1200, "height": 2200},
    ))
    driver.set_scroll_callback(lambda count: _screen(
        target_y=900,
        parent_bounds={"x": -100, "y": -50, "width": 1200, "height": 2200},
    ) if count == 1 else None)
    result = await _run(driver, _params(preferred_direction="down"))

    assert result["status"] == "passed"
    assert driver.element_swipes == []
    assert driver.coordinate_swipes == [(500, 700, 500, 1300, 300)]


async def test_zero_area_parent_is_explicit_error():
    driver = RecordingMockDriver()
    driver.set_screen(_screen(parent_bounds={"x": 0, "y": 200, "width": 0, "height": 1000}))
    with pytest.raises(ElementNotFound, match="父元素.*有效可见区域"):
        await _run(driver, _params(max_swipes_per_direction=1))


async def test_target_outside_parent_viewport_is_not_clicked():
    driver = RecordingMockDriver()
    driver.set_screen(_screen(target_y=1600))
    with pytest.raises(ElementNotFound):
        await _run(driver, _params(max_swipes_per_direction=1))
    assert driver.clicked == []


def test_appium_parent_query_uses_relative_xpath():
    from appium.webdriver.common.appiumby import AppiumBy

    from executor.appium_driver import AppiumDriver

    class Element:
        def find_element(self, by, value):
            assert (by, value) == (AppiumBy.XPATH, "./parent::*")
            return "parent-element"

    driver = AppiumDriver(device={"platform": "android"})
    driver.driver = cast(Any, object())
    assert driver.get_parent_element(Element(), wait_timeout=0) == "parent-element"


def test_appium_parent_query_translates_missing_parent():
    from selenium.common.exceptions import NoSuchElementException

    from executor.appium_driver import AppiumDriver

    class Element:
        def find_element(self, by, value):
            raise NoSuchElementException("no such element")

    driver = AppiumDriver(device={"platform": "android"})
    driver.driver = cast(Any, object())
    with pytest.raises(ElementNotFound, match="没有直接父节点"):
        driver.get_parent_element(Element(), wait_timeout=0)


def test_appium_android_attach_applies_unlimited_xpath_context(monkeypatch):
    import appium.webdriver as appium_webdriver

    from executor.appium_driver import AppiumDriver

    sessions = []

    class Session:
        session_id = "attach-session"

        def __init__(self):
            self.settings = []

        def update_settings(self, settings):
            self.settings.append(settings)

    def fake_remote(**kwargs):
        session = Session()
        sessions.append((session, kwargs))
        return session

    monkeypatch.setattr(appium_webdriver, "Remote", fake_remote)
    driver = AppiumDriver(device={"platform": "android", "udid": "android-1"})
    driver.attach_to_current_app()

    session, kwargs = sessions[0]
    options = kwargs["options"]
    assert options.get_capability("appium:settings[limitXPathContextScope]") is False
    assert session.settings == [{"limitXPathContextScope": False}]


def test_appium_android_launch_setting_failure_is_actionable(monkeypatch):
    import appium.webdriver as appium_webdriver

    from executor.appium_driver import AppiumDriver
    from executor.driver import DriverError

    class Session:
        session_id = "launch-session"

        def update_settings(self, settings):
            raise RuntimeError("settings endpoint unavailable")

    monkeypatch.setattr(appium_webdriver, "Remote", lambda **kwargs: Session())
    driver = AppiumDriver(device={"platform": "android", "udid": "android-1"})
    with pytest.raises(DriverError, match="limitXPathContextScope=false"):
        driver.launch_app("com.example", "com.example.MainActivity")


def test_appium_ios_does_not_receive_android_xpath_setting():
    from executor.appium_driver import AppiumDriver

    options = AppiumDriver(device={"platform": "ios"})._build_options(
        "com.example.ios", None, True
    )
    assert options.get_capability("appium:settings[limitXPathContextScope]") is None
