import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .actions import ACTION_REGISTRY
from .assertions import ASSERTION_REGISTRY
from .context import ExecutionContext
from .driver import StopRequested

SendFn = Callable[[dict], Awaitable[None]]


class RunnerReporter:
    def __init__(self, send: SendFn, execution_id: int) -> None:
        self.send = send
        self.execution_id = execution_id

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
    ) -> None:
        self.driver = driver
        self.send = send
        self.execution_id = execution_id
        self.parameters = parameters or {}
        self.should_stop = should_stop or (lambda: False)
        self.screenshots_dir = screenshots_dir

    async def run_case(self, case: dict) -> str:
        case_id = case.get("case_id")
        context = ExecutionContext(
            self.driver,
            case,
            self.parameters.get("variables", {}),
            self.screenshots_dir,
        )
        reporter = RunnerReporter(self.send, self.execution_id)
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
                result = await action_cls().execute(self.driver, context, effective)
            except Exception as exc:
                result = {"status": "failed", "error_message": str(exc)}
            duration = int((time.monotonic() - start) * 1000)
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
                res = await cls().verify(self.driver, context, effective)
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
