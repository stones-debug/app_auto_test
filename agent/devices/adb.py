"""ADB 命令封装（Windows 方案 §4.1）。

- 一律参数数组调用、禁用 shell（防注入）；
- 校验主机/IP 与端口；15 秒超时；
- ADB 错误翻译为可读提示。
"""

import ipaddress
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("agent.adb")

ADB_TIMEOUT_SECONDS = 15


def _find_adb() -> str:
    """定位 adb 可执行文件：PATH → 打包随附（exe 旁 platform-tools）→ 开发目录（agent/vendor）→ CWD vendor。"""
    path = shutil.which("adb")
    if path:
        return path
    candidates: list[Path] = []
    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / "platform-tools" / "adb.exe")
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "platform-tools" / "adb.exe")
    candidates.append(Path(__file__).resolve().parents[1] / "vendor" / "platform-tools" / "adb.exe")
    candidates.append(Path("vendor") / "platform-tools" / "adb.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "adb"

# 设备状态 → 上报状态（Windows 方案 §4.1：只有 device 上报 idle）
STATE_MAP = {
    "device": "idle",
    "unauthorized": "unauthorized",
    "offline": "offline",
    "no permissions": "unauthorized",
    "recovery": "offline",
    "sideload": "offline",
}

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")
_COMPONENT_RE = re.compile(
    r"^(?P<package>[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/(?P<activity>[A-Za-z0-9_.$]+)$"
)

_ADB_EXE = _find_adb()


class AdbError(Exception):
    """ADB 操作失败，message 为可读提示（Windows 方案 §4.1 错误翻译）。"""


def validate_host(host: str) -> bool:
    """校验主机名/IP，拒绝 shell 注入字符与路径。"""
    if not host or len(host) > 255:
        return False
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", host):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return bool(_HOSTNAME_RE.fullmatch(host))


def validate_port(port: int | str) -> bool:
    try:
        return 1 <= int(port) <= 65535
    except (TypeError, ValueError):
        return False


def _run(args: list[str], timeout: int = ADB_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """执行 adb 命令（参数数组 + shell=False + 超时，GUI 宿主下不弹黑色控制台窗口）。"""
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        return subprocess.run(
            [_ADB_EXE, *args],
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise AdbError("未找到 adb，请确认 platform-tools 已随 Agent 安装") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb 命令超时（>{timeout}s）：adb {' '.join(args)}") from exc
    except OSError as exc:
        raise AdbError(f"adb 执行失败：{exc}") from exc


def list_devices() -> list[dict]:
    """`adb devices -l` 输出解析。

    返回 [{udid, name, status, connection_type, address, model}]；status 为上报状态
    （idle/unauthorized/offline）。adb 不可用时返回 [] 并记警告。
    """
    try:
        proc = _run(["devices", "-l"])
    except AdbError as exc:
        logger.warning("adb devices 失败: %s", exc)
        return []
    if proc.returncode != 0:
        logger.warning("adb devices 非零退出: %s", (proc.stderr or "").strip())
        return []

    devices: list[dict] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] == "List":
            continue
        udid, raw_state = parts[0], parts[1]
        status = STATE_MAP.get(raw_state, "offline")
        attrs: dict[str, str] = {}
        for token in parts[2:]:
            if ":" in token:
                k, _, v = token.partition(":")
                attrs[k] = v
        is_wifi = ":" in udid and not udid.startswith("emulator")
        devices.append(
            {
                "udid": udid,
                "name": attrs.get("model") or udid,
                "status": status,
                "connection_type": "wifi" if is_wifi else "usb",
                "address": udid if is_wifi else None,
                "model": attrs.get("model"),
                "platform_version": attrs.get("sdk") or None,
            }
        )
    return devices


def _require_valid_endpoint(host: str, port: int | str) -> str:
    if not validate_host(host):
        raise AdbError(f"无效的主机/IP：{host!r}")
    if not validate_port(port):
        raise AdbError(f"无效的端口：{port!r}")
    return f"{host}:{port}"


def pair(host: str, port: int, code: str, timeout: int = ADB_TIMEOUT_SECONDS) -> str:
    """adb pair <host>:<port> <code>（无线配对）。返回成功提示。"""
    endpoint = _require_valid_endpoint(host, port)
    if not code:
        raise AdbError("缺少配对码")
    proc = _run(["pair", endpoint, code], timeout=timeout)
    message = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if proc.returncode != 0 or "success" not in message.lower():
        raise AdbError(f"配对失败：{message or '未知错误'}")
    return message


def connect(host: str, port: int, timeout: int = ADB_TIMEOUT_SECONDS) -> str:
    """adb connect <host>:<port>。返回成功提示。"""
    endpoint = _require_valid_endpoint(host, port)
    proc = _run(["connect", endpoint], timeout=timeout)
    message = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    lowered = message.lower()
    if proc.returncode != 0 or "connected" not in lowered:
        raise AdbError(f"连接失败：{message or '未知错误'}")
    return message


def disconnect(host: str, port: int, timeout: int = ADB_TIMEOUT_SECONDS) -> str:
    """adb disconnect <host>:<port>。无线断开；未知地址返回可读提示。"""
    endpoint = _require_valid_endpoint(host, port)
    proc = _run(["disconnect", endpoint], timeout=timeout)
    message = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if proc.returncode != 0:
        raise AdbError(f"断开失败：{message or '未知错误'}")
    return message or "已断开"


def _parse_resolved_activity(output: str, package: str) -> str | None:
    """从不同 Android 版本的 resolve-activity 输出中提取 Activity。"""
    for line in output.splitlines():
        match = _COMPONENT_RE.fullmatch(line.strip())
        if match is None or match.group("package") != package:
            continue
        activity = match.group("activity")
        return f"{package}{activity}" if activity.startswith(".") else activity
    return None


def resolve_launcher_activity(udid: str, package: str) -> str:
    """解析已安装 Android 应用的 MAIN/LAUNCHER Activity。

    优先使用 ``cmd package``，并兼容仅支持 ``pm resolve-activity`` 的旧系统。
    返回完整 Activity 类名；无法解析时区分包未安装和没有标准启动入口。
    """
    udid = udid.strip()
    package = package.strip()
    if not udid:
        raise AdbError("无法解析启动 Activity：缺少 Android 设备 UDID")
    if not _PACKAGE_RE.fullmatch(package):
        raise AdbError(f"无法解析启动 Activity：无效的 Android 包名 {package!r}")

    intent_args = [
        "--brief",
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
        package,
    ]
    errors: list[str] = []
    for resolver in (["cmd", "package", "resolve-activity"], ["pm", "resolve-activity"]):
        proc = _run(["-s", udid, "shell", *resolver, *intent_args])
        output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
        if proc.returncode == 0:
            activity = _parse_resolved_activity(output, package)
            if activity:
                return activity
        if output:
            errors.append(output)

    installed = _run(["-s", udid, "shell", "pm", "path", package])
    if installed.returncode != 0 or not (installed.stdout or "").strip().startswith("package:"):
        raise AdbError(f"设备 {udid} 未安装应用 {package}")

    detail = f"；ADB 输出：{' | '.join(errors)}" if errors else ""
    raise AdbError(
        f"应用 {package} 已安装，但 Manifest 中没有可解析的 MAIN/LAUNCHER Activity{detail}。"
        "请在启动 APP 步骤中显式填写 Activity"
    )


def heal_offline_emulator(udid: str) -> str:
    """雷电/模拟器双 adb 通道互踢时自愈：emulator-NNNN offline → adb connect 127.0.0.1:NNNN+1。

    返回自愈后可用的在线 udid（优先原 udid，其次 TCP 通道）；无法自愈时原样返回。
    """
    try:
        current = next((d for d in list_devices() if d["udid"] == udid), None)
        if current is not None and current["status"] == "idle":
            return udid
    except Exception:
        pass
    match = re.fullmatch(r"emulator-(\d+)", udid)
    if not match:
        return udid
    tcp = f"127.0.0.1:{int(match.group(1)) + 1}"
    try:
        connect(tcp, int(match.group(1)) + 1)
    except Exception:
        pass
    time.sleep(2)
    try:
        devices = list_devices()
    except Exception:
        return udid
    for d in devices:
        if d["udid"] == tcp and d["status"] == "idle":
            return tcp
    for d in devices:
        if d["udid"] == udid and d["status"] == "idle":
            return udid
    return udid
