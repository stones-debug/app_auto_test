"""日志脱敏：确保凭据不会以明文进入日志。

对应 Review B-1：`ws_client.connect()` 会把 register 负载整包 INFO 打印，
而 `agent_key` 的实际取值是机器 PSK（`main.build_agent_app` 的 key_provider
→ `bindings.machine_psk()`），此前未在 `_SENSITIVE_MARKERS` 中，导致明文落盘。
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
import websockets

from request_logging import format_for_log, sanitize_for_log
from ws_client import AgentWSClient

MACHINE_PSK = "psk-plaintext-should-never-appear"

# 注册负载的真实形状（ws_client.AgentWSClient.connect）
REGISTER_PAYLOAD = {
    "type": "register",
    "agent_key": MACHINE_PSK,
    "agent_id": "install-abc",
    "hostname": "qa-host-01",
    "platform": "windows",
    "version": "1.2.3",
    "protocol_version": "3.3.0",
}


def test_register_payload_never_leaks_machine_psk():
    """整包格式化后，机器 PSK 不得出现在日志文本中。"""
    rendered = format_for_log(REGISTER_PAYLOAD)
    assert MACHINE_PSK not in rendered
    assert "agent_key" in rendered
    assert "<redacted>" in rendered


def test_agent_key_redacted_but_harmless_fields_kept():
    """脱敏只针对凭据字段，排查问题需要的字段必须保留。"""
    sanitized = sanitize_for_log(REGISTER_PAYLOAD)
    assert isinstance(sanitized, dict)
    assert sanitized["agent_key"] == "<redacted>"
    assert sanitized["agent_id"] == "install-abc"
    assert sanitized["hostname"] == "qa-host-01"
    assert sanitized["protocol_version"] == "3.3.0"


def test_sensitive_marker_covers_key_name_variants():
    """`-` / `_` 与大小写变体都要能命中。"""
    for key in ("agent_key", "Agent-Key", "AGENT_KEY"):
        assert sanitize_for_log("secret-value", key=key) == "<redacted>"


def test_http_body_with_agent_key_is_redacted():
    """HTTP 侧同样走这套标记（上传/绑定请求体里也带 agent_key）。"""
    rendered = format_for_log({"agent_key": MACHINE_PSK, "execution_id": 7})
    assert MACHINE_PSK not in rendered
    assert json.loads(rendered)["execution_id"] == 7


def test_nested_payload_is_redacted_recursively():
    rendered = format_for_log({"outer": {"agent_key": MACHINE_PSK}, "list": [{"agent_key": MACHINE_PSK}]})
    assert MACHINE_PSK not in rendered


async def test_ws_client_connect_does_not_log_machine_psk(caplog: pytest.LogCaptureFixture):
    """端到端回归：`connect()` 打出的连接日志里不得出现机器 PSK。

    这是 B-1 的直接回归守卫——只测 `format_for_log` 无法发现
    `ws_client` 绕过脱敏自行拼字符串的写法。
    """

    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "registered", "agent_id": 42, "status": "ok"}))
        await asyncio.sleep(0.05)
        await ws.close()

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = AgentWSClient(
            url=f"ws://127.0.0.1:{port}/ws/agent",
            agent_key=MACHINE_PSK,
            agent_id="install-abc",
        )
        with caplog.at_level(logging.INFO, logger="agent.ws"):
            await client.connect()
        assert caplog.records, "connect() 未产生任何日志，回归守卫失效"
    finally:
        server.close()
        await server.wait_closed()

    for record in caplog.records:
        assert MACHINE_PSK not in record.getMessage(), f"机器 PSK 出现在日志中: {record.getMessage()}"
