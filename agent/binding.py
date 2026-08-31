"""本机绑定管理（Windows 方案 §3.2/§4.1）。

- 机器 PSK 与各用户撤销凭据保存在 CredentialStore；
- 用户 Key 是绑定阶段的唯一凭证；每次成功绑定保存服务端旋转后的机器 PSK；
- BindingManager 不记录用户 Key、不写日志；桌面端可按界面配置单独回填。
"""

import logging
from typing import Any

import httpx

from credentials import CredentialStore
from request_logging import log_http_request, log_http_response

logger = logging.getLogger("agent.binding")

REVOKE_PREFIX = "revoke_"
MACHINE_PSK_NAME = "machine_psk"


class BindingError(Exception):
    pass


class BindingManager:
    def __init__(
        self,
        base_url: str,
        install_id: str,
        creds: CredentialStore,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.install_id = install_id
        self.creds = creds
        self._transport = transport
        self._timeout = timeout

    def machine_psk(self) -> str | None:
        return self.creds.load(MACHINE_PSK_NAME)

    async def bind(self, user_key: str) -> dict:
        """仅使用用户 Key 绑定，并保存服务端刷新后的机器凭据。"""
        payload: dict[str, Any] = {
            "user_key": user_key,
            "install_id": self.install_id,
        }
        url = f"{self.base_url}/api/agent/bind"
        log_http_request(logger, "POST", url, body=payload)
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, trust_env=False
        ) as client:
            resp = await client.post(url, json=payload)
        log_http_response(logger, "POST", url, resp.status_code, resp.text)
        if resp.status_code not in (200, 201):
            raise BindingError(f"绑定失败: {resp.status_code} {resp.text}")
        body = resp.json()
        self.creds.save(MACHINE_PSK_NAME, body["machine_psk"])
        self.creds.save(f"{REVOKE_PREFIX}{body['user_id']}", body["revoke_credential"])
        logger.info("已绑定用户 id=%s，机器凭据已刷新", body["user_id"])
        return body

    async def list_users(self) -> list[dict]:
        """机器 PSK 认证，返回已绑定用户。"""
        psk = self.machine_psk()
        if not psk:
            raise BindingError("尚未绑定（无机器 PSK），请先使用用户 Key 绑定")
        url = f"{self.base_url}/api/agent/bindings"
        query = {"agent_id": self.install_id}
        log_http_request(logger, "GET", url, query=query, body={"headers": ["X-Agent-Key"]})
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, trust_env=False
        ) as client:
            resp = await client.get(
                url,
                params=query,
                headers={"X-Agent-Key": psk},
            )
        log_http_response(logger, "GET", url, resp.status_code, resp.text)
        if resp.status_code != 200:
            raise BindingError(f"查询绑定失败: {resp.status_code} {resp.text}")
        return resp.json()

    async def unbind(self, binding_id: int, user_id: int) -> None:
        """解绑指定用户（机器 PSK + 该用户撤销凭据）。"""
        psk = self.machine_psk()
        revoke = self.creds.load(f"{REVOKE_PREFIX}{user_id}")
        if not psk or not revoke:
            raise BindingError("缺少机器 PSK 或撤销凭据")
        url = f"{self.base_url}/api/agent/bindings/{binding_id}"
        query = {"agent_id": self.install_id}
        log_http_request(logger, "DELETE", url, query=query, body={"headers": ["X-Agent-Key", "X-Revoke-Credential"]})
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, trust_env=False
        ) as client:
            resp = await client.delete(
                url,
                params=query,
                headers={"X-Agent-Key": psk, "X-Revoke-Credential": revoke},
            )
        log_http_response(logger, "DELETE", url, resp.status_code, resp.text)
        if resp.status_code != 204:
            raise BindingError(f"解绑失败: {resp.status_code} {resp.text}")
        self.creds.delete(f"{REVOKE_PREFIX}{user_id}")
        logger.info("已解绑用户 id=%s", user_id)
