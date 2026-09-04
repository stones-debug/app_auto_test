import hashlib
import logging
import math
import threading
import time
from typing import TYPE_CHECKING

from .driver import BaseDriver, DriverError, _coerce_bool

if TYPE_CHECKING:
    from appium.webdriver.webdriver import WebDriver

logger = logging.getLogger("agent.appium")


def _xpath_literal(value: str) -> str:
    """将任意字符串编码为 XPath 字面量。"""
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    parts = value.split('"')
    return "concat(" + ", '\"', ".join(f'"{part}"' for part in parts) + ")"


def _resource_id_xpath(resource_id: str) -> str:
    """把纯 resource-id 转为精确 XPath，并保留可编辑子节点后缀。"""
    editable_suffix = "//android.widget.EditText"
    has_editable_suffix = resource_id.endswith(editable_suffix)
    pure_resource_id = resource_id[: -len(editable_suffix)] if has_editable_suffix else resource_id
    xpath = f"//*[@resource-id={_xpath_literal(pure_resource_id)}]"
    return f"{xpath}{editable_suffix}" if has_editable_suffix else xpath


class AppiumDriver(BaseDriver):
    """真实 Appium WebDriver 封装（需安装 appium-python-client）。

    CR-08：host/port/通用 capabilities 来自 config；设备信息（udid/platform）
    来自 Worker 下发的 start_test.device，据此构造 UiAutomator2/XCUITest options。
    """

    # Appium 查询、UiAutomator2 通信和设备端页面刷新都可能超过几十毫秒。
    # 逻辑等待时间由 WebDriverWait/deadline 控制，不能直接作为 HTTP 传输超时。
    HTTP_MIN_TIMEOUT = 5.0
    # 接近逻辑 deadline 时，不能把一次请求强行放大到 5 秒；保留一个
    # 足以完成本机回环 Appium 请求的短窗口，避免取消线程长期占用请求锁。
    HTTP_SHORT_TIMEOUT_MIN = 0.5
    HTTP_MAX_TIMEOUT = 30.0
    HTTP_IMMEDIATE_TIMEOUT = 2.0
    DEFAULT_HTTP_REQUEST_TIMEOUT = HTTP_MAX_TIMEOUT

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4723,
        capabilities: dict | None = None,
        device: dict | None = None,
        command_timeout: int | None = None,
        http_request_timeout: float | None = None,
    ) -> None:
        self.command_executor = f"http://{host}:{port}"
        self.capabilities = capabilities or {}
        self.device = device or {}
        self.command_timeout = command_timeout
        self.http_request_timeout = self._normalize_http_request_timeout(http_request_timeout)
        self.driver: WebDriver | None = None
        self._http_timeout_default: float | None = None
        self._http_command_lock = threading.RLock()

    @classmethod
    def _normalize_http_request_timeout(cls, timeout: float | None) -> float:
        if timeout is None:
            return cls.DEFAULT_HTTP_REQUEST_TIMEOUT
        try:
            value = float(timeout)
        except (TypeError, ValueError):
            return cls.DEFAULT_HTTP_REQUEST_TIMEOUT
        if not math.isfinite(value):
            return cls.DEFAULT_HTTP_REQUEST_TIMEOUT
        return min(cls.HTTP_MAX_TIMEOUT, max(cls.HTTP_MIN_TIMEOUT, value))

    def _client_config(self):
        driver = self.driver
        executor = getattr(driver, "command_executor", None)
        return getattr(executor, "_client_config", None)

    def _remember_http_timeout_default(self) -> None:
        client_config = self._client_config()
        self._http_timeout_default = getattr(client_config, "timeout", None)

    def _effective_http_timeout(self, remaining: float | None, *, immediate: bool = False) -> float:
        if immediate:
            return self.HTTP_IMMEDIATE_TIMEOUT
        if remaining is None:
            return self.http_request_timeout
        return min(
            self.http_request_timeout,
            max(self.HTTP_SHORT_TIMEOUT_MIN, float(remaining)),
        )

    def set_command_timeout(self, timeout: float | None) -> None:
        """兼容旧调用方设置 HTTP 超时；新代码应使用 run_with_http_timeout。"""
        client_config = self._client_config()
        if client_config is None:
            return
        with self._http_command_lock:
            client_config.timeout = (
                self._http_timeout_default
                if timeout is None
                else self._effective_http_timeout(timeout)
            )

    def run_with_http_timeout(
        self, remaining: float | None, operation, *, immediate: bool = False
    ):
        """使用独立的 HTTP 超时执行单次请求，并原子恢复原有配置。

        Appium 的 WebDriver client config 在同一驱动上是共享对象。必须把锁覆盖
        整个请求，而不是只锁住设置/恢复动作，否则被取消的工作线程可能在新请求
        执行期间恢复旧超时。
        """
        client_config = self._client_config()
        if client_config is None:
            return operation()
        with self._http_command_lock:
            old_timeout = client_config.timeout
            client_config.timeout = self._effective_http_timeout(remaining, immediate=immediate)
            try:
                return operation()
            finally:
                client_config.timeout = old_timeout

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
        if platform == "android":
            self._add_android_parent_xpath_setting(caps)
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

    def _build_current_app_options(self):
        """构造不携带 appPackage/bundleId 的会话，保持当前前台应用不变。"""
        from appium.options.android import UiAutomator2Options
        from appium.options.ios import XCUITestOptions

        caps = dict(self.capabilities)
        caps.update(self._device_caps())
        if self.command_timeout:
            caps["newCommandTimeout"] = self.command_timeout
        platform = (self.device.get("platform") or "").lower()
        if platform == "android":
            self._add_android_parent_xpath_setting(caps)
        options = XCUITestOptions() if platform == "ios" else UiAutomator2Options()
        caps.pop("appPackage", None)
        caps.pop("appActivity", None)
        caps.pop("bundleId", None)
        caps.pop("appium:appPackage", None)
        caps.pop("appium:appActivity", None)
        caps.pop("appium:bundleId", None)
        appium_options = caps.get("appium:options")
        if isinstance(appium_options, dict):
            appium_options = dict(appium_options)
            for key in ("appPackage", "appActivity", "bundleId", "appium:appPackage", "appium:appActivity", "appium:bundleId"):
                appium_options.pop(key, None)
            caps["appium:options"] = appium_options
        caps["noReset"] = True
        if platform != "ios":
            caps["dontStopAppOnReset"] = True
        options.load_capabilities(caps)
        return options

    @staticmethod
    def _add_android_parent_xpath_setting(caps: dict) -> None:
        """在 Android 会话创建能力中关闭受限的 XPath context scope。

        UiAutomator2 默认只为元素上下文收集后代节点，导致 ``./parent::*``
        无法工作。Appium 官方支持 ``appium:settings[<name>]`` 能力；同时
        合并 ``appium:settings`` 对象，兼容较新的 Appium 版本和已有配置。
        """
        caps["appium:settings[limitXPathContextScope]"] = False
        configured = caps.get("appium:settings")
        settings = dict(configured) if isinstance(configured, dict) else {}
        settings["limitXPathContextScope"] = False
        caps["appium:settings"] = settings
        configured_options = caps.get("appium:options")
        if isinstance(configured_options, dict):
            options = dict(configured_options)
            nested = options.get("settings")
            nested_settings = dict(nested) if isinstance(nested, dict) else {}
            nested_settings["limitXPathContextScope"] = False
            options["settings"] = nested_settings
            caps["appium:options"] = options

    def _apply_android_parent_xpath_setting(self) -> None:
        """会话创建后再次应用 setting，确保真机使用完整 XPath context。"""
        if (self.device.get("platform") or "").lower() != "android" or self.driver is None:
            return
        update_settings = getattr(self.driver, "update_settings", None)
        if not callable(update_settings):
            # 旧/极简客户端可能没有 Settings API；创建能力仍已携带官方 setting。
            logger.warning(
                "Appium 会话未提供 update_settings，父元素 XPath 将依赖会话创建时的 "
                "appium:settings[limitXPathContextScope]=false"
            )
            return
        try:
            update_settings({"limitXPathContextScope": False})
        except Exception as exc:
            raise DriverError(
                "Appium Android 会话无法设置 limitXPathContextScope=false，"
                "父元素视口不可用"
            ) from exc

    def attach_to_current_app(self) -> None:
        """创建 Appium 会话并保持设备当前前台界面，不启动指定 APP。"""
        try:
            from appium import webdriver as appium_webdriver
        except ImportError as exc:
            raise RuntimeError("未安装 appium-python-client，无法使用 Appium 驱动（pip install 'agent[appium]'）") from exc
        try:
            self.driver = appium_webdriver.Remote(
                command_executor=self.command_executor,
                options=self._build_current_app_options(),
            )
            self._remember_http_timeout_default()
        except Exception as exc:
            raise DriverError(f"Appium 连接当前设备界面失败：{exc}") from exc
        self._apply_android_parent_xpath_setting()
        logger.info("Appium 当前界面会话已创建: %s", self.driver.session_id)

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
            self._remember_http_timeout_default()
        except Exception as exc:
            target = package if platform == "ios" else f"{package}/{resolved_activity}"
            raise DriverError(f"Appium 启动应用失败（{target}）：{exc}") from exc
        self._apply_android_parent_xpath_setting()
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
        from selenium.webdriver.support.ui import WebDriverWait

        from .driver import ElementNotFound

        driver = self._ensure()
        normalized_type = str(locator_type or "").strip().lower()
        normalized_value = str(locator_value or "").strip()
        if normalized_type == "resource_id":
            by = AppiumBy.XPATH
            normalized_value = _resource_id_xpath(normalized_value)
        else:
            by = getattr(AppiumBy, normalized_type.upper(), None) or By.XPATH

        timeout = float(wait_timeout if wait_timeout is not None else 10)
        timeout_label = int(timeout) if timeout.is_integer() else timeout
        operation_deadline = time.monotonic() + max(0.0, timeout)

        def locate(_driver):
            remaining = operation_deadline - time.monotonic()
            if timeout > 0 and remaining <= 0:
                # WebDriverWait 的逻辑窗口已结束，不再发起一个必然越过 deadline
                # 的请求；wait_timeout=0 走一次立即查询例外。
                raise TimeoutException()
            return self.run_with_http_timeout(
                None if timeout <= 0 else remaining,
                lambda: driver.find_element(by, normalized_value),
                immediate=timeout <= 0,
            )

        try:
            if timeout <= 0:
                return locate(driver)
            return WebDriverWait(driver, timeout).until(locate)
        except TimeoutException:
            raise ElementNotFound(
                f"元素等待超时: {normalized_type}={normalized_value} ({timeout_label}s)"
            ) from None

    def get_parent_element(self, element, wait_timeout: float | None = 10):
        """通过元素上下文的相对 XPath 获取直接父节点。

        父视口没有独立的元素库定位值，所以必须在当前轮次、使用刚定位的
        container 句柄查询。逻辑等待由 WebDriverWait 控制，单次 HTTP 请求
        使用剩余时间，避免把 Appium 请求超时误当成元素等待时间。
        """
        from appium.webdriver.common.appiumby import AppiumBy
        from selenium.common.exceptions import NoSuchElementException, TimeoutException
        from selenium.webdriver.support.ui import WebDriverWait

        from .driver import ElementNotFound
        from .stale_guard import is_stale_element_error

        driver = self._ensure()
        timeout = float(wait_timeout if wait_timeout is not None else 10)
        timeout = max(0.0, timeout)
        operation_deadline = time.monotonic() + timeout

        def locate(_driver):
            remaining = operation_deadline - time.monotonic()
            if timeout > 0 and remaining <= 0:
                raise TimeoutException()
            return self.run_with_http_timeout(
                None if timeout <= 0 else remaining,
                lambda: element.find_element(AppiumBy.XPATH, "./parent::*"),
                immediate=timeout <= 0,
            )

        def translate_missing(exc):
            if isinstance(exc, (NoSuchElementException, TimeoutException)):
                raise ElementNotFound("元素没有直接父节点（无法作为父元素视口）") from None
            # stale 必须原样上抛，交由动作层现有 stale 重试重新定位 container。
            if is_stale_element_error(exc):
                raise exc
            if "no such element" in str(exc).lower():
                raise ElementNotFound("元素没有直接父节点（无法作为父元素视口）") from exc
            raise DriverError(f"获取元素直接父节点失败：{exc}") from exc

        try:
            if timeout <= 0:
                return locate(driver)
            return WebDriverWait(driver, timeout).until(locate)
        except (NoSuchElementException, TimeoutException) as exc:
            translate_missing(exc)
        except Exception as exc:
            translate_missing(exc)
        raise AssertionError("父节点定位状态异常")

    def find_elements(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
        """多匹配查询（智能定位用）：返回当前页面全部匹配元素。

        与 find_element 的单元素等待不同，本方法为快照式多匹配查询，
        滚动/重试节奏由智能定位解析器自行控制。
        """
        from appium.webdriver.common.appiumby import AppiumBy
        from selenium.webdriver.common.by import By

        driver = self._ensure()
        normalized_type = str(locator_type or "").strip().lower()
        normalized_value = str(locator_value or "").strip()
        if normalized_type == "uiautomator":
            by = AppiumBy.ANDROID_UIAUTOMATOR
        elif normalized_type == "resource_id":
            by = AppiumBy.XPATH
            normalized_value = _resource_id_xpath(normalized_value)
        else:
            by = getattr(AppiumBy, normalized_type.upper(), None) or By.XPATH
        return driver.find_elements(by, normalized_value)

    def find_elements_in_element(
        self, element, locator_type: str, locator_value: str, wait_timeout: int = 0
    ):
        from appium.webdriver.common.appiumby import AppiumBy
        from selenium.webdriver.common.by import By

        normalized_type = str(locator_type or "").strip().lower()
        normalized_value = str(locator_value or "").strip()
        if normalized_type == "uiautomator":
            by = AppiumBy.ANDROID_UIAUTOMATOR
        elif normalized_type == "resource_id":
            by = AppiumBy.XPATH
            normalized_value = _resource_id_xpath(normalized_value)
        else:
            by = getattr(AppiumBy, normalized_type.upper(), None) or By.XPATH
        timeout = float(wait_timeout) if wait_timeout is not None else 0.0
        return self.run_with_http_timeout(
            None if timeout <= 0 else timeout,
            lambda: element.find_elements(by, normalized_value),
            immediate=timeout <= 0,
        )

    def page_signature(self) -> str:
        """当前页面指纹：page_source 的 sha256 前 16 位 + 长度（绝不回传完整源码）。"""
        driver = self._ensure()
        source = driver.page_source or ""
        return f"{hashlib.sha256(source.encode('utf-8')).hexdigest()[:16]}:{len(source)}"

    def click(self, element) -> None:
        self._ensure()
        element.click()

    def input(self, element, value: str, clear_first: bool = True) -> None:
        self._ensure()
        from selenium.common.exceptions import InvalidElementStateException

        try:
            if clear_first:
                element.clear()
            element.send_keys(value)
        except InvalidElementStateException as exc:
            raise DriverError(
                "输入失败：定位到的控件不可编辑。请确认元素定位值直接指向可编辑控件，"
                "并确认控件处于启用状态"
            ) from exc

    def clear(self, element) -> None:
        self._ensure()
        element.clear()

    def get_text(self, element) -> str:
        self._ensure()
        return element.text or ""

    def get_attribute(self, element, attribute: str) -> str:
        self._ensure()
        return element.get_attribute(attribute) or ""

    def is_checked(self, element) -> bool:
        """读取 Android checkbox 的 checked 状态，selected 作为兼容回退。"""
        self._ensure()
        checked = element.get_attribute("checked")
        if checked not in (None, ""):
            return _coerce_bool(checked)
        is_selected = getattr(element, "is_selected", None)
        if callable(is_selected):
            return bool(is_selected())
        return False

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

    def _ensure_android_gesture(self) -> None:
        platform = (self.device.get("platform") or "").lower()
        if platform and platform != "android":
            raise DriverError("控件内滑动、区域内滑动和滑块坐标拖动当前仅支持 Android（UiAutomator2）")

    def swipe_in_element(
        self, element, direction: str, percent: float, speed: int | None = None
    ) -> None:
        """UiAutomator2 mobile: swipeGesture，在元素自身边界内滑动。"""
        self._ensure_android_gesture()
        driver = self._ensure()
        payload = {"elementId": element.id, "direction": direction, "percent": percent}
        if speed is not None:
            payload["speed"] = speed
        driver.execute_script("mobile: swipeGesture", payload)

    def scroll_in_element(self, element, direction: str, percent: float) -> bool:
        """UiAutomator2 mobile: scrollGesture，在元素边界内滚动并返回是否还能继续滚动。"""
        self._ensure_android_gesture()
        driver = self._ensure()
        result = driver.execute_script(
            "mobile: scrollGesture",
            {"elementId": element.id, "direction": direction, "percent": percent},
        )
        return bool(result)

    def get_element_rect(self, element) -> dict[str, int]:
        """返回元素矩形 x、y、width、height（W3C rect）。"""
        self._ensure()
        rect = element.rect or {}
        return {
            "x": int(rect.get("x", 0)),
            "y": int(rect.get("y", 0)),
            "width": int(rect.get("width", 0)),
            "height": int(rect.get("height", 0)),
        }

    def swipe_in_region(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        direction: str,
        percent: float,
        speed: int | None = None,
    ) -> None:
        """UiAutomator2 mobile: swipeGesture，在给定屏幕像素区域内滑动。"""
        self._ensure_android_gesture()
        driver = self._ensure()
        payload = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "direction": direction,
            "percent": percent,
        }
        if speed is not None:
            payload["speed"] = speed
        driver.execute_script("mobile: swipeGesture", payload)

    def get_window_size(self) -> dict[str, int]:
        return self._ensure().get_window_size()

    def scroll_to(self, element) -> None:
        driver = self._ensure()
        driver.execute_script("arguments[0].scrollIntoView(true);", element)

    def back(self) -> None:
        driver = self._ensure()
        driver.back()

    def tap_coordinate(self, x: int, y: int) -> None:
        driver = self._ensure()
        driver.tap([(x, y)])

    def drag_coordinate(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int
    ) -> None:
        self._ensure_android_gesture()
        driver = self._ensure()
        driver.swipe(start_x, start_y, end_x, end_y, duration_ms)

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
