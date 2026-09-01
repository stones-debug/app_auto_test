import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .actions import ACTION_REGISTRY
from .assertion_wait import verify_with_wait
from .assertions import ASSERTION_REGISTRY
from .context import ExecutionContext
from .driver import StopRequested
from .protocol_messages import (
    AssertionItem,
    AssertionResultMessage,
    CaseStatusMessage,
    ExecutionResultMessage,
    LogMessage,
    NodeResultMessage,
    NodeStartedMessage,
    StepResultMessage,
    SuiteStatusMessage,
)
from .status import aggregate_statuses

Message = (
    dict
    | StepResultMessage
    | AssertionResultMessage
    | ExecutionResultMessage
    | LogMessage
    | SuiteStatusMessage
    | CaseStatusMessage
    | NodeStartedMessage
    | NodeResultMessage
)
SendFn = Callable[[Message], Awaitable[None]]


# 用例步骤阶段归一化：后端快照五值 → 本地分桶的三段（setup/main/teardown）。
# 协议 V2 起后端下发 case_setup/case_main/case_teardown；历史兼容 setup/main/teardown。
_CASE_PHASE_BUCKET = {
    "case_setup": "setup",
    "case_main": "main",
    "case_teardown": "teardown",
}


def _case_phase_bucket(phase: str | None) -> str:
    """将用例步骤 phase 归一化为分桶阶段（缺省视为 main）。"""
    return _CASE_PHASE_BUCKET.get(str(phase or "main"), str(phase or "main"))


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
        *,
        execution_case_id: int | None = None,
        execution_suite_id: int | None = None,
        execution_step_id: int,
        phase: str,
        step_order: int | None,
        action: str,
        status: str,
        duration: int,
        actual_value: str | None = None,
        error_message: str | None = None,
        screenshot_path: str | None = None,
    ) -> None:
        # Step 6.2：step_order 必须能转为大于 0 的 int，否则为协议错误；
        # StepResultMessage.step_order 保持 int，不放宽消息类型。
        if step_order is None:
            raise ValueError("协议 V2 快照缺少有效 step_order")
        try:
            step_order = int(step_order)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"协议 V2 快照 step_order 非法: {step_order!r}") from exc
        if step_order <= 0:
            raise ValueError(f"协议 V2 快照 step_order 非法: {step_order!r}")
        msg: StepResultMessage = {
            "type": "step_result",
            "execution_id": self.execution_id,
            "session_token": self.session_token,
            "execution_step_id": execution_step_id,
            "phase": phase,
            "step_order": step_order,
            "action": action,
            "status": status,
            "duration": duration,
            "actual_value": actual_value,
            "error_message": error_message,
            "screenshot_path": screenshot_path,
        }
        if execution_case_id is not None:
            msg["execution_case_id"] = execution_case_id
        if execution_suite_id is not None:
            msg["execution_suite_id"] = execution_suite_id
        await self.send(msg)

    async def assertion_result(self, execution_step_id: int, assertions: list[AssertionItem]) -> None:
        msg: AssertionResultMessage = {
            "type": "assertion_result",
            "execution_id": self.execution_id,
            "session_token": self.session_token,
            "execution_step_id": execution_step_id,
            "assertions": assertions,
        }
        await self.send(msg)

    async def node_started(
        self,
        execution_node_id: int,
        attempt: int | None = None,
        *,
        execution_case_id: int | None = None,
        kind: str | None = None,
    ) -> None:
        msg: NodeStartedMessage = {
            "type": "node_started", "execution_id": self.execution_id,
            "session_token": self.session_token, "execution_node_id": execution_node_id,
        }
        if attempt is not None:
            msg["attempt"] = attempt
        if execution_case_id is not None:
            msg["execution_case_id"] = execution_case_id
        if kind is not None:
            msg["kind"] = kind
        await self.send(msg)

    async def node_result(
        self, execution_node_id: int, *, status: str, duration: int,
        actual_value: str | None = None, expected_value: str | None = None,
        error_message: str | None = None, screenshot_path: str | None = None,
        attempt_count: int | None = None, execution_case_id: int | None = None,
        kind: str | None = None,
    ) -> None:
        msg: NodeResultMessage = {
            "type": "node_result", "execution_id": self.execution_id,
            "session_token": self.session_token, "execution_node_id": execution_node_id,
            "status": status, "duration": duration, "actual_value": actual_value,
            "expected_value": expected_value, "error_message": error_message,
            "screenshot_path": screenshot_path,
        }
        if attempt_count is not None:
            msg["attempt_count"] = attempt_count
        if execution_case_id is not None:
            msg["execution_case_id"] = execution_case_id
        if kind is not None:
            msg["kind"] = kind
        await self.send(msg)

    async def suite_status(
        self, execution_suite_id: int, status: str, error_message: str | None = None
    ) -> None:
        msg: SuiteStatusMessage = {
            "type": "suite_status",
            "execution_id": self.execution_id,
            "session_token": self.session_token,
            "execution_suite_id": execution_suite_id,
            "status": status,
        }
        if error_message:
            msg["error_message"] = error_message
        await self.send(msg)

    async def case_status(
        self, execution_case_id: int, status: str, error_message: str | None = None
    ) -> None:
        msg: CaseStatusMessage = {
            "type": "case_status",
            "execution_id": self.execution_id,
            "session_token": self.session_token,
            "execution_case_id": execution_case_id,
            "status": status,
        }
        if error_message:
            msg["error_message"] = error_message
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
        context: ExecutionContext,
        reporter: RunnerReporter,
        phase: str,
        *,
        execution_case_id: int | None = None,
        execution_suite_id: int | None = None,
    ) -> tuple[bool, bool]:
        """执行一个阶段，返回 (阶段是否失败, 是否因失败中断阶段)。"""
        failed = False
        halted = False
        phase_label = {
            "setup": "前置步骤",
            "teardown": "后置步骤",
            "suite_setup": "套件前置步骤",
            "suite_teardown": "套件后置步骤",
        }.get(phase, "步骤")
        for step in steps:
            if self.should_stop():
                raise StopRequested("执行被用户停止")
            execution_step_id = int(step.get("execution_step_id") or 0)
            if execution_step_id <= 0:
                raise ValueError("协议 V2 快照缺少有效 execution_step_id")
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
                    action_cls = ACTION_REGISTRY.get(action_name)
                    if action_cls is None:
                        raise ValueError(f"未知动作: {action_name}")
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
            assertion_results: list[AssertionItem] = []
            if result.get("status", "passed") == "passed":
                for assertion_index, assertion in enumerate(step.get("assertions") or [], start=1):
                    try:
                        assertion_cls = ASSERTION_REGISTRY.get(assertion.get("type"))
                        if assertion_cls is None:
                            raise ValueError(f"未知断言: {assertion.get('type')}")
                        effective = dict(assertion.get("params") or {})
                        if assertion.get("element_id") is not None and "element_id" not in effective:
                            effective["element_id"] = assertion["element_id"]
                        assertion_result = await asyncio.to_thread(
                            _run_assertion_in_thread,
                            assertion_cls,
                            self.driver,
                            context,
                            effective,
                        )
                    except StopRequested:
                        raise
                    except Exception as exc:
                        assertion_result = {
                            "status": "failed",
                            "expected": (assertion.get("params") or {}).get("expected"),
                            "actual": "",
                            "error_message": str(exc),
                        }
                    execution_assertion_id = int(assertion.get("execution_assertion_id") or 0)
                    if execution_assertion_id <= 0:
                        raise ValueError("协议快照缺少有效 execution_assertion_id")
                    item: AssertionItem = {
                        "execution_assertion_id": execution_assertion_id,
                        "type": str(assertion.get("type") or ""),
                        "assertion_order": int(assertion.get("order") or assertion_index),
                        "expected": str(assertion_result.get("expected") or ""),
                        "actual": str(assertion_result.get("actual") or ""),
                        "status": str(assertion_result.get("status") or "failed"),
                        "error_message": (
                            str(assertion_result["error_message"])
                            if assertion_result.get("error_message")
                            else None
                        ),
                    }
                    assertion_results.append(item)
                    if assertion_result.get("status") != "passed":
                        result = {
                            **result,
                            "status": "failed",
                            "error_message": item.get("error_message")
                            or f"步骤后断言 {item['assertion_order']} 未通过",
                        }
            duration = int((time.monotonic() - start) * 1000)
            await self._resolve_screenshot(result)
            await reporter.step_result(
                execution_case_id=execution_case_id,
                execution_suite_id=execution_suite_id,
                execution_step_id=execution_step_id,
                phase=phase,
                step_order=step_order,
                action=action_name,
                status=result.get("status", "passed"),
                duration=duration,
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
            if assertion_results:
                if execution_case_id is None:
                    raise ValueError("套件步骤不支持步骤后断言")
                await reporter.assertion_result(execution_step_id, assertion_results)
            if step_status == "failed":
                failed = True
                if not step.get("continue_on_failure", False):
                    halted = True
                    break
        return failed, halted

    async def _run_flow_nodes(
        self,
        nodes: list[dict],
        context: ExecutionContext,
        reporter: RunnerReporter,
        phase: str,
        execution_case_id: int,
    ) -> tuple[bool, bool]:
        """V3：动作/断言共用一个有序节点流。"""
        failed = False
        for node in nodes:
            if self.should_stop():
                raise StopRequested("执行被用户停止")
            node_id = int(node.get("execution_node_id") or 0)
            if node_id <= 0:
                raise ValueError("协议 V3 快照缺少有效 execution_node_id")
            kind = node.get("kind") or ("assertion" if node.get("type") else "action")
            await reporter.node_started(
                node_id,
                execution_case_id=execution_case_id,
                kind=str(kind),
            )
            start = time.monotonic()
            result: dict = {"status": "passed"}
            try:
                if kind == "assertion":
                    assertion_cls = ASSERTION_REGISTRY.get(str(node.get("type") or node.get("assertion_type")))
                    if assertion_cls is None:
                        raise ValueError(f"未知断言: {node.get('type') or node.get('assertion_type')}")
                    effective = dict(node.get("params") or node.get("parameters") or {})
                    if node.get("element_id") is not None:
                        effective.setdefault("element_id", node["element_id"])

                    async def verify_once(assertion_type=assertion_cls, params=effective) -> dict:
                        return await asyncio.to_thread(
                            _run_assertion_in_thread, assertion_type, self.driver, context, params
                        )

                    result = await verify_with_wait(
                        verify_once,
                        max_wait_seconds=float(node.get("max_wait_seconds", 10)),
                        should_stop=self.should_stop,
                    )
                else:
                    action_name = str(node.get("action") or "unknown")
                    if self.parameters.get("attach_to_current_app") and action_name == "launch_app":
                        result = {"status": "passed", "actual_value": "已复用当前设备界面，跳过启动 APP"}
                    else:
                        action_cls = ACTION_REGISTRY.get(action_name)
                        if action_cls is None:
                            raise ValueError(f"未知动作: {action_name}")
                        effective = dict(node.get("params") or node.get("parameters") or {})
                        if node.get("element_id") is not None:
                            effective.setdefault("element_id", node["element_id"])
                        result = await asyncio.to_thread(
                            _run_action_in_thread, action_cls, self.driver, context, effective
                        )
            except StopRequested:
                raise
            except Exception as exc:
                result = {"status": "error", "error_message": str(exc), "attempt_count": 1}
            duration = int((time.monotonic() - start) * 1000)
            await self._resolve_screenshot(result)
            expected = result.get("expected") or (node.get("params") or {}).get("expected")
            status = str(result.get("status") or "error")
            await reporter.node_result(
                node_id, status=status, duration=duration,
                actual_value=str(result.get("actual")) if result.get("actual") is not None else result.get("actual_value"),
                expected_value=str(expected) if expected is not None else None,
                error_message=str(result.get("error_message")) if result.get("error_message") else None,
                screenshot_path=result.get("screenshot_path"),
                attempt_count=result.get("attempt_count"),
                execution_case_id=execution_case_id,
                kind=str(kind),
            )
            await reporter.log(
                "INFO" if status == "passed" else "ERROR",
                f"{phase} 节点 {node.get('order')} 执行{'通过' if status == 'passed' else '失败'}（{duration}ms）",
                int(node.get("order") or 0),
            )
            if status != "passed":
                failed = True
                if not node.get("continue_on_failure", False):
                    return failed, True
        return failed, False

    async def run_case(self, case: dict, reporter: RunnerReporter | None = None) -> str:
        execution_case_id = int(case.get("execution_case_id") or 0)
        if execution_case_id <= 0:
            raise ValueError("协议 V2 快照缺少有效 execution_case_id")
        context = ExecutionContext(
            self.driver,
            case,
            self.parameters.get("variables", {}),
            self.screenshots_dir,
            self.should_stop,
        )
        reporter = reporter or RunnerReporter(self.send, self.execution_id, self.session_token)
        case_status = "passed"

        all_steps = case.get("flow_snapshot") or case.get("steps_snapshot") or []
        if "flow_snapshot" in case:
            setup_nodes = [node for node in all_steps if _case_phase_bucket(node.get("phase")) == "setup"]
            main_nodes = [node for node in all_steps if _case_phase_bucket(node.get("phase")) == "main"]
            teardown_nodes = [node for node in all_steps if _case_phase_bucket(node.get("phase")) == "teardown"]
            case_failed = False
            setup_failed, setup_halted = await self._run_flow_nodes(
                setup_nodes, context, reporter, "setup", execution_case_id
            )
            case_failed = case_failed or setup_failed
            if not setup_halted:
                main_failed, _main_halted = await self._run_flow_nodes(
                    main_nodes, context, reporter, "main", execution_case_id
                )
                case_failed = case_failed or main_failed
            teardown_failed, _teardown_halted = await self._run_flow_nodes(
                teardown_nodes, context, reporter, "teardown", execution_case_id
            )
            case_failed = case_failed or teardown_failed
            return "failed" if case_failed else "passed"
        setup_steps = [s for s in all_steps if _case_phase_bucket(s.get("phase")) == "setup"]
        main_steps = [s for s in all_steps if _case_phase_bucket(s.get("phase")) == "main"]
        teardown_steps = [s for s in all_steps if _case_phase_bucket(s.get("phase")) == "teardown"]

        setup_failed, setup_halted = await self._run_steps(
            setup_steps, context, reporter, "setup", execution_case_id=execution_case_id
        )
        if setup_failed:
            case_status = "failed"
        if not setup_halted:
            main_failed, _main_halted = await self._run_steps(
                main_steps, context, reporter, "main", execution_case_id=execution_case_id
            )
            if main_failed:
                case_status = "failed"

        teardown_failed, _teardown_halted = await self._run_steps(
            teardown_steps, context, reporter, "teardown", execution_case_id=execution_case_id
        )
        if teardown_failed:
            case_status = "failed"
        return case_status

    async def run_suite(self, suite: dict) -> str:
        """执行一个套件：套件前置 → 用例循环 → 套件后置，上报 suite_status 终态。

        停止（StopRequested/CancelledError）不在此收敛，由上层 _run_execution
        统一收敛当前/未开始套件（当前→stopped，未开始→skipped）后上报 execution_result。
        """
        reporter = RunnerReporter(self.send, self.execution_id, self.session_token)
        suite_id = int(suite.get("execution_suite_id") or 0)
        if suite_id <= 0:
            raise ValueError("协议 V2 快照缺少有效 execution_suite_id")
        await reporter.suite_status(suite_id, "running")
        context = ExecutionContext(
            self.driver,
            {"elements_snapshot": suite.get("elements_snapshot") or {}},
            self.parameters.get("variables", {}),
            self.screenshots_dir,
            self.should_stop,
        )
        cases = suite.get("cases") or []
        case_statuses: list[str] = []

        setup_failed, _setup_halted = await self._run_steps(
            suite.get("setup_steps") or [],
            context,
            reporter,
            "suite_setup",
            execution_suite_id=suite_id,
        )
        if setup_failed:
            for case in cases:
                case_id = int(case.get("execution_case_id") or 0)
                await reporter.case_status(case_id, "skipped")
                case_statuses.append("skipped")
        else:
            for case in cases:
                case_id = int(case.get("execution_case_id") or 0)
                await reporter.case_status(case_id, "running")
                cstatus = await self.run_case(case, reporter)
                await reporter.case_status(case_id, cstatus)
                case_statuses.append(cstatus)

        teardown_failed, _teardown_halted = await self._run_steps(
            suite.get("teardown_steps") or [],
            context,
            reporter,
            "suite_teardown",
            execution_suite_id=suite_id,
        )

        statuses = list(case_statuses)
        if setup_failed:
            statuses.append("failed")
        if teardown_failed:
            statuses.append("failed")
        terminal = aggregate_statuses(statuses)
        await reporter.suite_status(suite_id, terminal)
        return terminal

    async def skip_suite(self, suite: dict) -> None:
        """停止收敛：未开始的套件整体标记 skipped（套件 + 其用例）。"""
        reporter = RunnerReporter(self.send, self.execution_id, self.session_token)
        suite_id = int(suite.get("execution_suite_id") or 0)
        await reporter.suite_status(suite_id, "skipped")
        for case in suite.get("cases") or []:
            await reporter.case_status(int(case.get("execution_case_id") or 0), "skipped")

    async def stop_suite(self, suite: dict) -> None:
        """停止收敛：当前正在执行的套件标记 stopped（用例终态由后端收敛）。"""
        reporter = RunnerReporter(self.send, self.execution_id, self.session_token)
        await reporter.suite_status(int(suite.get("execution_suite_id") or 0), "stopped")
