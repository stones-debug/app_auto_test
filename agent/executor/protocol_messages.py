"""Agent 出站消息类型（Step 10：RunnerReporter 不再拼接裸 dict）。"""

from typing import NotRequired, TypedDict


class StepResultMessage(TypedDict):
    type: str
    execution_id: int
    session_token: str | None
    case_id: int
    step_order: int
    action: str
    status: str
    duration: int
    actual_value: str | None
    error_message: str | None
    screenshot_path: str | None


class AssertionItem(TypedDict):
    type: str
    expected: str
    actual: str
    status: str
    error_message: str | None


class AssertionResultMessage(TypedDict):
    type: str
    execution_id: int
    session_token: str | None
    case_id: int
    assertions: list[AssertionItem]


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
