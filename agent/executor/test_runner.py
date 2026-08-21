import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .actions import ACTION_REGISTRY
from .assertions import ASSERTION_REGISTRY
from .context import ExecutionContext
from .driver import StopRequested

SendFn = Callable[[dict], Awaitable[None]]


def _run_action_in_thread(action_cls, driver, context, params: dict) -> dict:
    """Windows 方案 §2：动作在独立工作线程的专用事件循环中执行。

    动作内部对 Appium/ADB 的同步调用（find_element/click/截图等）因此不会阻塞
    主事件循环；停止时 interrupt() 在另一线程关闭 Appium 会话以打断阻塞命令。
    """
    return asyncio.run(action_cls().execute(driver, context, params))


def _run_assertion_in_thread(assertion_cls, driver, context, params: dict) -> dict:
    return asyncio.run(assertion_cls().verify(driver, context, params))


class RunnerReporter:
    def __init__(self, send: SendFn, execution_id: int, session_token: str | None = None) -> None:
        self.send = send
        self.execution_id = execution_id
        self.session_token = session_token

    async def step_result(
        self,
        case_id: int,
        step_order: int,
        action: str,
        status: str,
        duration: int,
        actual_value: str | None = None,
        error_message: str | None = None,
        screenshot_path: str | None = None,
    ) -> None:
        await self.send(
            {
                "type": "step_result",
                "execution_id": self.execution_id,
                "session_token": self.session_token,
                "case_id": case_id,
                "step_order": step_order,
                "action": action,
                "status": status,
                "duration": duration,
                "actual_value": actual_value,
                "error_message": error_message,
                "screenshot_path": screenshot_path,
            }
        )

    async def assertion_result(self, case_id: int, assertions: list[dict]) -> None:
        await self.send(
            {
                "type": "assertion_result",
                "execution_id": self.execution_id,
                "session_token": self.session_token,
                "case_id": case_id,
                "assertions": assertions,
            }
        )


class TestRunner:
    __test__ = False  # 防止被 pytest 当作测试类收集

    def __init__(
        self,
        driver,
        send: SendFn,
        execution_id: int,
        parameters: dict | None = None,
        should_stop: Callable[[], bool] | None = None,
        screenshots_dir: Path | None = None,
        session_token: str | None = None,
        uploader=None,
    ) -> None:
        self.driver = driver
        self.send = send
        self.execution_id = execution_id
        self.parameters = parameters or {}
        self.should_stop = should_stop or (lambda: False)
        self.screenshots_dir = screenshots_dir
        self.session_token = session_token
        self.uploader = uploader

    async def _resolve_screenshot(self, result: dict) -> None:
        """CR-07：截图成功后立即 HTTP 上传，只回传服务端对象键；失败记录明确错误。"""
        local_path = result.get("screenshot_path")
        if not local_path or self.uploader is None:
            return
        uploaded = await self.uploader.upload_screenshot(
            self.execution_id, local_path, self.session_token
        )
        if uploaded:
            result["screenshot_path"] = uploaded
        else:
            result["screenshot_path"] = None
            result["error_message"] = "截图上传失败，Agent 本地路径不回传服务端"

    async def run_case(self, case: dict) -> str:
        case_id = case.get("case_id")
        context = ExecutionContext(
            self.driver,
            case,
            self.parameters.get("variables", {}),
            self.screenshots_dir,
        )
        reporter = RunnerReporter(self.send, self.execution_id, self.session_token)
        case_status = "passed"

        for step in case.get("steps_snapshot") or []:
            if self.should_stop():
                raise StopRequested("执行被用户停止")
            step_order = step.get("order") or step.get("step_order")
            start = time.monotonic()
            result: dict = {"status": "passed"}
            try:
                action_cls = ACTION_REGISTRY.get(step.get("action"))
                if action_cls is None:
                    raise ValueError(f"未知动作: {step.get('action')}")
                effective = dict(step.get("params") or {})
                if step.get("element_id") is not None and "element_id" not in effective:
                    effective["element_id"] = step["element_id"]
                result = await asyncio.to_thread(
                    _run_action_in_thread, action_cls, self.driver, context, effective
                )
            except Exception as exc:
                result = {"status": "failed", "error_message": str(exc)}
            duration = int((time.monotonic() - start) * 1000)
            await self._resolve_screenshot(result)
            await reporter.step_result(
                case_id,
                step_order,
                step.get("action"),
                result.get("status", "passed"),
                duration,
                actual_value=result.get("actual_value"),
                error_message=result.get("error_message"),
                screenshot_path=result.get("screenshot_path"),
            )
            if result.get("status") == "failed":
                case_status = "failed"
                if not (step.get("params") or {}).get("continue_on_failure"):
                    break

        assertion_results: list[dict] = []
        for assertion in case.get("assertions_snapshot") or []:
            try:
                cls = ASSERTION_REGISTRY.get(assertion.get("type"))
                if cls is None:
                    raise ValueError(f"未知断言: {assertion.get('type')}")
                effective = dict(assertion.get("params") or {})
                if assertion.get("element_id") is not None and "element_id" not in effective:
                    effective["element_id"] = assertion["element_id"]
                res = await asyncio.to_thread(
                    _run_assertion_in_thread, cls, self.driver, context, effective
                )
            except Exception as exc:
                res = {
                    "status": "failed",
                    "expected": (assertion.get("params") or {}).get("expected"),
                    "actual": "",
                    "error_message": str(exc),
                }
            assertion_results.append(
                {
                    "type": assertion.get("type"),
                    "expected": res.get("expected"),
                    "actual": res.get("actual"),
                    "status": res.get("status", "failed"),
                    "error_message": res.get("error_message"),
                }
            )
            if res.get("status") != "passed":
                case_status = "failed"
        await reporter.assertion_result(case_id, assertion_results)
        return case_status
