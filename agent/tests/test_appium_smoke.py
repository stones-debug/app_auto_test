"""CR-08 真实 Appium smoke test（需模拟器/真机 + Appium 服务，默认跳过）。

用法:
    APPIUM_URL=http://127.0.0.1:4723 APPIUM_UDID=emulator-5554 \\
    APPIUM_PACKAGE=com.android.settings APPIUM_ACTIVITY=.Settings \\
    uv run pytest tests/test_appium_smoke.py -q

覆盖最小真实会话：建连 → launch → find → screenshot → quit。
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("APPIUM_URL") or not os.environ.get("APPIUM_UDID"),
    reason="未设置 APPIUM_URL/APPIUM_UDID，跳过真实 Appium smoke test",
)


def test_appium_smoke_session():
    appium_webdriver = pytest.importorskip("appium.webdriver")
    from appium.webdriver.common.appiumby import AppiumBy

    url = os.environ["APPIUM_URL"]
    caps = {
        "udid": os.environ["APPIUM_UDID"],
        "platformName": os.environ.get("APPIUM_PLATFORM", "Android"),
        "automationName": "UiAutomator2",
        "appPackage": os.environ.get("APPIUM_PACKAGE", "com.android.settings"),
        "noReset": True,
        "newCommandTimeout": 120,
    }
    activity = os.environ.get("APPIUM_ACTIVITY")
    if activity:
        caps["appActivity"] = activity

    driver = appium_webdriver.Remote(url, caps)
    try:
        # 建连
        assert driver.session_id is not None
        # launch + find（应用已通过 appPackage 启动）
        elements = driver.find_elements(AppiumBy.XPATH, "//*")
        assert len(elements) > 0
        # screenshot
        screenshot = driver.get_screenshot_as_base64()
        assert screenshot
    finally:
        driver.quit()
