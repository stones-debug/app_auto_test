import logging

from .driver import BaseDriver

logger = logging.getLogger("agent.appium")


class AppiumDriver(BaseDriver):
    """真实 Appium WebDriver 封装（需安装 appium-python-client）。

    CR-08：host/port/通用 capabilities 来自 config；设备信息（udid/platform）
    来自 Worker 下发的 start_test.device，据此构造 UiAutomator2/XCUITest options。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4723,
        capabilities: dict | None = None,
        device: dict | None = None,
        command_timeout: int | None = None,
    ) -> None:
        self.command_executor = f"http://{host}:{port}"
        self.capabilities = capabilities or {}
        self.device = device or {}
        self.command_timeout = command_timeout
        self.driver = None

    def _device_caps(self) -> dict:
        """由 Worker 下发的设备信息构造平台能力（CR-08）。"""
        caps: dict = {}
        udid = self.device.get("udid")
        if udid:
            caps["udid"] = udid
        platform = (self.device.get("platform") or "").lower()
        if platform == "ios":
            caps["platformName"] = "iOS"
            caps["automationName"] = "XCUITest"
        elif platform == "android":
            caps["platformName"] = "Android"
            caps["automationName"] = "UiAutomator2"
        return caps

    def _build_caps(self, package: str, activity: str | None, no_reset: bool) -> dict:
        caps = dict(self.capabilities)
        caps.update(self._device_caps())
        if self.command_timeout:
            caps["newCommandTimeout"] = self.command_timeout
        caps.update(
            {
                "appPackage": package,
                "appActivity": activity,
                "noReset": no_reset,
            }
        )
        return caps

    def _ensure(self):
        if self.driver is None:
            raise RuntimeError("Appium 会话未启动，请先执行 launch_app")

    def launch_app(self, package: str, activity: str | None = None, no_reset: bool = True) -> None:
        try:
            from appium import webdriver as appium_webdriver
        except ImportError as exc:
            raise RuntimeError("未安装 appium-python-client，无法使用 Appium 驱动（pip install 'agent[appium]'）") from exc
        caps = self._build_caps(package, activity, no_reset)
        self.driver = appium_webdriver.Remote(self.command_executor, caps)
        logger.info("Appium 会话已创建: %s", self.driver.session_id)

    def close_app(self, package: str | None = None) -> None:
        if self.driver is not None:
            try:
                self.driver.terminate_app(package or "")
            except Exception as exc:
                logger.warning("terminate_app 失败: %s", exc)

    def find_element(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
        from appium.webdriver.common.appiumby import AppiumBy

        self._ensure()
        by = getattr(AppiumBy, locator_type.upper(), None) or locator_type.upper()
        return self.driver.find_element(by, locator_value)

    def click(self, element) -> None:
        self._ensure()
        element.click()

    def input(self, element, value: str, clear_first: bool = True) -> None:
        self._ensure()
        if clear_first:
            element.clear()
        element.send_keys(value)

    def clear(self, element) -> None:
        self._ensure()
        element.clear()

    def get_text(self, element) -> str:
        self._ensure()
        return element.text or ""

    def get_attribute(self, element, attribute: str) -> str:
        self._ensure()
        return element.get_attribute(attribute) or ""

    def swipe(self, direction: str, duration: int = 500) -> None:
        self._ensure()
        size = self.driver.get_window_size()
        w, h = size["width"], size["height"]
        points = {
            "up": ((w // 2, int(h * 0.8)), (w // 2, int(h * 0.2))),
            "down": ((w // 2, int(h * 0.2)), (w // 2, int(h * 0.8))),
            "left": ((int(w * 0.8), h // 2), (int(w * 0.2), h // 2)),
            "right": ((int(w * 0.2), h // 2), (int(w * 0.8), h // 2)),
        }
        start, end = points.get(direction, points["up"])
        self.driver.swipe(*start, *end, duration)

    def scroll_to(self, element) -> None:
        self._ensure()
        self.driver.execute_script("arguments[0].scrollIntoView(true);", element)

    def back(self) -> None:
        self._ensure()
        self.driver.back()

    def tap_coordinate(self, x: int, y: int) -> None:
        self._ensure()
        self.driver.tap([(x, y)])

    def screenshot(self, path: str) -> None:
        self._ensure()
        self.driver.save_screenshot(path)

    def quit(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception as exc:
                logger.warning("driver.quit 失败: %s", exc)
            self.driver = None

    def interrupt(self) -> None:
        """CR-06：stop_test 时终止当前 app 会话，打断阻塞中的 Appium 命令。"""
        if self.driver is None:
            return
        try:
            package = None
            try:
                package = self.driver.current_package
            except Exception:
                package = None
            if package:
                self.driver.terminate_app(package)
                logger.info("已终止 app 会话: %s", package)
        except Exception as exc:
            logger.warning("interrupt 终止 Appium 会话失败: %s", exc)
