"""Agent HTTP/WS 请求日志格式化与敏感字段脱敏。"""

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
    "machine_psk",
    "psk",
    "credential",
    "jwt",
    "private_key",
    # 注册负载里的 agent_key 实际是机器 PSK（main.py build_agent_app 的 key_provider
    # → bindings.machine_psk()），必须与 machine_psk 同等脱敏，否则会以明文落盘。
    "agent_key",
)
_MAX_TEXT_LENGTH = 16 * 1024


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(marker in normalized for marker in _SENSITIVE_MARKERS)


def sanitize_for_log(value: Any, *, key: object | None = None) -> Any:
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


def log_http_request(logger, method: str, url: str, *, query=None, body=None, files=None) -> None:
    logger.info(
        "HTTP 请求 %s %s query=%s body=%s files=%s",
        method,
        url,
        format_for_log(query or {}),
        format_for_log(body),
        format_for_log(files or {}),
    )


def log_http_response(logger, method: str, url: str, status_code: int, body: Any = None) -> None:
    logger.info(
        "HTTP 响应 %s %s status=%s body=%s",
        method,
        url,
        status_code,
        format_for_log(body),
    )
