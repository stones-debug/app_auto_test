import json

import pytest

from app.core.request_logging import MAX_BODY_LOG_BYTES
from app.main import RequestLoggingMiddleware


def _scope(content_type: str, content_length: int | None = None, *, path: str = "/test", method: str = "POST") -> dict:
    headers = [(b"content-type", content_type.encode())]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {"type": "http", "method": method, "path": path, "headers": headers, "query_string": b""}


async def _run_middleware(scope: dict, messages: list[dict]) -> tuple[list[dict], list[int]]:
    remaining = list(messages)
    receive_calls = [0]
    body_messages: list[dict] = []

    async def receive() -> dict:
        receive_calls[0] += 1
        return remaining.pop(0)

    async def send(_message: dict) -> None:
        return None

    async def downstream(_scope: dict, downstream_receive, downstream_send) -> None:
        while True:
            body_message = await downstream_receive()
            body_messages.append(body_message)
            if not body_message.get("more_body", False):
                break
        await downstream_send({"type": "http.response.start", "status": 204, "headers": []})
        await downstream_send({"type": "http.response.body", "body": b""})

    await RequestLoggingMiddleware(downstream)(scope, receive, send)
    return body_messages, receive_calls


@pytest.mark.asyncio
async def test_multipart_body_is_not_prefetched(caplog):
    body = b"--boundary\r\nlarge upload\r\n"
    caplog.set_level("INFO", logger="app.request")

    body_messages, receive_calls = await _run_middleware(
        _scope("multipart/form-data; boundary=boundary", len(body)),
        [{"type": "http.request", "body": body, "more_body": False}],
    )

    assert receive_calls == [1]
    assert body_messages[0]["body"] == body
    assert '"skipped":"multipart"' in caplog.records[0].message


@pytest.mark.asyncio
async def test_large_body_is_not_prefetched(caplog):
    body = b"x" * (MAX_BODY_LOG_BYTES + 1)
    caplog.set_level("INFO", logger="app.request")

    body_messages, receive_calls = await _run_middleware(
        _scope("application/json", len(body)),
        [{"type": "http.request", "body": body, "more_body": False}],
    )

    assert receive_calls == [1]
    assert body_messages[0]["body"] == body
    assert '"skipped":"too_large"' in caplog.records[0].message


@pytest.mark.asyncio
async def test_small_json_is_replayed_without_changing_body(caplog):
    body = json.dumps({"name": "demo"}).encode()
    caplog.set_level("INFO", logger="app.request")

    body_messages, receive_calls = await _run_middleware(
        _scope("application/json", len(body)),
        [{"type": "http.request", "body": body, "more_body": False}],
    )

    assert receive_calls == [1]
    assert b"".join(message["body"] for message in body_messages) == body
    assert '"name":"demo"' in caplog.records[0].message


@pytest.mark.asyncio
async def test_unknown_length_json_only_prefetches_bounded_preview():
    chunks = [
        {"type": "http.request", "body": b"a" * MAX_BODY_LOG_BYTES, "more_body": True},
        {"type": "http.request", "body": b"b", "more_body": True},
        {"type": "http.request", "body": b"tail", "more_body": False},
    ]

    body_messages, receive_calls = await _run_middleware(_scope("application/json"), chunks)

    # 中间件只预读到超过日志上限的边界，剩余消息由下游继续读取。
    assert receive_calls == [3]
    assert b"".join(message["body"] for message in body_messages) == b"a" * MAX_BODY_LOG_BYTES + b"b" + b"tail"


@pytest.mark.asyncio
async def test_profile_variable_values_are_redacted_in_request_log(caplog):
    body = json.dumps({
        "request_id": "request-visible",
        "updates": [{"variable_id": 42, "value": "private-value"}],
    }).encode()
    caplog.set_level("INFO", logger="app.request")

    await _run_middleware(
        _scope("application/json", path="/api/app-profiles/7/my-variables", method="PATCH"),
        [{"type": "http.request", "body": body, "more_body": False}],
    )

    assert "private-value" not in caplog.records[0].message
    assert '"request_id":"request-visible"' in caplog.records[0].message
    assert '"variable_id":42' in caplog.records[0].message
    assert '"value":"<redacted>"' in caplog.records[0].message


@pytest.mark.asyncio
async def test_truncated_profile_variable_preview_is_redacted(caplog):
    body = json.dumps({
        "request_id": "request-visible",
        "updates": [{"variable_id": 42, "value": "private-preview-value"}],
        "padding": "x" * MAX_BODY_LOG_BYTES,
    }).encode()
    caplog.set_level("INFO", logger="app.request")

    await _run_middleware(
        _scope("application/json", path="/api/app-profiles/7/my-variables", method="PATCH"),
        [{"type": "http.request", "body": body, "more_body": False}],
    )

    assert "private-preview-value" not in caplog.records[0].message
