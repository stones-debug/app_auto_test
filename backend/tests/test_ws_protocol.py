"""Step 10：WS 消息类型校验（缺字段/错类型/未知 type → 结构化协议错误，不污染 DB）。"""

import pytest
from pydantic import ValidationError

from app.schemas.ws import validate_agent_message


def test_validate_agent_message_unknown_type():
    assert validate_agent_message({"type": "hack"}) is None


def test_validate_agent_message_missing_required_field():
    # step_result 缺 case_id/step_order → ValidationError（路由层转协议错误）
    with pytest.raises(ValidationError):
        validate_agent_message({"type": "step_result", "execution_id": 1})
    with pytest.raises(ValidationError):
        validate_agent_message({"type": "log", "message": "x"})
    with pytest.raises(ValidationError):
        validate_agent_message({"type": "register", "agent_id": "a"})


def test_validate_agent_message_wrong_type():
    # execution_id 必须是 int
    with pytest.raises(ValidationError):
        validate_agent_message({"type": "log", "execution_id": "abc"})


def test_validate_agent_message_ok_shapes():
    valid = validate_agent_message(
        {"type": "log", "execution_id": 7, "level": "INFO", "message": "hi", "step_order": 2}
    )
    assert valid is not None
    assert valid["step_order"] == 2

    step = validate_agent_message(
        {
            "type": "step_result",
            "execution_id": 7,
            "case_id": 3,
            "step_order": 1,
            "status": "passed",
            "assertions": [{"type": "text_equals", "expected": "a", "actual": "a", "status": "pass"}],
        }
    )
    assert step is not None
    assert step["assertions"][0]["status"] == "pass"

    result = validate_agent_message(
        {"type": "execution_result", "execution_id": 7, "status": "passed"}
    )
    assert result is not None
    assert result["status"] == "passed"


def test_protocol_error_never_reaches_handlers():
    """路由层 try/except 语义：validate 抛错时合法消息之外一律不进入 handler。"""
    # 非法消息不应被进一步处理：valid=None / raise 即协议错误
    for bad in (
        {"type": "step_result", "execution_id": 1},
        {"type": "log", "execution_id": "bad"},
        {"type": "execution_result", "status": "passed"},  # 缺 execution_id
        {"type": "register", "agent_key": "k"},  # 缺 agent_id
    ):
        with pytest.raises(ValidationError):
            validate_agent_message(bad)
