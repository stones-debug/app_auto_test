"""CR-08 真实 Appium smoke test（需模拟器/真机 + Appium 服务，默认跳过）。

用法:
    APPIUM_URL=http://127.0.0.1:4723 APPIUM_UDID=emulator-5554 \\
    APPIUM_PACKAGE=com.android.settings APPIUM_ACTIVITY=.Settings \\
    uv run pytest tests/test_appium_smoke.py -q

覆盖最小真实会话：建连 → launch → find → screenshot → quit。

驱动一律经由生产 AppiumDriver 创建（Appium 6 options 建连），
避免测试复制另一套 Remote 调用代码。
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("APPIUM_URL") or not os.environ.get("APPIUM_UDID"),
    reason="未设置 APPIUM_URL/APPIUM_UDID，跳过真实 Appium smoke test",
)


def test_appium_smoke_session():
    pytest.importorskip("appium.webdriver")
    from appium.webdriver.common.appiumby import AppiumBy

    from executor.appium_driver import AppiumDriver

    url = os.environ["APPIUM_URL"]
    # 解析 APPIUM_URL 的 host/port，构造与生产一致 AppiumDriver
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    driver = AppiumDriver(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 4723,
        device={
            "udid": os.environ["APPIUM_UDID"],
            "platform": os.environ.get("APPIUM_PLATFORM", "android").lower(),
        },
        command_timeout=120,
    )
    package = os.environ.get("APPIUM_PACKAGE", "com.android.settings")
    activity = os.environ.get("APPIUM_ACTIVITY")
    driver.launch_app(package, activity)
    try:
        # 建连
        assert driver.driver is not None and driver.driver.session_id is not None
        # launch + find（应用已通过 appPackage 启动）
        elements = driver.driver.find_elements(AppiumBy.XPATH, "//*")
        assert len(elements) > 0
        # screenshot
        screenshot = driver.driver.get_screenshot_as_base64()
        assert screenshot
    finally:
        driver.quit()
