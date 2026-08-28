import asyncio

from .driver import ElementNotFound, StopRequested
from .stale_guard import with_stale_retry


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

    async def execute(self, driver, context, params: dict) -> dict:
        await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.click(element),
            wait_timeout=params.get("wait_timeout"),
            retries=len(self._STALE_RETRY_DELAYS),
            delays=self._STALE_RETRY_DELAYS,
            label="点击",
        )
        return {"status": "passed"}


@register_action("input")
class InputAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        value = str(params.get("value", ""))
        clear_first = params.get("clear_first", True)
        await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.input(element, value, clear_first=clear_first),
            editable=True,
            label="输入",
        )
        return {"status": "passed"}


@register_action("clear")
class ClearAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.clear(element),
            editable=True,
            label="清空",
        )
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


@register_action("swipe_in_element")
class SwipeInElementAction(BaseAction):
    """在目标控件内部滑动，适用于日期选择器、列表和独立滚动容器。"""

    async def execute(self, driver, context, params: dict) -> dict:
        await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.swipe_in_element(
                element,
                params.get("direction", "up"),
                float(params.get("percent", 0.3)),
            ),
            wait_timeout=params.get("wait_timeout"),
            label="控件内滑动",
        )
        return {"status": "passed"}


@register_action("swipe_in_region")
class SwipeInRegionAction(BaseAction):
    """按屏幕百分比换算区域，在区域内部滑动。"""

    async def execute(self, driver, context, params: dict) -> dict:
        # 后端协议模型已校验 0..100 和边界和；这里保留换算，避免屏幕分辨率写入用例。
        left_percent = float(params["left_percent"])
        top_percent = float(params["top_percent"])
        width_percent = float(params["width_percent"])
        height_percent = float(params["height_percent"])
        size = driver.get_window_size()
        screen_width, screen_height = int(size["width"]), int(size["height"])
        left = min(max(0, round(screen_width * left_percent / 100)), screen_width - 1)
        top = min(max(0, round(screen_height * top_percent / 100)), screen_height - 1)
        # 百分比边界已由服务端校验；再按剩余像素夹紧，消除浮点/四舍五入造成的越界。
        width = min(max(1, round(screen_width * width_percent / 100)), screen_width - left)
        height = min(max(1, round(screen_height * height_percent / 100)), screen_height - top)
        driver.swipe_in_region(
            left,
            top,
            width,
            height,
            params.get("direction", "up"),
            float(params.get("percent", 0.3)),
        )
        return {"status": "passed"}


@register_action("scroll")
class ScrollAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.scroll_to(element),
            label="滚动到元素",
        )
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
        value = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_text(element),
            label="读取文本",
        )
        variable_name = params.get("variable_name")
        if variable_name:
            context.variables[variable_name] = value
        return {"status": "passed", "actual_value": value}


@register_action("get_attribute")
class GetAttributeAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        attribute = params.get("attribute", "")
        value = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_attribute(element, attribute),
            label="读取属性",
        )
        variable_name = params.get("variable_name")
        if variable_name:
            context.variables[variable_name] = value
        return {"status": "passed", "actual_value": value}


@register_action("tap_coordinate")
class TapCoordinateAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        driver.tap_coordinate(int(params.get("x", 0)), int(params.get("y", 0)))
        return {"status": "passed"}
