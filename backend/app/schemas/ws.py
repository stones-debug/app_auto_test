"""Agent WS 消息类型（Step 10：agent_ws 按 type 验证 payload）。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentRegisterIn(BaseModel):
    type: Literal["register"]
    agent_id: str
    agent_key: str
    version: str | None = None
    hostname: str | None = None
    platform: str | None = None


class HeartbeatIn(BaseModel):
    type: Literal["heartbeat"]


class DeviceListIn(BaseModel):
    type: Literal["device_list"]
    devices: list[dict[str, Any]] = Field(default_factory=list)


class AgentLogIn(BaseModel):
    type: Literal["log"]
    execution_id: int
    session_token: str | None = None
    level: str = "INFO"
    message: str = ""
    step_order: int | None = None
    timestamp: str | None = None


class AgentAssertionIn(BaseModel):
    type: str = ""
    execution_assertion_id: int
    assertion_order: int | None = None
    expected: str | None = None
    actual: str | None = None
    status: str = "pass"
    error_message: str | None = None


class StepResultIn(BaseModel):
    type: Literal["step_result"]
    execution_id: int
    session_token: str | None = None
    execution_suite_id: int | None = None
    execution_case_id: int | None = None
    execution_step_id: int
    phase: str | None = None
    step_order: int | None = None
    action: str = "unknown"
    status: str = "passed"
    duration: int | None = None
    actual_value: str | None = None
    error_message: str | None = None
    screenshot_path: str | None = None


class AssertionResultIn(BaseModel):
    type: Literal["assertion_result"]
    execution_id: int
    session_token: str | None = None
    execution_step_id: int
    step_order: int | None = None
    assertions: list[AgentAssertionIn] = Field(default_factory=list)


class CaseStatusIn(BaseModel):
    type: Literal["case_status"]
    execution_id: int
    session_token: str | None = None
    execution_case_id: int
    status: str = "running"
    error_message: str | None = None


class SuiteStatusIn(BaseModel):
    type: Literal["suite_status"]
    execution_id: int
    session_token: str | None = None
    execution_suite_id: int
    status: str = "running"
    error_message: str | None = None


class ExecutionResultIn(BaseModel):
    type: Literal["execution_result"]
    execution_id: int
    session_token: str | None = None
    status: str = "error"
    error_message: str | None = None


_AGENT_MODELS: dict[str, type[BaseModel]] = {
    "register": AgentRegisterIn,
    "heartbeat": HeartbeatIn,
    "device_list": DeviceListIn,
    "log": AgentLogIn,
    "step_result": StepResultIn,
    "assertion_result": AssertionResultIn,
    "case_status": CaseStatusIn,
    "suite_status": SuiteStatusIn,
    "execution_result": ExecutionResultIn,
}


def validate_agent_message(payload: dict) -> dict | None:
    """按 type 验证 Agent 入站消息；非法返回 None（调用方回结构化协议错误）。"""
    msg_type = payload.get("type")
    model = _AGENT_MODELS.get(msg_type)
    if model is None:
        return None
    validated = model.model_validate(payload)
    return validated.model_dump(exclude_none=True)
