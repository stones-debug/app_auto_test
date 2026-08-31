"""请求日志格式化与敏感字段脱敏。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

_SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "user_key",
    "agent_key",
    "machine_psk",
    "psk",
    "credential",
    "jwt",
    "private_key",
)
_MAX_TEXT_LENGTH = 16 * 1024


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(marker in normalized for marker in _SENSITIVE_MARKERS)


def sanitize_for_log(value: Any, *, key: object | None = None) -> Any:
    """递归脱敏，保留普通请求参数，避免凭据进入日志。"""
    if key is not None and _is_sensitive_key(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): sanitize_for_log(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_log(item) for item in value]
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, str) and len(value) > _MAX_TEXT_LENGTH:
        return f"{value[:_MAX_TEXT_LENGTH]}...<truncated>"
    return value


def format_for_log(value: Any) -> str:
    sanitized = sanitize_for_log(value)
    try:
        return json.dumps(sanitized, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(sanitized)


def parse_body_for_log(raw_body: bytes, content_type: str) -> Any:
    if not raw_body:
        return None
    if "multipart/form-data" in content_type:
        return {"content_type": content_type, "size": len(raw_body)}
    if "application/json" in content_type:
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    try:
        return raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return f"<bytes:{len(raw_body)}>"
