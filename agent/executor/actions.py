import asyncio
import logging

from .driver import (
    DriverError,
    ElementNotFound,
    ElementStaleRetryExhausted,
    StopRequested,
    _coerce_bool,
)
from .smart_locator import _java_string_escape
from .stale_guard import is_not_editable_error, is_stale_element_error, with_stale_retry

logger = logging.getLogger("agent.actions")


class BaseAction:
    async def execute(self, driver, context, params: dict) -> dict:
        raise NotImplementedError


def _run_editable_operation(
    driver, context, element_id, element, operation, wait_timeout
):
    """先操作 resource_id 目标，目标不可编辑时再尝试其 EditText 子节点。"""
    try:
        return operation(element)
    except Exception as exc:
        data = context.elements_snapshot.get(str(element_id))
        if (
            not data
            or data.get("locator_type") != "resource_id"
            or not is_not_editable_error(exc)
        ):
            raise
        fallback = context.find_element(
            element_id,
            wait_timeout=wait_timeout,
            editable=True,
            editable_suffix_only=True,
        )
        return operation(fallback)


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
        wait_timeout = params.get("wait_timeout")
        await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: _run_editable_operation(
                driver,
                context,
                params.get("element_id"),
                element,
                lambda target: driver.input(target, value, clear_first=clear_first),
                wait_timeout,
            ),
            wait_timeout=wait_timeout,
            editable=True,
            label="输入",
        )
        return {"status": "passed"}


@register_action("clear")
class ClearAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        wait_timeout = params.get("wait_timeout")
        await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: _run_editable_operation(
                driver,
                context,
                params.get("element_id"),
                element,
                driver.clear,
                wait_timeout,
            ),
            wait_timeout=wait_timeout,
            editable=True,
            label="清空",
        )
        return {"status": "passed"}


@register_action("set_checked")
class SetCheckedAction(BaseAction):
    """将 checkbox 设置为目标状态；状态一致时保持不点击。"""

    async def execute(self, driver, context, params: dict) -> dict:
        desired = _coerce_bool(params.get("checked", True))
        changed = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: _set_checked(driver, element, desired),
            wait_timeout=params.get("wait_timeout"),
            label="设置勾选状态",
        )
        return {
            "status": "passed",
            "expected": "checked" if desired else "unchecked",
            "changed": changed,
        }


def _set_checked(driver, element, desired: bool) -> bool:
    current = driver.is_checked(element)
    if current == desired:
        return False
    driver.click(element)
    return True


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
    每次手势调用即计入次数，首方向固定执行 max_swipes_per_direction 次，
    未找到目标后反方向固定执行两倍该次数；页面签名只用于日志，不作为提前
    停止条件。
    只接受中心点及安全边距落在 ListView 直接父元素视口内的匹配，
    点击距离父元素中心最近的匹配项。
    找到后立即点击；如遇 stale，重新定位列表和目标重试，不能复用旧句柄。
    """

    _CLICK_STALE_RETRY_DELAYS = (0.2, 0.5)
    _OBSERVE_STALE_RETRY_DELAYS = (0.05, 0.15)

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
        duration_ms = int(params.get("duration_ms", 300))
        if duration_ms <= 0:
            raise DriverError("滑动时长 duration_ms 必须为正数")

        # UiAutomator selector，转义防注入
        method = "text" if match_mode == "equals" else "textContains"
        selector = f"new UiSelector().{method}(\"{_java_string_escape(target_text)}\")"

        done_swipes = 0
        phase_swipes = 0
        reverse_swipes = 0
        swipe_counts = {preferred: 0, opposite: 0}
        last_click_error: BaseException | None = None

        # 阶段 1：固定使用首选方向，必须执行满 max_swipes 次；
        # changed/signature 和目标位置 hint 只用于诊断，不提前结束或改向。
        logger.info(
            "列表文字查找开始: element_id=%s target=%r preferred=%s max_per_direction=%s",
            element_id,
            target_text,
            preferred,
                max_swipes,
            )
        while phase_swipes < max_swipes:
            found, error, hint = await self._try_find_and_click(
                driver, context, element_id,
                selector, container_wait_timeout,
            )
            if found:
                logger.info(
                    "列表文字查找成功: phase=forward swipes=%s target=%r",
                    done_swipes,
                    target_text,
                )
                return self._make_result(done_swipes, target_text)
            if error is not None:
                last_click_error = error
            logger.info(
                "列表观测: phase=forward count=%s/%s hint=%s ignored=true",
                phase_swipes,
                max_swipes,
                hint or "none",
            )
            direction = preferred
            logger.info(
                "执行列表滑动: phase=forward direction=%s count=%s/%s total=%s",
                direction,
                phase_swipes + 1,
                max_swipes,
                done_swipes + 1,
            )
            changed = await self._scroll_dir(
                driver, context, element_id,
                direction, percent, duration_ms, container_wait_timeout, settle_ms,
            )
            done_swipes += 1
            phase_swipes += 1
            swipe_counts[direction] += 1
            logger.info(
                "列表滑动完成: phase=forward direction=%s count=%s changed=%s next=%s",
                direction,
                swipe_counts[direction],
                changed,
                "forward" if phase_swipes < max_swipes else "reverse",
            )
            if phase_swipes >= max_swipes:
                logger.info(
                    "正向阶段完成固定次数: direction=%s total=%s signature_changed=%s",
                    direction,
                    done_swipes,
                    changed,
                )
            found, error, hint = await self._try_find_and_click(
                driver, context, element_id,
                selector, container_wait_timeout,
            )
            if found:
                logger.info(
                    "列表文字查找成功: phase=forward-after-swipe swipes=%s target=%r",
                    done_swipes,
                    target_text,
                )
                return self._make_result(done_swipes, target_text)
            if error is not None:
                last_click_error = error

        # 阶段 2：固定使用反方向，执行正向预算 + max_swipes 次。
        # 例如 max_swipes=8 时，正向 8 次、反向 16 次；仍只在找到目标后提前结束。
        reverse_direction = opposite
        reverse_budget = max_swipes * 2
        logger.info(
            "切换反向阶段: from=%s to=%s forward_swipes=%s reverse_budget=%s",
            preferred,
            reverse_direction,
            phase_swipes,
            reverse_budget,
        )
        while reverse_swipes < reverse_budget:
            found, error, hint = await self._try_find_and_click(
                driver, context, element_id,
                selector, container_wait_timeout,
            )
            if found:
                logger.info(
                    "列表文字查找成功: phase=reverse swipes=%s target=%r",
                    done_swipes,
                    target_text,
                )
                return self._make_result(done_swipes, target_text)
            if error is not None:
                last_click_error = error
            logger.info(
                "列表观测: phase=reverse count=%s/%s hint=%s ignored=true",
                reverse_swipes,
                reverse_budget,
                hint or "none",
            )
            direction = reverse_direction
            logger.info(
                "执行列表滑动: phase=reverse direction=%s count=%s/%s total=%s",
                direction,
                reverse_swipes + 1,
                reverse_budget,
                done_swipes + 1,
            )
            changed = await self._scroll_dir(
                driver, context, element_id,
                direction, percent, duration_ms, container_wait_timeout, settle_ms,
            )
            done_swipes += 1
            reverse_swipes += 1
            swipe_counts[direction] += 1
            logger.info(
                "列表滑动完成: phase=reverse direction=%s count=%s changed=%s",
                direction,
                swipe_counts[direction],
                changed,
            )
            if reverse_swipes >= reverse_budget:
                logger.info(
                    "反向阶段完成固定次数: direction=%s total=%s signature_changed=%s",
                    direction,
                    done_swipes,
                    changed,
                )
            found, error, hint = await self._try_find_and_click(
                driver, context, element_id,
                selector, container_wait_timeout,
            )
            if found:
                logger.info(
                    "列表文字查找成功: phase=reverse-after-swipe swipes=%s target=%r",
                    done_swipes,
                    target_text,
                )
                return self._make_result(done_swipes, target_text)
            if error is not None:
                last_click_error = error

        logger.warning(
            "列表文字查找失败: target=%r total_swipes=%s counts=%s",
            target_text,
            done_swipes,
            swipe_counts,
        )
        raise ElementNotFound(self._failure_reason(
            element_id, target_text, match_mode, preferred, opposite,
            done_swipes, last_click_error,
        ))

    async def _try_find_and_click(
        self, driver, context, element_id,
        selector, container_wait_timeout,
    ) -> tuple[bool, BaseException | None, str | None]:
        """只在 ListView 后代查找目标，并按视口位置返回建议滑动方向。"""
        stop = getattr(context, "should_stop", None)
        if stop is not None and stop():
            raise StopRequested("执行被用户停止")
        observation = await self._observe_target(
            driver, context, element_id,
            selector, container_wait_timeout,
        )
        if observation is None:
            # 页面持续重绘时本轮观测未知，交给外层继续消耗滑动次数。
            return False, None, None
        _container, _parent, _parent_region, target, direction = observation
        if target is None:
            return False, None, direction
        if direction is not None:
            return False, None, direction
        try:
            clicked, click_direction = await self._click_with_stale_retry(
                driver, context, element_id, target,
                container_wait_timeout, selector,
            )
            if not clicked:
                return False, None, click_direction
        except ElementStaleRetryExhausted:
            # 点击 stale 重试耗尽：直接失败，不再继续滑动寻找其他同文字元素
            raise
        except Exception as exc:
            if is_stale_element_error(exc):
                return False, exc, direction
            # 非 stale 的点击失败直接上抛（不继续寻找其他同文字元素）
            raise
        return True, None, None

    async def _observe_target(
        self, driver, context, element_id,
        selector, wait_timeout,
    ):
        """重新定位并观测列表目标，stale 时丢弃旧句柄后重试。"""
        delays = self._OBSERVE_STALE_RETRY_DELAYS
        for attempt in range(len(delays) + 1):
            try:
                container, parent, parent_region = self._locate_container_and_parent(
                    driver, context, element_id, wait_timeout
                )
                target, direction = self._find_target_in_container(
                    driver, container, parent_region, selector
                )
                return container, parent, parent_region, target, direction
            except Exception as exc:
                if not is_stale_element_error(exc):
                    raise
                if attempt >= len(delays):
                    return None
                context.invalidate_element(element_id)
                await asyncio.sleep(delays[attempt])
        return None

    def _locate_container_and_parent(
        self, driver, context, element_id, wait_timeout
    ):
        try:
            container = context.find_element(
                element_id, wait_timeout=wait_timeout, disable_smart_scroll=True
            )
        except ElementNotFound as exc:
            raise ElementNotFound(f"ListView 未找到: element_id={element_id}") from exc
        try:
            parent = driver.get_parent_element(container, wait_timeout=wait_timeout)
        except ElementNotFound as exc:
            raise ElementNotFound(
                "ListView 没有直接父元素，无法确定父元素视口"
            ) from exc
        visible = self._parent_region(driver, parent)
        if visible["width"] <= 0 or visible["height"] <= 0:
            raise ElementNotFound("ListView 的直接父元素没有有效可见区域")
        return container, parent, visible

    @staticmethod
    def _parent_region(driver, parent):
        size = driver.get_window_size()
        return _clip_rect(driver.get_element_rect(parent), size)

    def _find_target_in_container(self, driver, container, visible, selector):
        matches = driver.find_elements_in_element(container, "uiautomator", selector, wait_timeout=0)
        candidates = [m for m in matches if _is_displayed_and_enabled(driver, m)]
        safe = [m for m in candidates if _is_safely_clickable(driver, m, visible)]
        if safe:
            cx = visible["x"] + visible["width"] / 2
            cy = visible["y"] + visible["height"] / 2
            return min(safe, key=lambda m: _center_distance(driver.get_element_rect(m), cx, cy)), None
        if not candidates:
            return None, None
        return min(
            candidates,
            key=lambda m: _distance_to_rect(driver.get_element_rect(m), visible),
        ), _direction_to_reveal(driver.get_element_rect(
            min(candidates, key=lambda m: _distance_to_rect(driver.get_element_rect(m), visible))
        ), visible)

    async def _click_with_stale_retry(
        self, driver, context, element_id, target,
        container_wait_timeout, selector,
    ):
        """每次点击前重新确认目标仍在安全视口内；stale 时等待后再重试。"""
        for attempt in range(len(self._CLICK_STALE_RETRY_DELAYS) + 1):
            stop = getattr(context, "should_stop", None)
            if stop is not None and stop():
                raise StopRequested("执行被用户停止")
            try:
                observation = await self._observe_target(
                    driver, context, element_id, selector,
                    container_wait_timeout,
                )
                if observation is None:
                    return False, None
                container, parent, visible, target, direction = observation
                if target is None:
                    return False, direction
                if direction is not None:
                    return False, direction
                # 观测后再次校验，覆盖定位到点击之间的视口变化。
                if not _is_safely_clickable(driver, target, visible):
                    return False, _direction_to_reveal(driver.get_element_rect(target), visible)
                driver.click(target)
                return True, None
            except Exception as exc:
                if not is_stale_element_error(exc):
                    if is_not_editable_error(exc):
                        observation = await self._observe_target(
                            driver, context, element_id, selector,
                            container_wait_timeout,
                        )
                        if observation is None:
                            return False, None
                        container, parent, visible, new_target, direction = observation
                        if (
                            new_target is not None
                            and direction is None
                            and _is_safely_clickable(driver, new_target, visible)
                        ):
                            rect = driver.get_element_rect(new_target)
                            driver.tap_coordinate(*_rect_center_int(rect))
                            return True, None
                        if direction is not None:
                            return False, direction
                    raise
                if attempt >= len(self._CLICK_STALE_RETRY_DELAYS):
                    raise ElementStaleRetryExhausted(
                        f"点击失败：页面持续刷新导致目标元素失效，重新定位并重试 {len(self._CLICK_STALE_RETRY_DELAYS)} 次后仍未成功"
                    ) from exc
                # 页面重绘后先让 UI 稳定，再重新定位；不能在等待前复用旧句柄。
                await asyncio.sleep(self._CLICK_STALE_RETRY_DELAYS[attempt])
                context.invalidate_element(element_id)
        raise AssertionError("点击 stale 重试状态异常")

    async def _scroll_dir(
        self, driver, context, element_id, direction,
        percent, duration_ms, container_wait_timeout, settle_ms,
    ):
        stop = getattr(context, "should_stop", None)
        if stop is not None and stop():
            raise StopRequested("执行被用户停止")

        try:
            before_signature = await self._observe_list_signature(
                driver, context, element_id,
                container_wait_timeout
            )
        except ElementNotFound as exc:
            raise ElementNotFound(f"ListView 未找到: element_id={element_id}") from exc

        def _do_scroll(container):
            # 每次手势都从本轮新定位的 container 解析第一层父元素，
            # 父元素句柄不能跨页面重绘复用；滑动距离统一按父视口高度计算。
            parent = driver.get_parent_element(container, wait_timeout=container_wait_timeout)
            region = self._parent_region(driver, parent)
            if region["width"] <= 0 or region["height"] <= 0:
                raise ElementNotFound("ListView 的直接父元素没有有效可见区域")
            list_rect = driver.get_element_rect(container)
            list_height = int(list_rect.get("height", 0))
            parent_height = int(region["height"])
            if list_height <= 0:
                raise ElementNotFound("ListView 没有有效高度，无法计算滑动距离")
            configured_percent = max(0.0, float(percent))
            target_distance_px = min(
                parent_height,
                max(1, round(parent_height * configured_percent)),
            )
            speed = max(1, round(target_distance_px * 1000 / duration_ms))
            effective_percent = target_distance_px / list_height
            if direction == "up":
                effective_percent = min(1.0, max(0.001, effective_percent))
                logger.info(
                    "列表元素内向上滑动: element_id=%s direction=%s list_height=%s "
                    "parent_height=%s configured_percent=%s effective_percent=%s "
                    "target_distance_px=%s duration_ms=%s speed=%s",
                    element_id,
                    direction,
                    list_height,
                    parent_height,
                    configured_percent,
                    effective_percent,
                    target_distance_px,
                    duration_ms,
                    speed,
                )
                return driver.swipe_in_element(
                    container, direction, effective_percent, speed=speed
                )
            logger.info(
                "父元素区域内向下滑动: element_id=%s direction=%s configured_percent=%s "
                "list_height=%s parent_height=%s effective_percent=%s "
                "target_distance_px=%s duration_ms=%s speed=%s region=(%s,%s,%s,%s)",
                element_id,
                direction,
                configured_percent,
                list_height,
                parent_height,
                configured_percent,
                target_distance_px,
                duration_ms,
                speed,
                region["x"],
                region["y"],
                region["width"],
                region["height"],
            )
            return driver.swipe_in_region(
                region["x"],
                region["y"],
                region["width"],
                region["height"],
                direction,
                configured_percent,
                speed=speed,
            )

        await with_stale_retry(
            driver,
            context,
            element_id,
            _do_scroll,
            wait_timeout=container_wait_timeout,
            disable_smart_scroll=True,
            label=f"列表内滚动（{direction}）",
        )
        if settle_ms > 0:
            await asyncio.sleep(settle_ms / 1000.0)
        try:
            after_signature = await self._observe_list_signature(
                driver, context, element_id,
                container_wait_timeout
            )
        except ElementNotFound as exc:
            raise ElementNotFound(f"ListView 未找到: element_id={element_id}") from exc
        if before_signature is None or after_signature is None:
            return True
        return before_signature != after_signature

    async def _observe_list_signature(
        self, driver, context, element_id, wait_timeout
    ):
        """读取局部列表签名；stale 时重新定位，最终未知则交给次数预算处理。"""
        delays = self._OBSERVE_STALE_RETRY_DELAYS
        for attempt in range(len(delays) + 1):
            try:
                container, _parent, parent_region = self._locate_container_and_parent(
                    driver, context, element_id, wait_timeout
                )
                return self._read_list_signature(driver, container, parent_region)
            except Exception as exc:
                if not is_stale_element_error(exc):
                    raise
                if attempt >= len(delays):
                    return None
                context.invalidate_element(element_id)
                await asyncio.sleep(delays[attempt])
        return None

    @classmethod
    def _read_list_signature(cls, driver, container, parent_region):
        try:
            nodes = driver.find_elements_in_element(
                container, "uiautomator", "new UiSelector()", wait_timeout=0
            )
            snapshot = []
            for node in nodes:
                rect = driver.get_element_rect(node)
                if _intersection_area(rect, parent_region) <= 0:
                    continue
                snapshot.append((
                    getattr(node, "text", "") or driver.get_attribute(node, "text"),
                    rect.get("x"), rect.get("y"), rect.get("width"), rect.get("height"),
                ))
            return tuple(snapshot)
        except (NotImplementedError, ElementNotFound):
            return None

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


def _intersect_rect(first: dict[str, int], second: dict[str, int]) -> dict[str, int]:
    left = max(int(first.get("x", 0)), int(second.get("x", 0)))
    top = max(int(first.get("y", 0)), int(second.get("y", 0)))
    right = min(
        int(first.get("x", 0)) + int(first.get("width", 0)),
        int(second.get("x", 0)) + int(second.get("width", 0)),
    )
    bottom = min(
        int(first.get("y", 0)) + int(first.get("height", 0)),
        int(second.get("y", 0)) + int(second.get("height", 0)),
    )
    return {"x": left, "y": top, "width": max(0, right - left), "height": max(0, bottom - top)}


def _rect_center(rect: dict[str, int]) -> tuple[float, float]:
    return (
        int(rect.get("x", 0)) + int(rect.get("width", 0)) / 2,
        int(rect.get("y", 0)) + int(rect.get("height", 0)) / 2,
    )


def _rect_center_int(rect: dict[str, int]) -> tuple[int, int]:
    cx, cy = _rect_center(rect)
    return round(cx), round(cy)


def _rect_area(rect: dict[str, int]) -> int:
    return max(0, int(rect.get("width", 0))) * max(0, int(rect.get("height", 0)))


def _intersection_area(first: dict[str, int], second: dict[str, int]) -> int:
    return _rect_area(_intersect_rect(first, second))


def _is_displayed_and_enabled(driver, element) -> bool:
    for method_name, attribute_name in (("is_displayed", "displayed"), ("is_enabled", "enabled")):
        method = getattr(element, method_name, None)
        if callable(method):
            try:
                if not method():
                    return False
                continue
            except Exception:
                pass
        getter = getattr(driver, "get_attribute", None)
        if callable(getter):
            try:
                value = getter(element, attribute_name)
            except Exception:
                value = ""
            if value not in ("", None) and str(value).lower() == "false":
                return False
    return True


def _is_safely_clickable(driver, element, visible: dict[str, int]) -> bool:
    rect = driver.get_element_rect(element)
    if _rect_area(rect) <= 0:
        return False
    if _intersection_area(rect, visible) / _rect_area(rect) < 0.8:
        return False
    cx, cy = _rect_center(rect)
    margin = max(4, min(int(rect.get("height", 0)) // 4, 24))
    return (
        visible["x"] + margin <= cx <= visible["x"] + visible["width"] - margin
        and visible["y"] + margin <= cy <= visible["y"] + visible["height"] - margin
    )


def _distance_to_rect(rect: dict[str, int], visible: dict[str, int]) -> float:
    if _intersection_area(rect, visible) > 0:
        return 0
    cx, cy = _rect_center(rect)
    left, top = visible["x"], visible["y"]
    right, bottom = left + visible["width"], top + visible["height"]
    dx = max(left - cx, 0, cx - right)
    dy = max(top - cy, 0, cy - bottom)
    return dx * dx + dy * dy


def _direction_to_reveal(rect: dict[str, int], visible: dict[str, int]) -> str | None:
    top = int(rect.get("y", 0))
    bottom = top + int(rect.get("height", 0))
    visible_top = int(visible.get("y", 0))
    visible_bottom = visible_top + int(visible.get("height", 0))
    if top >= visible_bottom:
        return "up"
    if bottom <= visible_top:
        return "down"
    visible_area = _intersection_area(rect, visible)
    full_area = _rect_area(rect)
    if full_area <= 0:
        return None
    above = max(0, visible_top - top)
    below = max(0, bottom - visible_bottom)
    if visible_area / full_area >= 0.8:
        _cx, cy = _rect_center(rect)
        margin = max(4, min(int(rect.get("height", 0)) // 4, 24))
        if cy < visible_top + margin:
            return "down"
        if cy > visible_bottom - margin:
            return "up"
        return None
    return "down" if above > below else "up"


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
