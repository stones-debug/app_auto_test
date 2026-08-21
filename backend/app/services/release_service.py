"""Windows 方案 §3.4：Agent 安装包发布目录服务。

- `latest.json`：{version, filename, sha256, size, published_at}，由发布脚本原子更新；
- 下载令牌：5 分钟有效、限定文件名的 JWT；
- 文件名安全校验：解析后必须仍在发布目录内（防路径穿越）。
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt

from app.core.config import releases_dir, settings
from app.core.security import decode_token

DOWNLOAD_TOKEN_TTL_MINUTES = 5


class ReleaseNotFound(Exception):
    pass


class InvalidDownloadToken(Exception):
    pass


def read_manifest() -> dict:
    """读取 latest.json；缺失/非法 → ReleaseNotFound。"""
    path = releases_dir() / "latest.json"
    if not path.exists():
        raise ReleaseNotFound("暂无 Agent 安装包发布")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleaseNotFound("发布清单无效") from exc
    if not isinstance(data, dict) or not data.get("filename"):
        raise ReleaseNotFound("发布清单无效")
    return data


def resolve_release_file(filename: str) -> Path:
    """校验文件名并返回发布目录内的真实文件（防路径穿越）。"""
    root = releases_dir().resolve()
    target = (root / filename).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ReleaseNotFound("非法文件名") from exc
    if not target.is_file():
        raise ReleaseNotFound("安装包不存在")
    return target


def issue_download_token(filename: str) -> str:
    payload = {
        "sub": filename,
        "type": "download",
        "exp": datetime.now(UTC) + timedelta(minutes=DOWNLOAD_TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_download_token(token: str, filename: str) -> bool:
    """令牌必须为 download 类型且 sub 与请求文件名一致（限定文件名）。"""
    payload = decode_token(token)
    return bool(
        payload is not None
        and payload.get("type") == "download"
        and payload.get("sub") == filename
    )
