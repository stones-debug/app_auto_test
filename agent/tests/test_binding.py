"""Windows 方案 §3.2/§4.1：本机绑定管理测试（httpx MockTransport 模拟后端）。"""

import json

import httpx
import pytest

from binding import BindingError, BindingManager
from credentials import CredentialStore, FileCredentialBackend

INSTALL_ID = "agent-test-001"


def _handler_factory(requests_log: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append({"method": request.method, "url": str(request.url), "headers": dict(request.headers), "body": request.content})
        if request.url.path.endswith("/api/agent/bind"):
            body = json.loads(request.content)
            user_id = 2 if body["user_key"] == "uak_pub2_sec" else 1
            sequence = len([item for item in requests_log if item["url"].endswith("/api/agent/bind")])
            return httpx.Response(
                201,
                json={
                    "agent_id": INSTALL_ID,
                    "machine_psk": f"sk-rotated-{sequence}",
                    "revoke_credential": f"rev-{user_id}",
                    "user_id": user_id,
                },
            )
        if "/api/agent/bindings" in request.url.path:
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json=[{"id": 1, "user_id": 1, "username": "alice"}])
        return httpx.Response(404)

    return handler


def _make_manager(tmp_path, requests_log: list[dict]) -> tuple[BindingManager, CredentialStore]:
    creds = CredentialStore(backend=FileCredentialBackend(tmp_path))
    transport = httpx.MockTransport(_handler_factory(requests_log))
    manager = BindingManager(
        "http://test-server", INSTALL_ID, creds, transport=transport
    )
    return manager, creds


async def test_first_bind_stores_machine_psk_and_revoke(tmp_path):
    requests_log: list[dict] = []
    manager, creds = _make_manager(tmp_path, requests_log)

    result = await manager.bind("uak_pub_sec")
    assert result["machine_psk"] == "sk-rotated-1"
    assert creds.load("machine_psk") == "sk-rotated-1"
    assert creds.load("revoke_1") == "rev-1"
    # 首次绑定请求不含 machine_psk，且走 {origin}/api/agent/bind
    bind_req = json.loads(requests_log[0]["body"])
    assert "machine_psk" not in bind_req
    assert requests_log[0]["url"] == "http://test-server/api/agent/bind"


async def test_second_bind_only_sends_user_key_and_refreshes_machine_psk(tmp_path):
    requests_log: list[dict] = []
    manager, creds = _make_manager(tmp_path, requests_log)
    creds.save("machine_psk", "sk-first")

    result = await manager.bind("uak_pub2_sec")
    assert result["user_id"] == 2
    assert creds.load("revoke_2") == "rev-2"
    bind_req = json.loads(requests_log[0]["body"])
    assert "machine_psk" not in bind_req
    assert creds.load("machine_psk") == "sk-rotated-1"


async def test_bind_replaces_stale_machine_psk(tmp_path):
    requests_log: list[dict] = []
    manager, creds = _make_manager(tmp_path, requests_log)
    creds.save("machine_psk", "sk-wrong")

    result = await manager.bind("uak_pub_sec")
    assert result["machine_psk"] == "sk-rotated-1"
    assert creds.load("machine_psk") == "sk-rotated-1"
    assert "machine_psk" not in json.loads(requests_log[0]["body"])


async def test_list_users_requires_machine_psk(tmp_path):
    manager, _creds = _make_manager(tmp_path, [])
    with pytest.raises(BindingError, match="机器 PSK"):
        await manager.list_users()

    requests_log: list[dict] = []
    manager2, creds2 = _make_manager(tmp_path, requests_log)
    creds2.save("machine_psk", "sk-first")
    users = await manager2.list_users()
    assert users[0]["username"] == "alice"
    assert requests_log[0]["headers"]["x-agent-key"] == "sk-first"


async def test_unbind_uses_revoke_credential(tmp_path):
    requests_log: list[dict] = []
    manager, creds = _make_manager(tmp_path, requests_log)
    creds.save("machine_psk", "sk-first")
    creds.save("revoke_1", "rev-1")

    await manager.unbind(1, 1)
    assert requests_log[0]["method"] == "DELETE"
    assert requests_log[0]["headers"]["x-revoke-credential"] == "rev-1"
    assert creds.load("revoke_1") is None  # 解绑后撤销凭据清除


async def test_unbind_without_revoke_raises(tmp_path):
    manager, creds = _make_manager(tmp_path, [])
    creds.save("machine_psk", "sk-first")
    with pytest.raises(BindingError, match="撤销凭据"):
        await manager.unbind(1, 99)
