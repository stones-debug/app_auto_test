"""Agent 本地状态（Windows 方案 §3.2/§4.1）。

- install_id：安装实例 ID，首次启动生成并持久化（绑定/追加绑定用）；
- 存储位置：LocalAppData\\AppAutoTestAgent\\state（打包环境），可注入覆盖（测试/开发）。
"""

import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger("agent.state")


def state_dir(override: Path | None = None) -> Path:
    if override is not None:
        override.mkdir(parents=True, exist_ok=True)
        return override
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".app-auto-test"))
    path = base / "AppAutoTestAgent"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_or_create_install_id(state: Path | None = None) -> str:
    """读取或创建 install_id（agent-<hex12>），持久化到状态目录。"""
    dir_path = state_dir(state)
    file = dir_path / "install_id"
    if file.exists():
        install_id = file.read_text(encoding="utf-8").strip()
        if install_id:
            return install_id
    install_id = f"agent-{uuid.uuid4().hex[:12]}"
    file.write_text(install_id, encoding="utf-8")
    logger.info("已生成 install_id: %s", install_id)
    return install_id
