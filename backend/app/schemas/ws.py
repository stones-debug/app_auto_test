"""Agent WS 消息类型（Step 10：agent_ws 按 type 验证 payload）。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentRegisterIn(BaseModel):
    type: Literal["register"]
    agent_id: str
    agent_key: str
    version: str | None = None
    protocol_version: str | None = None
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


# Step 7.2：状态字段收紧为 Literal——非法状态在消息验证层直接返回
# PROTOCOL_ERROR，不再静默转换为 running 或写入任意字符串。
NodeStatus = Literal["running", "passed", "failed", "error", "stopped", "skipped"]
ExecutionStatus = Literal["passed", "failed", "error", "stopped", "cancelled"]


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
    status: Literal["passed", "failed", "error", "stopped", "skipped"] = "passed"
    duration: int | None = None
    actual_value: str | None = None
    error_message: str | None = None
    screenshot_path: str | None = None


class NodeStartedIn(BaseModel):
    type: Literal["node_started"]
    execution_id: int
    session_token: str | None = None
    execution_node_id: int
    execution_suite_id: int | None = None
    execution_case_id: int | None = None
    kind: Literal["action", "assertion"] | None = None
    attempt: int | None = Field(default=None, ge=1)


class NodeResultIn(BaseModel):
    type: Literal["node_result"]
    execution_id: int
    session_token: str | None = None
    execution_node_id: int
    execution_suite_id: int | None = None
    execution_case_id: int | None = None
    kind: Literal["action", "assertion"] | None = None
    status: Literal["passed", "failed", "error", "stopped", "skipped"] = "passed"
    duration: int | None = None
    actual_value: str | None = None
    expected_value: str | None = None
    error_message: str | None = None
    screenshot_path: str | None = None
    attempt_count: int | None = Field(default=None, ge=0)


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
    status: NodeStatus = "running"
    error_message: str | None = None


class SuiteStatusIn(BaseModel):
    type: Literal["suite_status"]
    execution_id: int
    session_token: str | None = None
    execution_suite_id: int
    status: NodeStatus = "running"
    error_message: str | None = None


class ExecutionResultIn(BaseModel):
    type: Literal["execution_result"]
    execution_id: int
    session_token: str | None = None
    status: ExecutionStatus = "error"
    error_message: str | None = None


_AGENT_MODELS: dict[str, type[BaseModel]] = {
    "register": AgentRegisterIn,
    "heartbeat": HeartbeatIn,
    "device_list": DeviceListIn,
    "log": AgentLogIn,
    "step_result": StepResultIn,
    "node_started": NodeStartedIn,
    "node_result": NodeResultIn,
    "assertion_result": AssertionResultIn,
    "case_status": CaseStatusIn,
    "suite_status": SuiteStatusIn,
    "execution_result": ExecutionResultIn,
}


def validate_agent_message(payload: dict) -> dict | None:
    """按 type 验证 Agent 入站消息；非法返回 None（调用方回结构化协议错误）。"""
    msg_type = payload.get("type")
    model = _AGENT_MODELS.get(msg_type) if isinstance(msg_type, str) else None
    if model is None:
        return None
    validated = model.model_validate(payload)
    return validated.model_dump(exclude_none=True)
