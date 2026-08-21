"""截图存储路径安全处理。

截图只通过 HTTP 上传（§10.7），数据库 `execution_steps.screenshot_path`
只保存服务端生成的对象键（相对路径），禁止 Agent 通过 WS 提交任意文件系统路径。

本模块负责：
- 生成安全的服务端文件名（UUID，忽略用户提供的文件名）
- 将对象键解析为绝对路径，并强制限制在 `execution_{id}/screenshots/` 目录内
- 拒绝绝对路径、盘符、UNC、反斜杠、`..` 与非预期目录前缀
"""

from pathlib import Path
from uuid import uuid4

from app.core.config import reports_dir

SCREENSHOTS_SUBDIR = "screenshots"

_ALLOWED_EXTS = {".png", ".jpg", ".jpeg"}


def _is_absolute_or_traversal(path_str: str) -> bool:
    """判断字符串是否为绝对路径或含穿越片段（Windows 与 POSIX 双平台）。"""
    if not path_str:
        return True
    norm = path_str.replace("\\", "/")
    if ".." in norm.split("/"):
        return True
    p = Path(norm)
    if p.is_absolute():
        return True
    # 盘符（如 C:）、UNC（// 开头）均为绝对/网络路径
    if len(norm) >= 2 and norm[1] == ":":
        return True
    if norm.startswith("//"):
        return True
    return False


def new_screenshot_filename(original_name: str | None) -> str:
    """根据上传原文件名生成安全的服务端文件名（UUID + 扩展名）。

    调用方应确保扩展名属于 `_ALLOWED_EXTS`；无法判断时回退为 .png。
    """
    ext = ""
    if original_name and "." in original_name:
        candidate = "." + original_name.rsplit(".", 1)[-1].lower()
        ext = candidate if candidate in _ALLOWED_EXTS else ""
    if not ext:
        ext = ".png"
    return f"{uuid4().hex}{ext}"


def build_object_key(execution_id: int, filename: str) -> str:
    """构造数据库存储的对象键：execution_{id}/screenshots/{filename}。"""
    return f"execution_{execution_id}/{SCREENSHOTS_SUBDIR}/{filename}"


def validate_object_key(execution_id: int, object_key: str | None) -> bool:
    """校验 WS 回传/数据库中的对象键是否属于该 execution 的截图目录。"""
    if not object_key:
        return False
    expected_prefix = f"execution_{execution_id}/{SCREENSHOTS_SUBDIR}/"
    if not object_key.startswith(expected_prefix):
        return False
    rel = object_key[len(expected_prefix):]
    if not rel or "/" in rel.replace("\\", "/") or "\\" in rel:
        return False
    if _is_absolute_or_traversal(rel):
        return False
    ext = "." + rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
    return ext in _ALLOWED_EXTS


def resolve_screenshot_path(execution_id: int, object_key: str) -> Path | None:
    """将对象键解析为绝对路径；若非法或越界返回 None。"""
    if not validate_object_key(execution_id, object_key):
        return None
    base = (reports_dir() / f"execution_{execution_id}").resolve()
    rel = object_key.split(f"execution_{execution_id}/", 1)[1]
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        return None
    return target
