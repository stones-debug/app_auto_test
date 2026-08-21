class DriverError(Exception):
    pass


class ElementNotFound(DriverError):
    pass


class StopRequested(DriverError):
    pass


class MockElement:
    def __init__(self, locator_value: str) -> None:
        self.locator_value = locator_value
        self.text = ""
        self._attributes: dict[str, str] = {}

    def get_attribute(self, name: str) -> str:
        return self._attributes.get(name, "")

    def click(self) -> None:
        pass

    def clear(self) -> None:
        pass


class BaseDriver:
    """驱动抽象：MockDriver 与 AppiumDriver 共用接口。"""

    def launch_app(self, package: str, activity: str | None = None, no_reset: bool = True) -> None:
        raise NotImplementedError

    def close_app(self, package: str | None = None) -> None:
        raise NotImplementedError

    def find_element(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
        raise NotImplementedError

    def click(self, element) -> None:
        raise NotImplementedError

    def input(self, element, value: str, clear_first: bool = True) -> None:
        raise NotImplementedError

    def clear(self, element) -> None:
        raise NotImplementedError

    def get_text(self, element) -> str:
        raise NotImplementedError

    def get_attribute(self, element, attribute: str) -> str:
        raise NotImplementedError

    def swipe(self, direction: str, duration: int = 500) -> None:
        raise NotImplementedError

    def scroll_to(self, element) -> None:
        raise NotImplementedError

    def back(self) -> None:
        raise NotImplementedError

    def tap_coordinate(self, x: int, y: int) -> None:
        raise NotImplementedError

    def screenshot(self, path: str) -> None:
        raise NotImplementedError

    def quit(self) -> None:
        raise NotImplementedError

    def interrupt(self) -> None:
        """CR-06：stop_test 时尝试终止阻塞中的操作（Appium 终止 app 会话；Mock 无操作）。"""
        pass


class MockDriver(BaseDriver):
    """确定性模拟驱动：以 locator_value 为 key 维护应用状态，供本地联调/测试。"""

    def __init__(self, initial_state: dict | None = None) -> None:
        self.state: dict[str, str] = dict(initial_state or {})
        self.screenshots: list[str] = []
        self.launched = False

    def launch_app(self, package: str, activity: str | None = None, no_reset: bool = True) -> None:
        self.launched = True

    def close_app(self, package: str | None = None) -> None:
        self.launched = False

    def find_element(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
        return MockElement(locator_value)

    def click(self, element) -> None:
        pass

    def input(self, element, value: str, clear_first: bool = True) -> None:
        self.state[element.locator_value] = value

    def clear(self, element) -> None:
        self.state[element.locator_value] = ""

    def get_text(self, element) -> str:
        return self.state.get(element.locator_value, "")

    def get_attribute(self, element, attribute: str) -> str:
        return element.get_attribute(attribute)

    def swipe(self, direction: str, duration: int = 500) -> None:
        pass

    def scroll_to(self, element) -> None:
        pass

    def back(self) -> None:
        pass

    def tap_coordinate(self, x: int, y: int) -> None:
        pass

    def screenshot(self, path: str) -> None:
        self.screenshots.append(path)

    def quit(self) -> None:
        pass


def create_driver(mode: str, config: dict | None = None, device: dict | None = None, initial_state: dict | None = None):
    if mode == "appium":
        from .appium_driver import AppiumDriver

        config = config or {}
        return AppiumDriver(
            host=config.get("appium_host", "127.0.0.1"),
            port=int(config.get("appium_port", 4723)),
            capabilities=config.get("appium_capabilities") or {},
            device=device,
            command_timeout=config.get("appium_command_timeout"),
        )
    return MockDriver(initial_state)
