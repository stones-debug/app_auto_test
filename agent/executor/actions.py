import asyncio
import json
import logging
import math
import re
import time

from .appium_driver import _xpath_literal
from .driver import (
    DriverError,
    ElementNotFound,
    ElementStaleRetryExhausted,
    StopRequested,
    _coerce_bool,
)
from .stale_guard import is_not_editable_error, is_stale_element_error, with_stale_retry

logger = logging.getLogger("agent.actions")


def _diagnostic_value(value, *, limit: int = 200):
    """Return a bounded, log-safe representation for action diagnostics."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
    else:
        text = repr(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


def _read_element_attribute(element, name: str, errors: list[str]):
    try:
        getter = getattr(element, "get_attribute", None)
        if not callable(getter):
            return None
        return getter(name)
    except Exception:
        errors.append(f"attribute:{name}")
        return None


def _read_object_attribute(obj, name: str, errors: list[str], label: str):
    try:
        return getattr(obj, name, None)
    except Exception:
        errors.append(f"{label}:{name}")
        return None


def _context_reference(context, name: str, params: dict | None = None):
    """Read optional execution references without requiring a context contract change."""
    value = getattr(context, name, None)
    if value is None:
        case = getattr(context, "case", None)
        if isinstance(case, dict):
            value = case.get(name)
    if value is None and isinstance(params, dict):
        value = params.get(name)
    return value


def _smart_locator_summary(data: dict) -> str:
    config = data.get("locator_config")
    if not isinstance(config, dict):
        return "smart"
    alternatives = config.get("alternatives")
    if not isinstance(alternatives, list):
        return "smart"
    target_parts: list[str] = []
    for alternative in alternatives[:3]:
        if not isinstance(alternative, dict):
            continue
        for condition in alternative.get("target") or []:
            if not isinstance(condition, dict):
                continue
            attribute = condition.get("attribute")
            operator = condition.get("operator")
            value = _diagnostic_value(condition.get("value"), limit=80)
            if attribute and operator:
                target_parts.append(f"{attribute}{operator}{value}")
    suffix = f" targets={','.join(target_parts)}" if target_parts else ""
    return f"smart alternatives={len(alternatives)}{suffix}"


def _element_diagnostics(driver, context, element_id, element, *, action: str, params: dict | None = None) -> dict:
    """Collect best-effort identity/geometry data without affecting the action."""
    errors: list[str] = []
    snapshot = _read_object_attribute(context, "elements_snapshot", errors, "context") or {}
    data = snapshot.get(str(element_id)) if isinstance(snapshot, dict) else {}
    data = data if isinstance(data, dict) else {}
    locator_type = data.get("locator_type")
    locator_value = data.get("locator_value")
    if locator_type == "smart":
        locator_value = _smart_locator_summary(data)

    real_driver = _read_object_attribute(driver, "driver", errors, "driver") or driver
    try:
        appium_context = getattr(real_driver, "current_context", None)
    except Exception:
        errors.append("driver:current_context")
        appium_context = None

    try:
        rect = driver.get_element_rect(element)
    except Exception:
        errors.append("driver:rect")
        rect = None
    rect = rect if isinstance(rect, dict) else None
    center = None
    if rect is not None:
        try:
            center = {
                "x": int(rect.get("x", 0)) + int(rect.get("width", 0)) / 2,
                "y": int(rect.get("y", 0)) + int(rect.get("height", 0)) / 2,
            }
        except (TypeError, ValueError):
            errors.append("rect:center")

    bounds = _read_element_attribute(element, "bounds", errors)
    if bounds in (None, ""):
        bounds = _read_object_attribute(element, "_bounds", errors, "element")
    if bounds in (None, "") and rect is not None:
        # W3C rect is the only geometry some Appium backends expose; label it as
        # a best-effort bounds fallback rather than claiming XML bounds were read.
        bounds = rect

    result = {
        "event": "element_diagnostic",
        "action": action,
        "element_id": element_id,
        "locator_type": locator_type,
        "locator_value": _diagnostic_value(locator_value),
        "appium_context": _diagnostic_value(appium_context),
        "remote_element_id": _diagnostic_value(
            _read_object_attribute(element, "id", errors, "element")
        ),
        "class": _diagnostic_value(_read_element_attribute(element, "className", errors)
                                   or _read_element_attribute(element, "class", errors)),
        "text": _diagnostic_value(_read_element_attribute(element, "text", errors)),
        "content_desc": _diagnostic_value(_read_element_attribute(element, "content-desc", errors)),
        "resource_id": _diagnostic_value(_read_element_attribute(element, "resource-id", errors)),
        "displayed": _read_element_attribute(element, "displayed", errors),
        "enabled": _read_element_attribute(element, "enabled", errors),
        "rect": rect,
        "bounds": bounds,
        "element_center_point": center,
    }
    for reference in ("execution_node_id", "execution_step_id", "execution_case_id", "execution_suite_id"):
        value = _context_reference(context, reference, params)
        if value is not None:
            result[reference] = value
    if errors:
        result["diagnostic_errors"] = errors
    return result


def _log_diagnostic(level: int, payload: dict) -> None:
    """Log JSON diagnostics; never let formatting/serialization affect actions."""
    try:
        logger.log(level, "元素诊断 %s", json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    except Exception:
        logger.log(level, "元素诊断记录失败（不影响动作）")


def _swipe_geometry(driver, direction: str, percent: float | None) -> dict:
    """Mirror AppiumDriver.swipe's direction-to-pixel conversion for diagnostics."""
    try:
        size = driver.get_window_size()
        width, height = int(size["width"]), int(size["height"])
        if percent is None:
            points = {
                "up": ((width // 2, int(height * 0.8)), (width // 2, int(height * 0.2))),
                "down": ((width // 2, int(height * 0.2)), (width // 2, int(height * 0.8))),
                "left": ((int(width * 0.8), height // 2), (int(width * 0.2), height // 2)),
                "right": ((int(width * 0.2), height // 2), (int(width * 0.8), height // 2)),
            }
        else:
            half_width = width * percent / 2
            half_height = height * percent / 2
            center_x, center_y = width / 2, height / 2
            points = {
                "up": ((round(center_x), round(center_y + half_height)),
                       (round(center_x), round(center_y - half_height))),
                "down": ((round(center_x), round(center_y - half_height)),
                         (round(center_x), round(center_y + half_height))),
                "left": ((round(center_x + half_width), round(center_y)),
                         (round(center_x - half_width), round(center_y))),
                "right": ((round(center_x - half_width), round(center_y)),
                          (round(center_x + half_width), round(center_y))),
            }
        start, end = points.get(direction, points["up"])
        return {
            "window_width": width,
            "window_height": height,
            "start_x": start[0],
            "start_y": start[1],
            "end_x": end[0],
            "end_y": end[1],
            "coordinate_source": "driver.swipe direction conversion (mirrors AppiumDriver.swipe)",
        }
    except Exception as exc:
        return {
            "coordinate_source": "driver.swipe(direction, duration, percent)",
            "coordinate_error": _diagnostic_value(str(exc)),
        }


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
        element_id = params.get("element_id")

        def click_with_diagnostics(element):
            diagnostic = _element_diagnostics(
                driver, context, element_id, element, action="click", params=params
            )
            diagnostic["expected_calculated_click_point"] = diagnostic.get("element_center_point")
            diagnostic["actual_touch_point"] = (
                "unknown: element.click does not expose the Appium touch coordinates"
            )
            diagnostic["click_method"] = "element.click"
            diagnostic["click_phase"] = "before"
            _log_diagnostic(logging.INFO, diagnostic)
            # Keep the real click exception visible to stale_guard; diagnostics
            # are best-effort and must never turn a click failure into a pass.
            result = driver.click(element)
            diagnostic["click_phase"] = "after"
            diagnostic["click_status"] = "passed"
            _log_diagnostic(logging.INFO, diagnostic)
            return result

        await with_stale_retry(
            driver,
            context,
            element_id,
            click_with_diagnostics,
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


_SLIDER_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _parse_slider_number(raw: object) -> float | None:
    """仅从滑块按钮自身文本中保守地解析一个数值。"""
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip().replace(",", "")
    matches = _SLIDER_NUMBER_RE.findall(text)
    if len(matches) != 1:
        return None
    try:
        value = float(matches[0])
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _read_slider_value(
    driver,
    slider,
) -> float:
    value = _parse_slider_number(driver.get_text(slider))
    if value is None:
        raise DriverError("无法从滑块按钮自身文本解析当前值；文本必须恰好包含一个数字")
    return value


def _slider_snapshot(driver, slider, *, minimum: float, maximum: float, wait_timeout: float):
    """Read a fresh slider, its two parents, and the current track geometry."""
    current = _read_slider_value(driver, slider)
    slider_rect = driver.get_element_rect(slider)
    try:
        parent = driver.get_parent_element(slider, wait_timeout=wait_timeout)
    except ElementNotFound as exc:
        raise ElementNotFound("滑块按钮缺少一级父节点") from exc
    try:
        track = driver.get_parent_element(parent, wait_timeout=wait_timeout)
    except ElementNotFound as exc:
        raise ElementNotFound("滑块按钮缺少二级父节点（轨道）") from exc
    track_rect = driver.get_element_rect(track)
    track_x = int(track_rect.get("x", 0))
    track_y = int(track_rect.get("y", 0))
    track_width = int(track_rect.get("width", 0))
    track_height = int(track_rect.get("height", 0))
    thumb_width = int(slider_rect.get("width", 0))
    thumb_height = int(slider_rect.get("height", 0))
    if track_width <= 1 or track_height <= 0:
        raise DriverError(f"滑块轨道无效: width={track_width}, height={track_height}")
    if thumb_width <= 0 or thumb_height <= 0:
        raise DriverError(f"滑块按钮无效: width={thumb_width}, height={thumb_height}")
    # The second parent is the thin track itself.  Its endpoints are the
    # complete travel for the thumb center; the thumb is expected to extend
    # beyond the track at the minimum/maximum values.
    left = track_x
    right = track_x + track_width - 1
    if right <= left:
        raise DriverError("滑块轨道有效水平行程不足")
    start_x = int(slider_rect.get("x", 0)) + (thumb_width - 1) // 2
    start_y = int(slider_rect.get("y", 0)) + (thumb_height - 1) // 2
    track_bottom = track_y + track_height - 1
    if not left <= start_x <= right or not track_y <= start_y <= track_bottom:
        raise ElementNotFound("滑块按钮不在二级父节点轨道的有效范围内")
    if not minimum <= current <= maximum:
        raise DriverError(f"滑块当前值 {current:g} 不在配置范围 {minimum:g}～{maximum:g} 内")
    return {
        "current": current,
        "start_x": start_x,
        "start_y": start_y,
        "left": left,
        "right": right,
    }


def _move_slider(
    driver,
    slider,
    *,
    target_value: float,
    minimum: float,
    maximum: float,
    tolerance: float,
    wait_timeout: float,
    duration_ms: int,
) -> tuple[str, float]:
    snapshot = _slider_snapshot(
        driver, slider, minimum=minimum, maximum=maximum, wait_timeout=wait_timeout
    )
    current = snapshot["current"]
    if abs(current - target_value) <= tolerance:
        return "noop", current
    effective_span = snapshot["right"] - snapshot["left"]
    delta = effective_span * (target_value - current) / (maximum - minimum)
    move_px = round(delta)
    if move_px == 0:
        move_px = 1 if delta > 0 else -1
    end_x = max(snapshot["left"], min(snapshot["right"], snapshot["start_x"] + move_px))
    if end_x == snapshot["start_x"]:
        raise DriverError("滑块目标位移被轨道边界夹紧为零，无法继续拖动")
    driver.drag_coordinate(
        snapshot["start_x"], snapshot["start_y"], end_x, snapshot["start_y"], duration_ms
    )
    return "drag", current


@register_action("set_slider_value")
class SetSliderValueAction(BaseAction):
    """通过滑块按钮自身文本和二级父节点轨道，按数值比例拖动并校正。"""

    async def execute(self, driver, context, params: dict) -> dict:
        def finite_float(name: str, default=None) -> float:
            raw = params.get(name, default)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise DriverError(f"滑块参数 {name} 必须是数字") from None
            if not math.isfinite(value):
                raise DriverError(f"滑块参数 {name} 必须是有限数字")
            return value

        def bounded_int(name: str, default: int, minimum_bound: int, maximum_bound: int) -> int:
            raw = params.get(name, default)
            if isinstance(raw, bool):
                raise DriverError(f"滑块参数 {name} 必须是整数")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise DriverError(f"滑块参数 {name} 必须是整数") from None
            if not math.isfinite(value) or not value.is_integer():
                raise DriverError(f"滑块参数 {name} 必须是整数")
            result = int(value)
            if not minimum_bound <= result <= maximum_bound:
                raise DriverError(
                    f"滑块参数 {name} 必须在 {minimum_bound}～{maximum_bound} 之间"
                )
            return result

        minimum = finite_float("min_value")
        maximum = finite_float("max_value")
        target = finite_float("target_value")
        if minimum >= maximum:
            raise DriverError("滑块最小值必须小于最大值")
        if not minimum <= target <= maximum:
            raise DriverError("滑块目标值必须在最小值与最大值之间")

        element_id = params.get("element_id")
        duration_ms = bounded_int("duration_ms", 300, 1, 10000)
        settle_ms = bounded_int("settle_ms", 300, 0, 5000)
        tolerance = finite_float("tolerance", 0)
        if tolerance < 0:
            raise DriverError("滑块参数 tolerance 不能小于 0")
        max_adjustments = bounded_int("max_adjustments", 3, 0, 10)
        wait_timeout = bounded_int("wait_timeout", 10, 0, 300)
        settle_seconds = settle_ms / 1000

        async def sleep_with_stop(seconds: float) -> None:
            end = asyncio.get_running_loop().time() + max(0.0, seconds)
            while True:
                stop = getattr(context, "should_stop", None)
                if stop is not None and stop():
                    raise StopRequested("执行被用户停止")
                remaining = end - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return
                await asyncio.sleep(min(0.05, remaining))

        actual: float | None = None
        changed = False
        for adjustment in range(max_adjustments + 1):
            interaction, current = await with_stale_retry(
                driver,
                context,
                element_id,
                lambda slider: _move_slider(
                    driver,
                    slider,
                    target_value=target,
                    minimum=minimum,
                    maximum=maximum,
                    tolerance=tolerance,
                    wait_timeout=wait_timeout,
                    duration_ms=duration_ms,
                ),
                wait_timeout=wait_timeout,
                label="设置滑块数值",
            )
            actual = current
            if interaction == "noop":
                return {
                    "status": "passed",
                    "actual_value": f"{current:g}",
                    "target_value": target,
                    "changed": changed,
                    "verified": True,
                    "interaction": "drag" if changed else "noop",
                    "adjustments": adjustment if changed else 0,
                }
            changed = True
            if settle_seconds:
                await sleep_with_stop(settle_seconds)
            actual = await with_stale_retry(
                driver,
                context,
                element_id,
                lambda slider: _read_slider_value(driver, slider),
                wait_timeout=wait_timeout,
                label="读取滑块数值",
            )
            if abs(actual - target) <= tolerance:
                return {
                    "status": "passed",
                    "actual_value": f"{actual:g}",
                    "target_value": target,
                    "changed": True,
                    "verified": True,
                    "interaction": interaction,
                    "adjustments": adjustment,
                }

        raise DriverError(
            f"设置滑块失败：目标值 {target:g}，实际值 {actual:g}，"
            f"已修正 {max_adjustments} 次"
        )


@register_action("swipe")
class SwipeAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        driver.swipe(params.get("direction", "up"), duration=params.get("duration", 500))
        return {"status": "passed"}


@register_action("swipe_to_find")
class SwipeToFindAction(BaseAction):
    """上下滑动页面直至找到目标元素（最多 max_swipes 次滑动）。

    找到返回 passed + found_after_swipes（0 表示未滑动即找到）；
    滑完仍未找到抛 ElementNotFound（步骤失败并附可读信息）。
    """

    _SETTLE_POLL_SECONDS = 0.05

    async def execute(self, driver, context, params: dict) -> dict:
        max_swipes = int(params.get("max_swipes", 5))
        direction = params.get("direction", "up")
        wait_timeout = params.get("wait_timeout", 2)
        percent = self._bounded_percent(params.get("percent", 0.2))
        duration = int(params.get("duration", 500))
        settle_ms = self._bounded_settle_ms(params.get("settle_ms", 500))
        stop = getattr(context, "should_stop", None)
        last_error: ElementNotFound | None = None
        element_id = params.get("element_id")
        logger.info(
            "swipe_to_find 开始 %s",
            json.dumps(
                {
                    "event": "swipe_to_find_start",
                    "action": "swipe_to_find",
                    "element_id": element_id,
                    "direction": direction,
                    "max_swipes": max_swipes,
                    "duration_ms": duration,
                    "wait_timeout": wait_timeout,
                    "percent": percent,
                    "settle_ms": settle_ms,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        for i in range(max_swipes + 1):
            if stop is not None and stop():
                raise StopRequested("执行被用户停止")
            try:
                element = context.find_element(element_id, wait_timeout=wait_timeout)
                diagnostic = _element_diagnostics(
                    driver, context, element_id, element,
                    action="swipe_to_find", params=params,
                )
                diagnostic.update({
                    "event": "swipe_to_find_found",
                    "found_after_swipes": i,
                    "touch_point": "not applicable: swipe_to_find only locates and does not click",
                })
                _log_diagnostic(logging.INFO, diagnostic)
                return {"status": "passed", "found_after_swipes": i}
            except ElementNotFound as exc:
                last_error = exc
                logger.debug(
                    "swipe_to_find 未命中 %s",
                    json.dumps(
                        {
                            "event": "swipe_to_find_not_found",
                            "action": "swipe_to_find",
                            "element_id": element_id,
                            "swipe_index": i,
                            "error": _diagnostic_value(str(exc)),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            if i < max_swipes:
                geometry = _swipe_geometry(driver, direction, percent)
                geometry.update({
                    "event": "swipe_to_find_swipe_before",
                    "action": "swipe_to_find",
                    "element_id": element_id,
                    "swipe_index": i + 1,
                    "direction": direction,
                    "duration_ms": duration,
                    "settle_ms": settle_ms,
                    "percent": percent,
                })
                logger.debug(
                    "swipe_to_find 滑动前 %s",
                    json.dumps(geometry, ensure_ascii=False, sort_keys=True),
                )
                driver.swipe(direction, duration=duration, percent=percent)
                geometry["event"] = "swipe_to_find_swipe_after"
                geometry["swipe_status"] = "sent"
                logger.debug(
                    "swipe_to_find 滑动后 %s",
                    json.dumps(geometry, ensure_ascii=False, sort_keys=True),
                )
                await self._wait_for_settle(context, settle_ms / 1000.0)
        raise ElementNotFound(f"滑动 {max_swipes} 次后仍未找到元素（{direction}，{last_error}）")

    @staticmethod
    def _bounded_percent(raw) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise DriverError("滑动查找参数 percent 必须是数字") from None
        if not math.isfinite(value) or not 0.05 <= value <= 0.95:
            raise DriverError("滑动查找参数 percent 必须在 0.05～0.95 之间")
        return value

    @staticmethod
    def _bounded_settle_ms(raw) -> int:
        if isinstance(raw, bool):
            raise DriverError("滑动查找参数 settle_ms 必须是整数")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise DriverError("滑动查找参数 settle_ms 必须是整数") from None
        if not math.isfinite(value) or not value.is_integer() or not 0 <= value <= 5000:
            raise DriverError("滑动查找参数 settle_ms 必须在 0～5000ms")
        return int(value)

    async def _wait_for_settle(self, context, seconds: float) -> None:
        """滑动后等待页面稳定；等待分片执行以便及时响应停止请求。"""
        deadline = asyncio.get_running_loop().time() + seconds
        while True:
            stop = getattr(context, "should_stop", None)
            if stop is not None and stop():
                raise StopRequested("执行被用户停止")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(self._SETTLE_POLL_SECONDS, remaining))


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
    找到后先等待页面稳定，再重新定位并复核视口后点击；如遇 stale，重新定位列表和目标重试，不能复用旧句柄。
    """

    _CLICK_STALE_RETRY_DELAYS = (0.2, 0.5)
    _OBSERVE_STALE_RETRY_DELAYS = (0.05, 0.15)
    _TARGET_CLICK_SETTLE_SECONDS = 0.5
    _TARGET_CLICK_SETTLE_POLL_SECONDS = 0.05
    _DIAGNOSTIC_MAX_CANDIDATES = 20

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

        # 查询日志需要知道当前阶段，但阶段信息只用于诊断，不参与任何控制流。
        self._diagnostic_phase = "forward"
        self._diagnostic_direction = preferred
        self._diagnostic_round = 0
        self._diagnostic_target_text = target_text
        self._diagnostic_match_mode = match_mode
        self._diagnostic_container_locator = self._container_locator(context, element_id)

        # ListView 直接子项的相对 XPath；literal 由 Appium 驱动统一安全编码。
        literal = _xpath_literal(target_text)
        selector = (
            f"./*[@text={literal}]"
            if match_mode == "equals"
            else f"./*[contains(@text,{literal})]"
        )

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
                "列表文字查询未命中: action=swipe_in_element_find_text_click phase=forward "
                "direction=%s next_scroll_direction=%s target=%r",
                preferred,
                preferred,
                _diagnostic_value(target_text, limit=120),
            )
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
        self._diagnostic_phase = "reverse"
        self._diagnostic_direction = reverse_direction
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
                "列表文字查询未命中: action=swipe_in_element_find_text_click phase=reverse "
                "direction=%s next_scroll_direction=%s target=%r",
                reverse_direction,
                reverse_direction,
                _diagnostic_value(target_text, limit=120),
            )
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
        logger.info(
            "列表目标命中候选: element_id=%s target=%r click_validation=pending",
            self._diagnostic_element_id(target),
            _diagnostic_value(getattr(self, "_diagnostic_target_text", ""), limit=120),
        )
        try:
            # 目标刚被观测为安全可点击时，页面仍可能在滚动/重绘。
            # 等待期间不持有或使用旧句柄；点击流程会重新定位并再次校验视口。
            await self._wait_before_click(context)
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

    async def _wait_before_click(self, context) -> None:
        """安全命中后等待页面稳定，并在等待期间及时响应停止请求。"""
        stop = getattr(context, "should_stop", None)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._TARGET_CLICK_SETTLE_SECONDS
        while True:
            if stop is not None and stop():
                raise StopRequested("执行被用户停止")
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(self._TARGET_CLICK_SETTLE_POLL_SECONDS, remaining))

    async def _observe_target(
        self, driver, context, element_id,
        selector, wait_timeout,
    ):
        """重新定位并观测列表目标，stale 时丢弃旧句柄后重试。"""
        delays = self._OBSERVE_STALE_RETRY_DELAYS
        for attempt in range(len(delays) + 1):
            try:
                self._diagnostic_round += 1
                container, parent, parent_region = self._locate_container_and_parent(
                    driver, context, element_id, wait_timeout
                )
                self._log_target_query_start(container, parent_region, selector, element_id)
                query_started = time.perf_counter()
                target, direction = self._find_target_in_container(
                    driver, container, parent_region, selector
                )
                query_elapsed_ms = (time.perf_counter() - query_started) * 1000
                logger.info(
                    "列表目标查询完成: action=swipe_in_element_find_text_click "
                    "round=%s phase=%s query_kind=target elapsed_ms=%.1f candidates=%s",
                    self._diagnostic_round,
                    getattr(self, "_diagnostic_phase", "unknown"),
                    getattr(self, "_diagnostic_query_elapsed_ms", query_elapsed_ms),
                    getattr(self, "_diagnostic_candidate_count", "unknown"),
                )
                return container, parent, parent_region, target, direction
            except Exception as exc:
                logger.warning(
                    "列表目标查询异常: action=swipe_in_element_find_text_click "
                    "round=%s phase=%s exception_type=%s exception=%s",
                    getattr(self, "_diagnostic_round", "unknown"),
                    getattr(self, "_diagnostic_phase", "unknown"),
                    type(exc).__name__,
                    _diagnostic_value(str(exc), limit=240),
                )
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
    def _container_locator(context, element_id):
        try:
            snapshot = getattr(context, "elements_snapshot", {})
            data = snapshot.get(str(element_id), {}) if isinstance(snapshot, dict) else {}
            if isinstance(data, dict):
                return {
                    "locator_type": data.get("locator_type"),
                    "locator_value": _diagnostic_value(data.get("locator_value"), limit=160),
                }
        except Exception:
            pass
        return {"element_id": element_id}

    @staticmethod
    def _diagnostic_element_id(element):
        try:
            value = getattr(element, "id", None)
            if value not in (None, ""):
                return _diagnostic_value(value, limit=120)
        except Exception:
            pass
        try:
            return _diagnostic_value(getattr(element, "locator_value", None), limit=120)
        except Exception:
            return None

    def _log_target_query_start(self, container, parent_region, selector, element_id):
        logger.info(
            "列表目标查询开始: action=swipe_in_element_find_text_click round=%s phase=%s direction=%s "
            "target=%r match_mode=%s container_locator=%s container_element_id=%s "
            "parent_viewport=%s safe_click_region=%s strategy=xpath "
            "selector=%s",
            getattr(self, "_diagnostic_round", "unknown"),
            getattr(self, "_diagnostic_phase", "unknown"),
            getattr(self, "_diagnostic_direction", "unknown"),
            _diagnostic_value(getattr(self, "_diagnostic_target_text", ""), limit=120),
            getattr(self, "_diagnostic_match_mode", "unknown"),
            getattr(self, "_diagnostic_container_locator", {"element_id": element_id}),
            self._diagnostic_element_id(container),
            parent_region,
            parent_region,
            _diagnostic_value(selector, limit=240),
        )

    def _log_actual_click(self, target, rect, message):
        logger.info(
            "列表目标%s: element_id=%s target=%r rect=%s expected_click_point=%s",
            message,
            self._diagnostic_element_id(target),
            _diagnostic_value(getattr(self, "_diagnostic_target_text", ""), limit=120),
            rect,
            _rect_center_int(rect),
        )

    @staticmethod
    def _parent_region(driver, parent):
        size = driver.get_window_size()
        return _clip_rect(driver.get_element_rect(parent), size)

    def _find_target_in_container(self, driver, container, visible, selector):
        query_started = time.perf_counter()
        matches = driver.find_elements_in_element(container, "xpath", selector, wait_timeout=0)
        self._diagnostic_query_elapsed_ms = (time.perf_counter() - query_started) * 1000
        self._diagnostic_candidate_count = len(matches)
        if not matches:
            logger.info(
                "列表目标查询返回空: action=swipe_in_element_find_text_click query_kind=target "
                "container_element_id=%s selector=%s elapsed_ms=%s",
                self._diagnostic_element_id(container),
                _diagnostic_value(selector, limit=240),
                f"{self._diagnostic_query_elapsed_ms:.1f}",
            )
        candidates = []
        safe = []
        for index, match in enumerate(matches):
            displayed, enabled, displayed_enabled = _displayed_enabled_state(driver, match)
            reason = None
            rect = None
            if displayed_enabled:
                rect = driver.get_element_rect(match)
                safe_match, reason = self._safe_clickability(rect, visible)
                candidates.append(match)
                if safe_match:
                    safe.append(match)
            else:
                reason = "not_displayed_or_disabled"
            if index < self._DIAGNOSTIC_MAX_CANDIDATES:
                logger.info(
                    "列表目标候选: element_id=%s displayed=%s enabled=%s rect=%s "
                    "safe_clickable=%s reject_reason=%s",
                    self._diagnostic_element_id(match),
                    displayed,
                    enabled,
                    rect,
                    match in safe,
                    reason or "none",
                )
        omitted = len(matches) - min(len(matches), self._DIAGNOSTIC_MAX_CANDIDATES)
        if omitted > 0:
            logger.info("列表目标候选日志省略: omitted=%s max=%s", omitted, self._DIAGNOSTIC_MAX_CANDIDATES)
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

    @staticmethod
    def _safe_clickability(rect, visible):
        """与 _is_safely_clickable 保持同一判定，同时提供诊断原因。"""
        if _rect_area(rect) <= 0:
            return False, "empty_rect"
        coverage = _intersection_area(rect, visible) / _rect_area(rect)
        if coverage < 0.8:
            return False, f"coverage={coverage:.3f}"
        cx, cy = _rect_center(rect)
        margin = max(4, min(int(rect.get("height", 0)) // 4, 24))
        if not (
            visible["x"] + margin <= cx <= visible["x"] + visible["width"] - margin
            and visible["y"] + margin <= cy <= visible["y"] + visible["height"] - margin
        ):
            return False, f"center_outside_safe_margin={margin}"
        return True, None

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
                target_rect = driver.get_element_rect(target)
                safe, _reason = self._safe_clickability(target_rect, visible)
                if not safe:
                    return False, _direction_to_reveal(target_rect, visible)
                self._log_actual_click(target, target_rect, "实际点击")
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
                        ):
                            rect = driver.get_element_rect(new_target)
                            safe, _reason = self._safe_clickability(rect, visible)
                            if safe:
                                self._log_actual_click(new_target, rect, "实际点击（坐标回退）")
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
            if direction == "up":
                # 向上手势必须限制在本轮 ListView 的自身可见区域，不能扩大到父视口。
                gesture_region = _clip_rect(list_rect, driver.get_window_size())
                region_type = "list_view_visible"
            else:
                gesture_region = region
                region_type = "parent_visible"
            start_x, start_y, end_x, end_y, actual_distance_px = _vertical_swipe_points(
                gesture_region, direction, target_distance_px
            )
            logger.info(
                "列表坐标滑动: gesture=duration_w3c region_type=%s direction=%s "
                "start=(%s,%s) end=(%s,%s) target_distance_px=%s "
                "actual_distance_px=%s duration_ms=%s",
                region_type,
                direction,
                start_x,
                start_y,
                end_x,
                end_y,
                target_distance_px,
                actual_distance_px,
                duration_ms,
            )
            return driver.swipe_coordinate(
                start_x, start_y, end_x, end_y, duration_ms
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
            query_started = time.perf_counter()
            nodes = driver.find_elements_in_element(
                container, "uiautomator", "new UiSelector()", wait_timeout=0
            )
            logger.info(
                "列表签名查询完成: action=swipe_in_element_find_text_click "
                "query_kind=signature container_element_id=%s elapsed_ms=%.1f nodes=%s",
                cls._diagnostic_element_id(container),
                (time.perf_counter() - query_started) * 1000,
                len(nodes),
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


def _vertical_swipe_points(
    region: dict[str, int], direction: str, target_distance_px: int
) -> tuple[int, int, int, int, int]:
    """在可见矩形内生成带安全边距的竖向坐标手势，并夹紧可用距离。"""
    left = int(region.get("x", 0))
    top = int(region.get("y", 0))
    width = int(region.get("width", 0))
    height = int(region.get("height", 0))
    if width <= 0 or height <= 0:
        raise DriverError(
            f"列表滑动区域过小，无法执行坐标手势: region=({left},{top},{width},{height})"
        )

    # 轨迹整体位于区域中央，避免从底部/顶部边缘附近注入手势。
    # 正常区域至少留出 8px 且约为高度的 5%；极小区域自动缩小边距。
    margin = max(8, round(height * 0.05))
    margin = min(margin, (height - 1) // 2)
    safe_top = top + margin
    safe_bottom = top + height - 1 - margin
    available_distance = safe_bottom - safe_top
    if available_distance <= 0:
        raise DriverError(
            f"列表滑动区域过小，无法容纳安全手势距离: region=({left},{top},{width},{height})"
        )
    actual_distance = min(max(1, int(target_distance_px)), available_distance)
    start_x = left + width // 2
    center_twice = safe_top + safe_bottom
    # 向上取整起点处理奇偶像素，使轨迹中心尽可能贴近安全内框中心。
    centered_start = (center_twice - actual_distance + 1) // 2
    if direction == "up":
        end_y = centered_start
        start_y = end_y + actual_distance
    elif direction == "down":
        start_y = centered_start
        end_y = start_y + actual_distance
    else:
        raise DriverError(f"不支持的列表滑动方向: {direction}")
    return start_x, start_y, start_x, end_y, actual_distance


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
    return _displayed_enabled_state(driver, element)[2]


def _displayed_enabled_state(driver, element) -> tuple[bool, bool | None, bool]:
    """Return displayed/enabled diagnostics while preserving the normal short-circuit order."""
    state: dict[str, bool] = {}
    for method_name, attribute_name in (("is_displayed", "displayed"), ("is_enabled", "enabled")):
        method = getattr(element, method_name, None)
        if callable(method):
            try:
                value = bool(method())
            except Exception:
                value = None
        else:
            value = None
        if value is None:
            getter = getattr(driver, "get_attribute", None)
            try:
                value = getter(element, attribute_name) if callable(getter) else None
            except Exception:
                value = ""
            value = not (value not in ("", None) and str(value).lower() == "false")
        state[attribute_name] = bool(value)
        if not value:
            return state.get("displayed", True), state.get("enabled"), False
    return state.get("displayed", True), state.get("enabled"), True


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
