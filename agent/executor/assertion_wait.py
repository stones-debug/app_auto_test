"""统一执行流的断言等待策略。"""

import asyncio
import time
from collections.abc import Callable

from .driver import ElementNotFound, StopRequested
from .stale_guard import is_stale_element_error


def _retryable_error(error: BaseException) -> bool:
    return isinstance(error, ElementNotFound) or is_stale_element_error(error)


async def verify_with_wait(
    verify: Callable[[], object],
    *,
    max_wait_seconds: float = 10,
    should_stop: Callable[[], bool] | None = None,
    interval_seconds: float = 0.5,
) -> dict:
    """在最大等待时间内轮询断言，返回最后一次结果及尝试次数。

    ``verify`` 可以是同步函数，也可以返回 awaitable。断言返回 failed 表示条件
    尚未满足，只有可恢复的定位/stale 异常才会被转换成重试；其他异常直接抛出。
    """
    deadline = time.monotonic() + max(0.0, float(max_wait_seconds))
    attempts = 0
    while True:
        if should_stop is not None and should_stop():
            raise StopRequested("执行被用户停止")
        attempts += 1
        try:
            result = verify()
            if hasattr(result, "__await__"):
                result = await result
            normalized = dict(result) if isinstance(result, dict) else {"status": "failed", "actual": str(result)}
        except StopRequested:
            raise
        except Exception as exc:
            if not _retryable_error(exc):
                raise
            normalized = {"status": "failed", "actual": "", "error_message": str(exc)}

        normalized["attempt_count"] = attempts
        if normalized.get("status") == "passed":
            return normalized
        if max_wait_seconds <= 0 or time.monotonic() >= deadline:
            return normalized
        await asyncio.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))
