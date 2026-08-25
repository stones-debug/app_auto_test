import asyncio
import logging

from .driver import DriverError, ElementNotFound, StopRequested

logger = logging.getLogger("agent.executor.actions")

_STALE_EXCEPTION_NAMES = frozenset(
    {
        "StaleElementReferenceException",
        "StaleObjectException",
    }
)
_STALE_MESSAGE_MARKERS = (
    "stale element reference",
    "staleelementreferenceexception",
    "staleobjectexception",
)


def _is_stale_element_error(error: BaseException) -> bool:
    """识别 Selenium/Appium 直接抛出或包装后的元素失效异常。"""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if current.__class__.__name__ in _STALE_EXCEPTION_NAMES:
            return True
        message = str(current).lower()
        if any(marker in message for marker in _STALE_MESSAGE_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


class BaseAction:
    async def execute(self, driver, context, params: dict) -> dict:
        raise NotImplementedError


ACTION_REGISTRY: dict[str, type[BaseAction]] = {}


def register_action(name: str):
    def decorator(cls: type[BaseAction]):
        ACTION_REGISTRY[name] = cls
        return cls

    return decorator


@register_action("launch_app")
class LaunchAppAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        driver.launch_app(
            params.get("package", ""),
            activity=params.get("activity"),
            no_reset=params.get("no_reset", True),
        )
        return {"status": "passed"}


@register_action("close_app")
class CloseAppAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        driver.close_app(params.get("package"))
        return {"status": "passed"}


@register_action("click")
class ClickAction(BaseAction):
    # UIAutomator2 页面重绘时，元素可能在“定位成功”与“点击”之间失效。
    # 重试必须回到元素快照重新定位，不能复用已经 stale 的 WebElement。
    _STALE_RETRY_DELAYS = (0.2, 0.5)

    @staticmethod
    def _raise_if_stopped(context) -> None:
        stop = getattr(context, "should_stop", None)
        if stop is not None and stop():
            raise StopRequested("执行被用户停止")

    async def execute(self, driver, context, params: dict) -> dict:
        wait_timeout = params.get("wait_timeout")
        element_id = params.get("element_id")
        for attempt in range(len(self._STALE_RETRY_DELAYS) + 1):
            self._raise_if_stopped(context)
            try:
                element = context.find_element(element_id, wait_timeout=wait_timeout)
                driver.click(element)
                return {"status": "passed"}
            except Exception as exc:
                if not _is_stale_element_error(exc):
                    raise
                if attempt >= len(self._STALE_RETRY_DELAYS):
                    logger.error(
                        "click 元素连续失效，重新定位重试仍失败: element_id=%s",
                        element_id,
                        exc_info=True,
                    )
                    raise DriverError(
                        "点击失败：页面持续刷新导致元素失效，重新定位并重试 2 次后仍未成功"
                    ) from exc
                delay = self._STALE_RETRY_DELAYS[attempt]
                logger.warning(
                    "click 遇到失效元素，将在 %.1fs 后重新定位（第 %s/%s 次重试）: element_id=%s",
                    delay,
                    attempt + 1,
                    len(self._STALE_RETRY_DELAYS),
                    element_id,
                )
                await asyncio.sleep(delay)
                self._raise_if_stopped(context)

        raise AssertionError("click stale 重试状态异常")


@register_action("input")
class InputAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"), editable=True)
        driver.input(element, str(params.get("value", "")), clear_first=params.get("clear_first", True))
        return {"status": "passed"}


@register_action("clear")
class ClearAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"), editable=True)
        driver.clear(element)
        return {"status": "passed"}


@register_action("swipe")
class SwipeAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        driver.swipe(params.get("direction", "up"), duration=params.get("duration", 500))
        return {"status": "passed"}


@register_action("swipe_to_find")
class SwipeToFindAction(BaseAction):
    """上下滑动页面直至找到目标元素（最多 max_swipes 次滑动，每次滑动前短等待查找）。

    找到返回 passed + found_after_swipes（0 表示未滑动即找到）；
    滑完仍未找到抛 ElementNotFound（步骤失败并附可读信息）。
    """

    async def execute(self, driver, context, params: dict) -> dict:
        max_swipes = int(params.get("max_swipes", 5))
        direction = params.get("direction", "up")
        wait_timeout = params.get("wait_timeout", 2)
        duration = int(params.get("duration", 500))
        stop = getattr(context, "should_stop", None)
        last_error: ElementNotFound | None = None
        for i in range(max_swipes + 1):
            if stop is not None and stop():
                raise StopRequested("执行被用户停止")
            try:
                context.find_element(params.get("element_id"), wait_timeout=wait_timeout)
                return {"status": "passed", "found_after_swipes": i}
            except ElementNotFound as exc:
                last_error = exc
            if i < max_swipes:
                driver.swipe(direction, duration=duration)
        raise ElementNotFound(f"滑动 {max_swipes} 次后仍未找到元素（{direction}，{last_error}）")


@register_action("scroll")
class ScrollAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        driver.scroll_to(element)
        return {"status": "passed"}


@register_action("back")
class BackAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        driver.back()
        return {"status": "passed"}


@register_action("sleep")
class SleepAction(BaseAction):
    """分片 sleep：stop 信号能及时打断阻塞中的动作线程（收敛性，Step 3）。"""

    _POLL = 0.2

    async def execute(self, driver, context, params: dict) -> dict:
        duration = float(params.get("duration", 1))
        stop = getattr(context, "should_stop", None)
        deadline = asyncio.get_event_loop().time() + duration
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return {"status": "passed"}
            if stop is not None and stop():
                raise StopRequested("执行被用户停止")
            await asyncio.sleep(min(self._POLL, remaining))


@register_action("screenshot")
class ScreenshotAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        filename = params.get("filename") or "screenshot.png"
        path = context.save_screenshot(filename)
        return {"status": "passed", "screenshot_path": path}


@register_action("get_text")
class GetTextAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        value = driver.get_text(element)
        variable_name = params.get("variable_name")
        if variable_name:
            context.variables[variable_name] = value
        return {"status": "passed", "actual_value": value}


@register_action("get_attribute")
class GetAttributeAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        value = driver.get_attribute(element, params.get("attribute", ""))
        variable_name = params.get("variable_name")
        if variable_name:
            context.variables[variable_name] = value
        return {"status": "passed", "actual_value": value}


@register_action("tap_coordinate")
class TapCoordinateAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        driver.tap_coordinate(int(params.get("x", 0)), int(params.get("y", 0)))
        return {"status": "passed"}
