import logging
from collections.abc import Callable
from pathlib import Path

import httpx

from request_logging import log_http_request, log_http_response

logger = logging.getLogger("agent.uploader")

KeyProvider = str | Callable[[], str]


class Uploader:
    """截图等文件经 HTTP 上传到服务器（§10.7），返回相对路径。

    agent_key 支持回调（绑定后无需重启即可用新机器 PSK 上传）。
    """

    def __init__(
        self,
        base_url: str,
        agent_key: KeyProvider,
        agent_id: str,
        max_size: int = 10 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_key = agent_key
        self.agent_id = agent_id
        self.max_size = max_size
        self._transport = transport

    def _resolve_key(self) -> str:
        key = self.agent_key() if callable(self.agent_key) else self.agent_key
        return key or ""

    async def upload_screenshot(self, execution_id: int, path: str, session_token: str | None = None) -> str | None:
        file_path = Path(path)
        try:
            if not file_path.exists():
                logger.warning("截图不存在或超限: %s", path)
                return None
            file_size = file_path.stat().st_size
        except OSError as exc:
            logger.warning("截图文件读取失败: %s", exc)
            return None
        if file_size > self.max_size:
            logger.warning("截图不存在或超限: %s", path)
            return None
        url = f"{self.base_url}/api/agent/upload"
        data = {
            "execution_id": str(execution_id),
            "agent_id": self.agent_id,
            "session_token": session_token or "",
            "file_type": "screenshot",
        }
        files = {
            "file": {
                "name": file_path.name,
                "content_type": "image/png",
                "size": file_size,
            }
        }
        log_http_request(logger, "POST", url, body=data, files=files)
        async with httpx.AsyncClient(
            timeout=30, transport=self._transport, trust_env=False
        ) as client:
            try:
                fh = file_path.open("rb")
            except OSError as exc:
                logger.warning("截图文件读取失败: %s", exc)
                return None
            try:
                resp = await client.post(
                    url,
                    headers={"X-Agent-Key": self._resolve_key()},
                    data=data,
                    files={"file": (file_path.name, fh, "image/png")},
                )
            finally:
                fh.close()
        log_http_response(logger, "POST", url, resp.status_code, resp.text)
        if resp.status_code != 200:
            logger.warning("截图上传失败: %s %s", resp.status_code, resp.text)
            return None
        return resp.json().get("path")
