"""Agent 出站消息类型（Step 10：RunnerReporter 不再拼接裸 dict）。"""

from typing import NotRequired, TypedDict


class StepResultMessage(TypedDict):
    type: str
    execution_id: int
    session_token: str | None
    execution_suite_id: NotRequired[int | None]
    execution_case_id: NotRequired[int | None]
    execution_step_id: NotRequired[int | None]
    phase: str
    step_order: int
    action: str
    status: str
    duration: int
    actual_value: str | None
    error_message: str | None
    screenshot_path: str | None


class AssertionItem(TypedDict):
    execution_assertion_id: NotRequired[int | None]
    assertion_order: NotRequired[int]
    type: str
    expected: str
    actual: str
    status: str
    error_message: str | None


class AssertionResultMessage(TypedDict):
    type: str
    execution_id: int
    session_token: str | None
    execution_case_id: int
    assertions: list[AssertionItem]


class SuiteStatusMessage(TypedDict):
    type: str
    execution_id: int
    session_token: str | None
    execution_suite_id: int
    status: str
    error_message: NotRequired[str]


class CaseStatusMessage(TypedDict):
    type: str
    execution_id: int
    session_token: str | None
    execution_case_id: int
    status: str
    error_message: NotRequired[str]


class ExecutionResultMessage(TypedDict):
    type: str
    execution_id: int
    session_token: str | None
    status: str
    error_message: NotRequired[str]


class DeviceListMessage(TypedDict):
    type: str
    devices: list[dict]


class LogMessage(TypedDict):
    type: str
    execution_id: int
    session_token: str | None
    level: str
    message: str
    step_order: NotRequired[int | None]
    timestamp: NotRequired[str]
