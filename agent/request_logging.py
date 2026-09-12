"""Agent HTTP/WS 请求日志格式化与敏感字段脱敏。"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
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
_SENSITIVE_LOG_VALUES: ContextVar[tuple[str, ...]] = ContextVar(
    "agent_sensitive_log_values", default=()
)


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


def _replace_sensitive_text(value: str, secrets: tuple[str, ...]) -> str:
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "<redacted>")
    return result


def _sanitize_log_argument(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return _replace_sensitive_text(value, secrets)
    if isinstance(value, Mapping):
        return {key: _sanitize_log_argument(child, secrets) for key, child in value.items()}
    if isinstance(value, list):
        return [_sanitize_log_argument(child, secrets) for child in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_log_argument(child, secrets) for child in value)
    if isinstance(value, BaseException):
        return _replace_sensitive_text(str(value), secrets)
    if value is not None and str(value) in secrets:
        return "<redacted>"
    return value


@contextmanager
def sensitive_log_context(names: Any, variables: Any):
    """Apply task-local redaction to every Agent logger during execution.

    ContextVars are copied by ``asyncio.to_thread``; the actual params passed to
    an action remain untouched while log records get a safe copy. Empty values
    are intentionally excluded so empty-string variables remain valid values.
    """
    parent = _SENSITIVE_LOG_VALUES.get()
    values = list(parent)
    if isinstance(names, (list, tuple, set, frozenset)) and isinstance(variables, Mapping):
        for name in names:
            value = variables.get(name)
            if value is None or str(value) == "":
                continue
            text = str(value)
            if text not in values:
                values.append(text)
    token = _SENSITIVE_LOG_VALUES.set(tuple(values))
    try:
        yield
    finally:
        _SENSITIVE_LOG_VALUES.reset(token)


_BASE_LOG_RECORD_FACTORY = logging.getLogRecordFactory()


def _safe_log_record_factory(*args, **kwargs):
    record = _BASE_LOG_RECORD_FACTORY(*args, **kwargs)
    secrets = _SENSITIVE_LOG_VALUES.get()
    if not secrets:
        return record
    record.args = _sanitize_log_argument(record.args, secrets)
    record.msg = _replace_sensitive_text(str(record.msg), secrets)
    if record.exc_info is not None:
        # Tracebacks can contain driver/registry exception text. Do not emit an
        # unsanitizable traceback for a sensitive execution.
        record.msg = f"{record.msg} [exception details redacted]"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
    return record


logging.setLogRecordFactory(_safe_log_record_factory)


def sanitize_agent_message_for_log(value: Any) -> Any:
    """Keep WS diagnostics useful without serializing execution values."""
    if not isinstance(value, Mapping):
        return sanitize_for_log(value)
    if value.get("type") == "start_test":
        suites = value.get("suites")
        summary = []
        if isinstance(suites, list):
            for suite in suites:
                if isinstance(suite, Mapping):
                    summary.append({
                        key: suite.get(key)
                        for key in ("execution_suite_id", "suite_id", "suite_order", "is_virtual")
                        if key in suite
                    } | {
                        "case_count": len(suite.get("cases") or [])
                        if isinstance(suite.get("cases"), list) else 0,
                    })
        return sanitize_for_log({
            key: value.get(key)
            for key in ("type", "execution_id", "protocol_version", "device")
            if key in value
        } | {
            "parameters": {"keys": sorted((value.get("parameters") or {}).keys())}
            if isinstance(value.get("parameters"), Mapping) else {},
            "suites": summary,
        })

    def redact(item: Any, *, key: object | None = None) -> Any:
        if key is not None and str(key).lower() in {
            "actual", "actual_value", "expected", "expected_value",
            "error", "error_message", "message", "log", "variables",
        }:
            return "<redacted>"
        if isinstance(item, Mapping):
            return {str(k): redact(v, key=k) for k, v in item.items()}
        if isinstance(item, list):
            return [redact(v) for v in item]
        return sanitize_for_log(item, key=key)

    return redact(value)


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
