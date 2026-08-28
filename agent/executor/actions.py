import asyncio

from .driver import (
    ElementNotFound,
    ElementStaleRetryExhausted,
    StopRequested,
)
from .smart_locator import _java_string_escape
from .stale_guard import is_stale_element_error, with_stale_retry


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


@register_action("swipe_in_element_find_text_click")
class SwipeInElementFindTextClickAction(BaseAction):
    """在列表控件内双向滑动查找文字并点击（Step 12）。

    目标文字直接写在动作参数（支持 ${变量}），不进入元素库。
    匹配方式 equals/contains；先按首选方向查找，未找到再反向跨过起点继续。
    只接受中心点落在列表控件可见矩形内的匹配，点击距离列表中心最近的匹配项。
    找到后立即点击；如遇 stale，重新定位列表和目标重试，不能复用旧句柄。
    """

    _CLICK_STALE_RETRY_DELAYS = (0.2, 0.5)

    async def execute(self, driver, context, params: dict) -> dict:
        element_id = params.get("element_id")
        if element_id is None:
            raise ElementNotFound("缺少列表控件 element_id")
        target_text = params.get("target_text") or ""
        target_text = context.render(target_text).strip()
        if not target_text:
            raise ElementNotFound("目标文字不能为空")
        match_mode = params.get("match_mode") or "equals"
        preferred = params.get("preferred_direction") or "up"
        opposite = "down" if preferred == "up" else "up"
        max_swipes = int(params.get("max_swipes_per_direction", 8))
        percent = float(params.get("percent", 0.3))
        container_wait_timeout = params.get("container_wait_timeout", 10)
        settle_ms = int(params.get("settle_ms", 300))

        # UiAutomator selector，转义防注入
        method = "text" if match_mode == "equals" else "textContains"
        selector = f"new UiSelector().{method}(\"{_java_string_escape(target_text)}\")"

        done_swipes = 0
        preferred_swipes = 0
        last_click_error: BaseException | None = None

        # 阶段 1：首选方向，最多 max_swipes 次
        for i in range(max_swipes + 1):
            found, error = await self._try_find_and_click(
                driver, context, element_id, selector, container_wait_timeout,
            )
            if found:
                return self._make_result(done_swipes, target_text)
            if error is not None:
                last_click_error = error
            if i >= max_swipes:
                break
            if not await self._scroll_dir(
                driver, context, element_id, preferred, percent,
                container_wait_timeout, settle_ms,
            ):
                break
            done_swipes += 1
            preferred_swipes += 1

        # 阶段 2：反向，预算 = 首选实际滑动次数 + max_swipes（前半返回起点，后半探索另一侧）
        for i in range(preferred_swipes + max_swipes + 1):
            found, error = await self._try_find_and_click(
                driver, context, element_id, selector, container_wait_timeout,
            )
            if found:
                return self._make_result(done_swipes, target_text)
            if error is not None:
                last_click_error = error
            if i >= preferred_swipes + max_swipes:
                break
            if not await self._scroll_dir(
                driver, context, element_id, opposite, percent,
                container_wait_timeout, settle_ms,
            ):
                break
            done_swipes += 1

        raise ElementNotFound(self._failure_reason(
            element_id, target_text, match_mode, preferred, opposite,
            done_swipes, last_click_error,
        ))

    async def _try_find_and_click(
        self, driver, context, element_id, selector, container_wait_timeout,
    ) -> tuple[bool, BaseException | None]:
        """当前页面查找并点击目标；返回 (是否命中, 点击失败错误或 None)。"""
        stop = getattr(context, "should_stop", None)
        if stop is not None and stop():
            raise StopRequested("执行被用户停止")
        # 重新定位列表控件（智能定位临时禁滚）
        try:
            container = context.find_element(
                element_id, wait_timeout=container_wait_timeout, disable_smart_scroll=True
            )
        except ElementNotFound:
            return False, None
        target = self._find_target_in_container(driver, container, selector)
        if target is None:
            return False, None
        try:
            await self._click_with_stale_retry(driver, context, element_id, target, container_wait_timeout, selector)
        except ElementStaleRetryExhausted:
            # 点击 stale 重试耗尽：直接失败，不再继续滑动寻找其他同文字元素
            raise
        except Exception as exc:
            if is_stale_element_error(exc):
                return False, exc
            # 非 stale 的点击失败直接上抛（不继续寻找其他同文字元素）
            raise
        return True, None

    def _find_target_in_container(self, driver, container, selector):
        rect = driver.get_element_rect(container)
        size = driver.get_window_size()
        vis = _clip_rect(rect, size)
        matches = driver.find_elements("uiautomator", selector, wait_timeout=0)
        candidates = [m for m in matches if _center_in_rect(driver.get_element_rect(m), vis)]
        if not candidates:
            return None
        cx = vis["x"] + vis["width"] / 2
        cy = vis["y"] + vis["height"] / 2
        return min(candidates, key=lambda m: _center_distance(driver.get_element_rect(m), cx, cy))

    async def _click_with_stale_retry(self, driver, context, element_id, target, container_wait_timeout, selector):
        """点击目标；stale 时重新定位列表与目标后重试，耗尽抛 ElementStaleRetryExhausted。"""
        for attempt in range(len(self._CLICK_STALE_RETRY_DELAYS) + 1):
            stop = getattr(context, "should_stop", None)
            if stop is not None and stop():
                raise StopRequested("执行被用户停止")
            try:
                # 每次点击都用最新句柄（target 本身是本次查询结果，stale 后需重新查找）
                driver.click(target)
                return
            except Exception as exc:
                if not is_stale_element_error(exc):
                    raise
                if attempt >= len(self._CLICK_STALE_RETRY_DELAYS):
                    raise ElementStaleRetryExhausted(
                        f"点击失败：页面持续刷新导致目标元素失效，重新定位并重试 {len(self._CLICK_STALE_RETRY_DELAYS)} 次后仍未成功"
                    ) from exc
                delay = self._CLICK_STALE_RETRY_DELAYS[attempt]
                # 重新定位列表与目标
                container = context.find_element(
                    element_id, wait_timeout=container_wait_timeout, disable_smart_scroll=True
                )
                new_target = self._find_target_in_container(driver, container, selector)
                if new_target is None:
                    raise ElementNotFound("点击失败后重新定位目标文字未找到") from exc
                target = new_target
                await asyncio.sleep(delay)
        raise AssertionError("点击 stale 重试状态异常")

    async def _scroll_dir(self, driver, context, element_id, direction, percent, container_wait_timeout, settle_ms):
        stop = getattr(context, "should_stop", None)
        if stop is not None and stop():
            raise StopRequested("执行被用户停止")
        container = context.find_element(element_id, wait_timeout=container_wait_timeout, disable_smart_scroll=True)
        can_continue = driver.scroll_in_element(container, direction, percent)
        if settle_ms > 0:
            await asyncio.sleep(settle_ms / 1000.0)
        return can_continue

    def _make_result(self, done_swipes, target_text):
        return {
            "status": "passed",
            "found_after_swipes": done_swipes,
            "actual_value": f"在列表内滑动 {done_swipes} 次后找到并点击文字“{target_text}”",
        }

    def _failure_reason(self, element_id, target_text, match_mode, preferred, opposite, done_swipes, last_click_error):
        mode = "精确匹配" if match_mode == "equals" else "包含匹配"
        msg = f"目标文字“{target_text}”（{mode}）未在列表控件 {element_id} 中找到"
        msg += f"，首选方向 {preferred} 实际滑动、反方向 {opposite} 实际滑动合计 {done_swipes} 次"
        if last_click_error is not None:
            msg += f"，最后点击失败: {last_click_error}"
        return msg


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


# ---------- Step 12：列表内查找文字并点击的几何工具 ----------


def _clip_rect(rect: dict[str, int], size: dict[str, int]) -> dict[str, int]:
    """把控件矩形裁剪到当前屏幕可见区域，返回等宽/高矩形。"""
    screen_w = int(size.get("width", 0))
    screen_h = int(size.get("height", 0))
    x = max(0, int(rect.get("x", 0)))
    y = max(0, int(rect.get("y", 0)))
    right = min(int(rect.get("x", 0)) + int(rect.get("width", 0)), screen_w)
    bottom = min(int(rect.get("y", 0)) + int(rect.get("height", 0)), screen_h)
    width = max(0, right - x)
    height = max(0, bottom - y)
    return {"x": x, "y": y, "width": width, "height": height}


def _rect_center(rect: dict[str, int]) -> tuple[float, float]:
    return (
        int(rect.get("x", 0)) + int(rect.get("width", 0)) / 2,
        int(rect.get("y", 0)) + int(rect.get("height", 0)) / 2,
    )


def _center_in_rect(rect: dict[str, int], visible: dict[str, int]) -> bool:
    """目标中心点是否落在列表可见矩形内。"""
    cx, cy = _rect_center(rect)
    return (
        int(visible.get("x", 0)) <= cx <= int(visible.get("x", 0)) + int(visible.get("width", 0))
        and int(visible.get("y", 0)) <= cy <= int(visible.get("y", 0)) + int(visible.get("height", 0))
    )


def _center_distance(rect: dict[str, int], cx: float, cy: float) -> float:
    mx, my = _rect_center(rect)
    return (mx - cx) ** 2 + (my - cy) ** 2
