"""B1：领域错误结构规范（V2 §7.2）。

- `api_error` 生成 `detail={code, message, field_errors}`；
- 保持对既有字符串 detail / 422 数组的兼容解析；
- PageResult 统一携带 page/page_size。
"""

from fastapi import HTTPException

from app.core.errors import api_error, error_message


def test_api_error_structure():
    exc = api_error(409, "DEVICE_BUSY", "设备忙或已被其他执行占用")
    assert isinstance(exc, HTTPException)
    assert exc.status_code == 409
    assert exc.detail == {"code": "DEVICE_BUSY", "message": "设备忙或已被其他执行占用", "field_errors": None}


def test_api_error_with_field_errors():
    exc = api_error(422, "INVALID_SCOPE", "变量作用域不合法", {"scope": "必须提供 project_id"})
    assert exc.detail["code"] == "INVALID_SCOPE"
    assert exc.detail["field_errors"] == {"scope": "必须提供 project_id"}


def test_error_message_string_detail():
    assert error_message("用户不存在") == "用户不存在"


def test_error_message_object_detail():
    detail = {"code": "DEVICE_BUSY", "message": "设备忙或已被其他执行占用", "field_errors": None}
    assert error_message(detail) == "设备忙或已被其他执行占用"


def test_error_message_422_array():
    detail = [
        {"loc": ["body", "name"], "msg": "Field required"},
        {"loc": ["query", "page"], "msg": "Input should be a valid integer"},
    ]
    msg = error_message(detail)
    assert "body.name: Field required" in msg
    assert "query.page: Input should be a valid integer" in msg


def test_error_message_empty():
    assert error_message(None) == ""
    assert error_message("") == ""
