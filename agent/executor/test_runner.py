import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .actions import ACTION_REGISTRY
from .assertions import ASSERTION_REGISTRY
from .context import ExecutionContext
from .driver import StopRequested
from .protocol_messages import (
    AssertionItem,
    AssertionResultMessage,
    ExecutionResultMessage,
    LogMessage,
    StepResultMessage,
)

Message = dict | StepResultMessage | AssertionResultMessage | ExecutionResultMessage | LogMessage
SendFn = Callable[[Message], Awaitable[None]]


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

    async def log(
        self,
        level: str,
        message: str,
        step_order: int | None = None,
    ) -> None:
        msg: LogMessage = {
            "type": "log",
            "execution_id": self.execution_id,
            "session_token": self.session_token,
            "level": level,
            "message": message,
        }
        if step_order is not None:
            msg["step_order"] = step_order
        await self.send(msg)

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
        msg: StepResultMessage = {
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
        await self.send(msg)

    async def assertion_result(self, case_id: int, assertions: list[AssertionItem]) -> None:
        msg: AssertionResultMessage = {
            "type": "assertion_result",
            "execution_id": self.execution_id,
            "session_token": self.session_token,
            "case_id": case_id,
            "assertions": assertions,
        }
        await self.send(msg)


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

    async def _run_steps(
        self,
        steps: list[dict],
        case_id: int,
        context: ExecutionContext,
        reporter: RunnerReporter,
        phase: str,
    ) -> tuple[bool, bool]:
        """执行一个阶段，返回 (阶段是否失败, 是否因失败中断阶段)。"""
        failed = False
        halted = False
        phase_label = {"setup": "前置步骤", "teardown": "后置步骤"}.get(phase, "步骤")
        for step in steps:
            if self.should_stop():
                raise StopRequested("执行被用户停止")
            step_order = step.get("order") or step.get("step_order")
            start = time.monotonic()
            result: dict = {"status": "passed"}
            action_name = str(step.get("action") or "unknown")
            try:
                if self.parameters.get("attach_to_current_app") and action_name == "launch_app":
                    result = {
                        "status": "passed",
                        "actual_value": "已复用当前设备界面，跳过启动 APP",
                    }
                else:
                    action_cls = ACTION_REGISTRY.get(step.get("action"))
                    if action_cls is None:
                        raise ValueError(f"未知动作: {step.get('action')}")
                    effective = dict(step.get("params") or {})
                    if step.get("element_id") is not None and "element_id" not in effective:
                        effective["element_id"] = step["element_id"]
                    result = await asyncio.to_thread(
                        _run_action_in_thread, action_cls, self.driver, context, effective
                    )
            except StopRequested:
                raise
            except Exception as exc:
                result = {"status": "failed", "error_message": str(exc)}
            duration = int((time.monotonic() - start) * 1000)
            await self._resolve_screenshot(result)
            await reporter.step_result(
                case_id,
                step_order,
                action_name,
                result.get("status", "passed"),
                duration,
                actual_value=result.get("actual_value"),
                error_message=result.get("error_message"),
                screenshot_path=result.get("screenshot_path"),
            )
            step_status = result.get("status", "passed")
            if step_status == "passed":
                log_level = "INFO"
                log_message = f"{phase_label} {step_order} {action_name} 执行通过（{duration}ms）"
            else:
                log_level = "ERROR"
                error_message = str(result.get("error_message") or "未知错误")
                log_message = (
                    f"{phase_label} {step_order} {action_name} 执行失败（{duration}ms）：{error_message}"
                )
            await reporter.log(log_level, log_message, step_order)
            if step_status == "failed":
                failed = True
                if not step.get("continue_on_failure", False):
                    halted = True
                    break
        return failed, halted

    async def run_case(self, case: dict) -> str:
        case_id = int(case.get("case_id") or 0)
        context = ExecutionContext(
            self.driver,
            case,
            self.parameters.get("variables", {}),
            self.screenshots_dir,
            self.should_stop,
        )
        reporter = RunnerReporter(self.send, self.execution_id, self.session_token)
        case_status = "passed"

        all_steps = case.get("steps_snapshot") or []
        setup_steps = [s for s in all_steps if str(s.get("phase") or "main") == "setup"]
        main_steps = [s for s in all_steps if str(s.get("phase") or "main") == "main"]
        teardown_steps = [s for s in all_steps if str(s.get("phase") or "main") == "teardown"]

        setup_failed, setup_halted = await self._run_steps(
            setup_steps, case_id, context, reporter, "setup"
        )
        if setup_failed:
            case_status = "failed"
        if not setup_halted:
            main_failed, _main_halted = await self._run_steps(
                main_steps, case_id, context, reporter, "main"
            )
            if main_failed:
                case_status = "failed"

        assertion_results: list[AssertionItem] = []
        assertions = [] if setup_halted else (case.get("assertions_snapshot") or [])
        for assertion in assertions:
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
                    "type": str(assertion.get("type") or ""),
                    "expected": str(res.get("expected") or ""),
                    "actual": str(res.get("actual") or ""),
                    "status": str(res.get("status") or "failed"),
                    "error_message": str(res.get("error_message") or None),
                }
            )
            if res.get("status") != "passed":
                case_status = "failed"
        await reporter.assertion_result(case_id, assertion_results)

        teardown_failed, _teardown_halted = await self._run_steps(
            teardown_steps, case_id, context, reporter, "teardown"
        )
        if teardown_failed:
            case_status = "failed"
        return case_status
