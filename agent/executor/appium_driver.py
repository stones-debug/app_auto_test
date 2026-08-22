import logging
from typing import TYPE_CHECKING

from .driver import BaseDriver, DriverError

if TYPE_CHECKING:
    from appium.webdriver.webdriver import WebDriver

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
        self.driver: WebDriver | None = None

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

    def _build_options(self, package: str, activity: str | None, no_reset: bool):
        """构造 Appium 6 options（UiAutomator2 / XCUITest）。

        通用配置与设备能力从 dict 加载进 options；Worker 下发的 udid/platformName/
        automationName 为最高优先级，禁止通用配置覆盖目标 UDID。
        """
        from appium.options.android import UiAutomator2Options
        from appium.options.ios import XCUITestOptions

        caps = dict(self.capabilities)
        caps.update(self._device_caps())
        if self.command_timeout:
            caps["newCommandTimeout"] = self.command_timeout
        platform = (self.device.get("platform") or "").lower()
        if platform == "ios":
            options = XCUITestOptions()
            caps["bundleId"] = package
        else:
            options = UiAutomator2Options()
            caps["appPackage"] = package
            if activity is not None:
                caps["appActivity"] = activity
        caps["noReset"] = no_reset
        options.load_capabilities(caps)
        return options

    def _configured_android_capability(self, name: str) -> str | None:
        """读取普通、W3C 前缀或 appium:options 中的 Android capability。"""
        candidates = (name, f"appium:{name}")
        for key in candidates:
            value = self.capabilities.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        appium_options = self.capabilities.get("appium:options")
        if isinstance(appium_options, dict):
            for key in candidates:
                value = appium_options.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _resolve_android_activity(self, package: str, activity: str | None) -> tuple[str, bool]:
        """返回有效 Activity 及其是否由 ADB 自动解析。"""
        explicit = activity.strip() if isinstance(activity, str) else ""
        if explicit:
            return explicit, False

        configured = self._configured_android_capability("appActivity")
        if configured:
            return configured, False

        udid = str(self.device.get("udid") or "").strip()
        from devices.adb import AdbError, resolve_launcher_activity

        try:
            resolved = resolve_launcher_activity(udid, package)
        except AdbError as exc:
            raise DriverError(
                f"无法自动识别应用 {package} 的启动 Activity：{exc}。"
                "请确认包名正确，或在“启动 APP”步骤中显式填写 Activity"
            ) from exc
        logger.info("已自动解析 Android 启动 Activity: %s/%s", package, resolved)
        return resolved, True

    def _apply_auto_activity_wait(self, options, package: str) -> None:
        """自动解析入口时兼容 launcher alias、闪屏页和跳板 Activity。"""
        if self._configured_android_capability("appWaitPackage") is None:
            options.set_capability("appWaitPackage", package)
        if self._configured_android_capability("appWaitActivity") is None:
            options.set_capability("appWaitActivity", "*")

    def _ensure(self) -> "WebDriver":
        if self.driver is None:
            raise RuntimeError("Appium 会话未启动，请先执行 launch_app")
        return self.driver

    def launch_app(self, package: str, activity: str | None = None, no_reset: bool = True) -> None:
        try:
            from appium import webdriver as appium_webdriver
        except ImportError as exc:
            raise RuntimeError("未安装 appium-python-client，无法使用 Appium 驱动（pip install 'agent[appium]'）") from exc
        package = str(package or "").strip()
        if not package:
            raise DriverError("启动 APP 失败：包名不能为空")

        platform = (self.device.get("platform") or "").lower()
        resolved_activity = activity
        auto_resolved = False
        if platform != "ios":
            resolved_activity, auto_resolved = self._resolve_android_activity(package, activity)

        options = self._build_options(package, resolved_activity, no_reset)
        if auto_resolved:
            self._apply_auto_activity_wait(options, package)
        try:
            self.driver = appium_webdriver.Remote(
                command_executor=self.command_executor,
                options=options,
            )
        except Exception as exc:
            target = package if platform == "ios" else f"{package}/{resolved_activity}"
            raise DriverError(f"Appium 启动应用失败（{target}）：{exc}") from exc
        logger.info("Appium 会话已创建: %s", self.driver.session_id)

    def close_app(self, package: str | None = None) -> None:
        if self.driver is not None:
            try:
                self.driver.terminate_app(package or "")
            except Exception as exc:
                logger.warning("terminate_app 失败: %s", exc)

    def find_element(self, locator_type: str, locator_value: str, wait_timeout: int | None = None):
        """按 wait_timeout 等待元素。

        - None → 默认 10 秒；
        - 0 → 立即查找（driver.find_element 一次）；
        - >0 → Selenium WebDriverWait 轮询；超时统一转 ElementNotFound。
        """
        from appium.webdriver.common.appiumby import AppiumBy
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        from .driver import ElementNotFound

        driver = self._ensure()
        by = getattr(AppiumBy, locator_type.upper(), None) or By.XPATH
        timeout = wait_timeout if wait_timeout is not None else 10
        if timeout <= 0:
            return driver.find_element(by, locator_value)
        try:
            return WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, locator_value))
            )
        except TimeoutException:
            raise ElementNotFound(
                f"元素等待超时: {locator_type}={locator_value} ({timeout}s)"
            ) from None

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
        driver = self._ensure()
        size = driver.get_window_size()
        w, h = size["width"], size["height"]
        points = {
            "up": ((w // 2, int(h * 0.8)), (w // 2, int(h * 0.2))),
            "down": ((w // 2, int(h * 0.2)), (w // 2, int(h * 0.8))),
            "left": ((int(w * 0.8), h // 2), (int(w * 0.2), h // 2)),
            "right": ((int(w * 0.2), h // 2), (int(w * 0.8), h // 2)),
        }
        start, end = points.get(direction, points["up"])
        driver.swipe(start[0], start[1], end[0], end[1], duration)

    def scroll_to(self, element) -> None:
        driver = self._ensure()
        driver.execute_script("arguments[0].scrollIntoView(true);", element)

    def back(self) -> None:
        driver = self._ensure()
        driver.back()

    def tap_coordinate(self, x: int, y: int) -> None:
        driver = self._ensure()
        driver.tap([(x, y)])

    def screenshot(self, path: str) -> None:
        driver = self._ensure()
        driver.save_screenshot(path)

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
