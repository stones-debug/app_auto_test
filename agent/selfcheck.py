"""--self-check 安装自检（Windows 方案 §4.2）。

校验：配置可读、adb 可用、Appium 依赖可导入、状态目录可写、必需包完整。
输出结构化 JSON，供 CI 与用户排障。
"""

import logging
import shutil
import sys
from pathlib import Path

from main import load_config
from version import __version__

logger = logging.getLogger("agent.selfcheck")


def _check_adb() -> tuple[bool, str]:
    path = shutil.which("adb")
    if path:
        return True, f"adb: {path}"
    for candidate in (
        Path("vendor") / "platform-tools" / "adb.exe",
        Path("vendor") / "platform-tools" / "adb",
    ):
        if candidate.exists():
            return True, f"adb: {candidate.resolve()}"
    return False, "未找到 adb（platform-tools 未随 Agent 安装或不在 PATH）"


def _check_appium() -> tuple[bool, str]:
    if shutil.which("appium"):
        return True, "appium 在 PATH 中"
    try:
        from appium import webdriver  # noqa: F401

        return True, "appium-python-client 已安装（server 请另行配置）"
    except ImportError as exc:
        probe = ""
        try:
            from importlib.metadata import distribution

            dist = distribution("Appium-Python-Client")
            probe = f" metadata_path={dist._path}"
        except Exception as meta_exc:
            probe = f" metadata_lookup_failed: {meta_exc}"
        return False, f"appium-python-client 导入失败（pip install 'agent[appium]'）: {exc}{probe}"


def _check_state_dir(state: Path | None) -> tuple[bool, str]:
    target = state or Path.home()
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".self_check_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, str(target)
    except OSError as exc:
        return False, f"状态目录不可写: {exc}"


def run_self_check(config_path: str, state: Path | None = None) -> dict:
    checks: list[dict] = []

    def check(name: str, fn, ok_detail: str = "") -> None:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, str(exc)
        checks.append({"name": name, "ok": ok, "detail": detail or ok_detail})

    check("config", lambda: (True, f"config: {config_path}") if Path(config_path).exists() else (False, f"配置文件不存在: {config_path}"))
    if Path(config_path).exists():
        check("config_parse", lambda: (True, f"server={load_config(config_path).get('server')}"))
    check("adb", _check_adb)
    check("appium", _check_appium)
    check("state_dir", lambda: _check_state_dir(state))
    check("python", lambda: (True, f"{sys.version.split()[0]}"))

    return {
        "ok": all(c["ok"] for c in checks),
        "version": __version__,
        "checks": checks,
    }
