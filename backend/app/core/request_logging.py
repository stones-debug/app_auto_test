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
MAX_BODY_LOG_BYTES = 16 * 1024
_MAX_TEXT_LENGTH = MAX_BODY_LOG_BYTES


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


def sanitize_request_body_for_log(path: str, value: Any) -> Any:
    """Apply endpoint-specific redaction before generic request logging.

    The private profile-variable endpoint intentionally calls its payload field
    ``value`` (rather than ``password``/``token``), so generic key-based
    redaction cannot identify it.  Keep request ids and variable ids visible,
    but never put update values into the request log.
    """
    if not path.rstrip("/").endswith("/my-variables") or not isinstance(value, Mapping):
        return value
    def sanitize_payload(payload: Any) -> Any:
        if not isinstance(payload, Mapping):
            # A truncated JSON preview is often no longer parseable.  Do not
            # emit the raw fragment when the endpoint is known to carry
            # private values.
            return "<redacted>" if isinstance(payload, str) else payload
        sanitized_payload = dict(payload)
        updates = sanitized_payload.get("updates")
        if isinstance(updates, list):
            sanitized_payload["updates"] = [
                {**item, "value": "<redacted>"}
                if isinstance(item, Mapping) and "value" in item
                else item
                for item in updates
            ]
        return sanitized_payload

    sanitized = dict(value)
    if "preview" in sanitized and sanitized.get("truncated"):
        sanitized["preview"] = sanitize_payload(sanitized["preview"])
    else:
        sanitized = sanitize_payload(sanitized)
    return sanitized


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
