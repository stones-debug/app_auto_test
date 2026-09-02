"""统一执行流的断言等待策略。"""

import asyncio
import inspect
import time
from collections.abc import Callable

from .driver import ElementNotFound, StopRequested
from .stale_guard import is_stale_element_error


def _retryable_error(error: BaseException) -> bool:
    return isinstance(error, ElementNotFound) or is_stale_element_error(error)


async def verify_with_wait(
    verify: Callable[[float], object],
    *,
    max_wait_seconds: float = 10,
    should_stop: Callable[[], bool] | None = None,
    interval_seconds: float = 0.5,
    on_interrupt: Callable[[], object] | None = None,
) -> dict:
    """在最大等待时间内轮询断言，返回最后一次结果及尝试次数。

    ``verify`` 接收本次断言的绝对 monotonic 截止时间，可以是同步函数，也可以
    返回 awaitable。断言返回 failed 表示条件尚未满足，只有可恢复的定位/stale
    异常才会被转换成重试；其他异常直接抛出。

    每次验证都在独立任务中运行，以便截止时间或停止信号到达时取消等待。对于
    仍阻塞在 Appium/驱动调用中的线程，通过 ``on_interrupt`` 请求驱动中断，避免
    外层事件循环被默认的元素等待时间拖住。
    """
    deadline = time.monotonic() + max(0.0, float(max_wait_seconds))
    attempts = 0
    interrupt_requested = False
    immediate_attempt_pending = max_wait_seconds <= 0

    def request_interrupt() -> None:
        nonlocal interrupt_requested
        if interrupt_requested or on_interrupt is None:
            return
        interrupt_requested = True
        try:
            result = on_interrupt()
            if inspect.isawaitable(result):
                async def wait_interrupt() -> None:
                    try:
                        await result
                    except Exception:
                        pass

                asyncio.create_task(wait_interrupt())
        except Exception:
            # 中断只是尽力而为，原始的停止/超时结果不能被中断失败覆盖。
            pass

    async def cancel_attempt(task: asyncio.Task, *, interrupt: bool = False) -> None:
        # 只有用户停止才允许打断驱动。普通断言超时不能调用 driver.interrupt，
        # 因为 AppiumDriver.interrupt 会 terminate_app，破坏后续节点执行。
        if interrupt:
            request_interrupt()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def sleep_until_retry() -> None:
        while True:
            if should_stop is not None and should_stop():
                raise StopRequested("执行被用户停止")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.1, interval_seconds, remaining))

    async def invoke_verify(current_deadline: float):
        result = verify(current_deadline)
        if inspect.isawaitable(result):
            return await result
        return result

    while True:
        if should_stop is not None and should_stop():
            raise StopRequested("执行被用户停止")
        attempts += 1
        attempt_task = asyncio.create_task(invoke_verify(deadline))
        normalized: dict
        try:
            while not attempt_task.done():
                remaining = deadline - time.monotonic()
                if should_stop is not None and should_stop():
                    await cancel_attempt(attempt_task, interrupt=True)
                    raise StopRequested("执行被用户停止")
                if remaining <= 0:
                    if immediate_attempt_pending:
                        # max_wait=0 仍执行一次立即尝试。必须等待这一次驱动请求
                        # 完成，不能在 50ms 后取消 asyncio.to_thread，避免底层线程
                        # 继续持有 Appium 请求锁并阻塞下一条命令。
                        immediate_attempt_pending = False
                        await attempt_task
                        continue
                    await cancel_attempt(attempt_task)
                    normalized = {
                        "status": "failed",
                        "actual": "",
                        "error_message": "断言达到最大等待时间",
                    }
                    break
                await asyncio.wait({attempt_task}, timeout=min(0.1, remaining))
            else:
                result = await attempt_task
                normalized = (
                    dict(result)
                    if isinstance(result, dict)
                    else {"status": "failed", "actual": str(result)}
                )
        except StopRequested:
            raise
        except Exception as exc:
            if not _retryable_error(exc):
                raise
            normalized = {"status": "failed", "actual": "", "error_message": str(exc)}

        normalized["attempt_count"] = attempts
        if normalized.get("status") == "passed":
            return normalized
        if time.monotonic() >= deadline:
            return normalized
        await sleep_until_retry()
