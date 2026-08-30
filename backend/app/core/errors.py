"""HTTP 业务错误的统一机器可读契约。"""

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field


class ErrorCode:
    """核心端点使用的稳定错误码；新增端点沿用同一大写下划线约定。"""

    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_CREDENTIALS_INVALID = "AUTH_CREDENTIALS_INVALID"
    AUTH_USER_EXISTS = "AUTH_USER_EXISTS"
    AUTH_USER_DISABLED = "AUTH_USER_DISABLED"
    AUTH_REFRESH_INVALID = "AUTH_REFRESH_INVALID"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    PROJECT_FORBIDDEN = "PROJECT_FORBIDDEN"
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_FORBIDDEN = "AGENT_FORBIDDEN"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    DEVICE_FORBIDDEN = "DEVICE_FORBIDDEN"
    PLATFORM_ADMIN_REQUIRED = "PLATFORM_ADMIN_REQUIRED"
    INTERNAL_TOKEN_INVALID = "INTERNAL_TOKEN_INVALID"
    DEVICE_REQUIRED = "DEVICE_REQUIRED"
    DEVICE_BUSY = "DEVICE_BUSY"
    AGENT_OFFLINE = "AGENT_OFFLINE"
    EXECUTION_NOT_FOUND = "EXECUTION_NOT_FOUND"
    EXECUTION_NOT_STOPPABLE = "EXECUTION_NOT_STOPPABLE"
    APP_PROFILE_REQUIRED = "APP_PROFILE_REQUIRED"
    APP_PROFILE_NOT_FOUND = "APP_PROFILE_NOT_FOUND"
    APP_RELEASE_NOT_FOUND = "APP_RELEASE_NOT_FOUND"
    APP_RELEASE_EXISTS = "APP_RELEASE_EXISTS"
    PROFILE_REVISION_CONFLICT = "PROFILE_REVISION_CONFLICT"
    TEST_ASSET_REVISION_CONFLICT = "TEST_ASSET_REVISION_CONFLICT"
    APP_RELEASE_CHANGED = "APP_RELEASE_CHANGED"
    PROFILE_EMPTY = "PROFILE_EMPTY"
    PROFILE_RULE_INVALID = "PROFILE_RULE_INVALID"
    SNAPSHOT_TOO_LARGE = "SNAPSHOT_TOO_LARGE"


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class ApiErrorResponse(BaseModel):
    detail: ApiErrorDetail


class ApiError(Exception):
    """领域错误对象，可在服务层构造并由 API 层转成 HTTPException。"""

    def __init__(self, status_code: int, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.context = context or {}
        super().__init__(message)

    def as_http_exception(self) -> HTTPException:
        return HTTPException(status_code=self.status_code, detail=self.detail())

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "context": self.context}


def api_error(
    status_code: int,
    code: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> HTTPException:
    """构造统一结构的 FastAPI 异常。"""
    return ApiError(status_code, code, message, context).as_http_exception()


def error_message(detail: Any) -> str:
    """从 FastAPI detail 提取展示消息，供日志和边界层使用。"""
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or "")
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(value) for value in item.get("loc", []))
                parts.append(f"{loc}: {item.get('msg', '')}" if loc else item.get("msg", ""))
            else:
                parts.append(str(item))
        return "; ".join(part for part in parts if part)
    return str(detail or "")
