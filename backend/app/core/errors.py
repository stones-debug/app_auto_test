"""领域错误统一结构（V2 §7.2）。

后端业务错误通过 `HTTPException(detail={code, message, field_errors})` 返回；
本模块提供 `api_error()` 便捷构造器，并兼容既有字符串 `detail`（保持现状，
前端请求层同时解析字符串 detail / 对象 detail / 422 数组）。
"""

from typing import Any

from fastapi import HTTPException


def api_error(
    status_code: int,
    code: str,
    message: str,
    field_errors: dict[str, str] | None = None,
) -> HTTPException:
    """构造结构化业务错误。

    示例：`raise api_error(409, "DEVICE_BUSY", "设备忙或已被其他执行占用")`
    """
    detail: dict[str, Any] = {"code": code, "message": message, "field_errors": field_errors}
    return HTTPException(status_code=status_code, detail=detail)


def error_message(detail: Any) -> str:
    """从 FastAPI detail（字符串或对象）提取展示消息，供前端统一解析。"""
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or "")
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(x) for x in item.get("loc", []))
                parts.append(f"{loc}: {item.get('msg', '')}" if loc else item.get("msg", ""))
            else:
                parts.append(str(item))
        return "; ".join(p for p in parts if p)
    return str(detail or "")
