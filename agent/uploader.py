import logging
from pathlib import Path

import httpx

logger = logging.getLogger("agent.uploader")


class Uploader:
    """截图等文件经 HTTP 上传到服务器（§10.7），返回相对路径。"""

    def __init__(self, base_url: str, agent_key: str, agent_id: str, max_size: int = 10 * 1024 * 1024) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_key = agent_key
        self.agent_id = agent_id
        self.max_size = max_size

    async def upload_screenshot(self, execution_id: int, path: str, session_token: str | None = None) -> str | None:
        file_path = Path(path)
        if not file_path.exists() or file_path.stat().st_size > self.max_size:
            logger.warning("截图不存在或超限: %s", path)
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            with file_path.open("rb") as fh:
                resp = await client.post(
                    f"{self.base_url}/api/agent/upload",
                    headers={"X-Agent-Key": self.agent_key},
                    data={
                        "execution_id": str(execution_id),
                        "agent_id": self.agent_id,
                        "session_token": session_token or "",
                        "file_type": "screenshot",
                    },
                    files={"file": (file_path.name, fh, "image/png")},
                )
        if resp.status_code != 200:
            logger.warning("截图上传失败: %s %s", resp.status_code, resp.text)
            return None
        return resp.json().get("path")
