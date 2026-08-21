"""本机绑定管理（Windows 方案 §3.2/§4.1）。

- 机器 PSK 与各用户撤销凭据保存在 CredentialStore；
- 首次绑定创建 Agent 并保存 PSK；追加绑定携带 PSK 只新增关联；
- 用户 Key 不落盘、不写日志。
"""

import logging
from typing import Any

import httpx

from credentials import CredentialStore

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
        """绑定用户 Key。首次绑定保存机器 PSK 与撤销凭据。"""
        psk = self.machine_psk()
        payload: dict[str, Any] = {
            "user_key": user_key,
            "install_id": self.install_id,
        }
        if psk:
            payload["machine_psk"] = psk
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            resp = await client.post(f"{self.base_url}/api/agent/bind", json=payload)
        if resp.status_code not in (200, 201):
            raise BindingError(f"绑定失败: {resp.status_code} {resp.text}")
        body = resp.json()
        if body.get("machine_psk"):
            self.creds.save(MACHINE_PSK_NAME, body["machine_psk"])
        self.creds.save(f"{REVOKE_PREFIX}{body['user_id']}", body["revoke_credential"])
        logger.info("已绑定用户 id=%s（%s）", body["user_id"], "首次绑定" if psk is None else "追加绑定")
        return body

    async def list_users(self) -> list[dict]:
        """机器 PSK 认证，返回已绑定用户。"""
        psk = self.machine_psk()
        if not psk:
            raise BindingError("尚未绑定（无机器 PSK），请先使用用户 Key 绑定")
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            resp = await client.get(
                f"{self.base_url}/api/agent/bindings",
                params={"agent_id": self.install_id},
                headers={"X-Agent-Key": psk},
            )
        if resp.status_code != 200:
            raise BindingError(f"查询绑定失败: {resp.status_code} {resp.text}")
        return resp.json()

    async def unbind(self, binding_id: int, user_id: int) -> None:
        """解绑指定用户（机器 PSK + 该用户撤销凭据）。"""
        psk = self.machine_psk()
        revoke = self.creds.load(f"{REVOKE_PREFIX}{user_id}")
        if not psk or not revoke:
            raise BindingError("缺少机器 PSK 或撤销凭据")
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            resp = await client.delete(
                f"{self.base_url}/api/agent/bindings/{binding_id}",
                params={"agent_id": self.install_id},
                headers={"X-Agent-Key": psk, "X-Revoke-Credential": revoke},
            )
        if resp.status_code != 204:
            raise BindingError(f"解绑失败: {resp.status_code} {resp.text}")
        self.creds.delete(f"{REVOKE_PREFIX}{user_id}")
        logger.info("已解绑用户 id=%s", user_id)
