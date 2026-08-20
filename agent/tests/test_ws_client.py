import asyncio
import json

import pytest
import websockets

from ws_client import AgentWSClient, AuthError


async def test_ws_client_register_handshake():
    registered = {}

    async def handler(ws):
        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "register"
        assert msg["agent_key"] == "sk-test"
        assert msg["agent_id"] == "agent-1"
        await ws.send(json.dumps({"type": "registered", "agent_id": 42, "status": "ok"}))
        await asyncio.sleep(0.2)
        await ws.close()

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = AgentWSClient(
            url=f"ws://127.0.0.1:{port}/ws/agent",
            agent_key="sk-test",
            agent_id="agent-1",
        )

        async def on_registered(reply):
            registered["reply"] = reply

        client.on_registered = on_registered
        await client.connect()
        assert registered["reply"]["status"] == "ok"
        assert registered["reply"]["agent_id"] == 42
        await client.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_ws_client_auth_failure_stops():
    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "error", "code": "AUTH_FAILED"}))
        await ws.close()

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = AgentWSClient(
            url=f"ws://127.0.0.1:{port}/ws/agent",
            agent_key="sk-wrong",
            agent_id="agent-1",
        )
        with pytest.raises(AuthError):
            await client.connect()
        await client.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_ws_client_send_receive():
    received = []
    got_message = asyncio.Event()

    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "registered", "agent_id": 1, "status": "ok"}))
        msg = json.loads(await ws.recv())
        assert msg["type"] == "heartbeat"
        await ws.send(json.dumps({"type": "start_test", "execution_id": 5}))
        await asyncio.sleep(0.3)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async def on_message(message):
            received.append(message)
            got_message.set()

        client = AgentWSClient(f"ws://127.0.0.1:{port}/ws/agent", "sk-test", "agent-1")
        client.on_message = on_message
        await client.connect()
        recv_task = asyncio.create_task(client._receive_loop())
        await client.send({"type": "heartbeat"})
        await asyncio.wait_for(got_message.wait(), timeout=2)
        assert received and received[0]["type"] == "start_test"
        recv_task.cancel()
        await client.close()
    finally:
        server.close()
        await server.wait_closed()
