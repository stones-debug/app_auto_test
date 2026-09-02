"""统一元素操作 stale 重试守卫（智能定位统一接入点）。"""

import asyncio
import logging

from .driver import ElementNotFound, ElementStaleRetryExhausted, StopRequested

logger = logging.getLogger("agent.executor.stale_guard")

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


def is_stale_element_error(error: BaseException) -> bool:
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


async def with_stale_retry(
    driver,
    context,
    element_id,
    operation,
    *,
    wait_timeout=None,
    deadline: float | None = None,
    editable: bool = False,
    disable_smart_scroll: bool = False,
    retries: int = 2,
    delays: tuple[float, ...] = (0.2, 0.5),
    label: str = "操作",
):
    """统一元素操作：失效后丢弃旧元素 → 重新定位 → 重试 operation。

    operation 接收新定位到的元素并返回其操作结果；不得复用失效元素对象。
    捕获到失效异常后：context.invalidate_element 丢弃旧句柄 → 重新
    context.find_element → 重试；耗尽抛 ElementStaleRetryExhausted（DriverError 子类）。
    disable_smart_scroll 透传给 context.find_element：智能定位时临时禁用自动滚动。
    """
    stop = getattr(context, "should_stop", None)
    for attempt in range(retries + 1):
        if stop is not None and stop():
            raise StopRequested("执行被用户停止")
        remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
        allow_immediate = remaining is not None and remaining <= 0 and attempt == 0
        if remaining is not None and remaining <= 0 and not allow_immediate:
            raise ElementNotFound(f"{label}达到最大等待时间")
        bounded_wait_timeout = wait_timeout
        if remaining is not None:
            bounded_wait_timeout = (
                0.0
                if allow_immediate
                else remaining if wait_timeout is None else min(float(wait_timeout), remaining)
            )
        find_kwargs = {
            "wait_timeout": bounded_wait_timeout,
            "editable": editable,
            "disable_smart_scroll": disable_smart_scroll,
        }
        if deadline is not None:
            find_kwargs["deadline"] = deadline
            find_kwargs["allow_immediate"] = allow_immediate
        try:
            element = context.find_element(element_id, **find_kwargs)
            if deadline is None:
                return operation(element)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0 and not allow_immediate:
                raise ElementNotFound(f"{label}达到最大等待时间")
            run_with_timeout = getattr(driver, "run_with_http_timeout", None)
            if callable(run_with_timeout):
                return run_with_timeout(
                    None if allow_immediate else remaining,
                    lambda element=element: operation(element),
                )
            return operation(element)
        except Exception as exc:
            if not is_stale_element_error(exc):
                raise
            if attempt >= retries:
                logger.error(
                    "%s元素连续失效，重新定位重试仍失败: element_id=%s",
                    label,
                    element_id,
                    exc_info=True,
                )
                raise ElementStaleRetryExhausted(
                    f"{label}失败：页面持续刷新导致元素失效，重新定位并重试 {retries} 次后仍未成功"
                ) from exc
            delay = delays[attempt]
            logger.warning(
                "%s遇到失效元素，将在 %.1fs 后重新定位（第 %s/%s 次重试）: element_id=%s",
                label,
                delay,
                attempt + 1,
                retries,
                element_id,
            )
            context.invalidate_element(element_id)
            remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
            if remaining is not None and remaining <= 0:
                raise ElementNotFound(f"{label}达到最大等待时间") from exc
            await asyncio.sleep(delay if remaining is None else min(delay, remaining))
    raise AssertionError("stale 重试状态异常")
