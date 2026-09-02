"""Appium Server 生命周期（Windows 方案 §4.1）。

- 需要执行时由 Agent 隐藏启动（127.0.0.1），stdout/stderr 落日志文件；
- wait_ready 轮询 /status，就绪后创建会话；
- 执行结束/Agent 退出时清理 Session 与子进程树（Windows taskkill /T /F）。
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("agent.appium")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _scan_appium_dir(base: Path) -> tuple[Path, Path, Path | None] | None:
    """扫描一个「便携 Appium 目录」（node + appium main.js + appium-home）是否齐全。

    base 下期望布局（与 publish.ps1 生成/拷贝结构一致）：
      base/node/node.exe
      base/appium/node_modules/appium/build/lib/main.js
      base/appium-home/（驱动目录，可选）
    """
    node = base / "node" / "node.exe"
    main_js = base / "appium" / "node_modules" / "appium" / "build" / "lib" / "main.js"
    if not (node.is_file() and main_js.is_file()):
        return None
    home = base / "appium-home"
    return node, main_js, home if home.is_dir() else None


def bundled_appium() -> tuple[Path, Path, Path | None] | None:
    """打包安装版随附的便携 Node + Appium + 驱动目录（exe 旁 appium/，publish.ps1 生成）。

    返回 (node.exe, appium main.js, APPIUM_HOME 驱动目录)；未随包时返回 None。
    """
    root = Path(sys.executable).resolve().parent
    return _scan_appium_dir(root / "appium")


def dev_bundled_appium() -> tuple[Path, Path, Path | None] | None:
    """源码/调试模式：仓库 agent/vendor/appium/（与 publish.ps1 打包目录同构）。

    与 devices/adb.py 的「开发目录 vendor 兜底」保持一致，使
    `uv run python main.py --config config.yaml` 无需额外配置即可自启 Appium。
    """
    repo = Path(__file__).resolve().parents[1] / "vendor" / "appium"
    return _scan_appium_dir(repo)


def resolve_android_sdk_root(explicit: str | Path | None = None) -> Path | None:
    """查找 Appium/ADB 使用的 Android SDK 根目录。

    Windows 下 Agent 经常由 PowerShell、桌面快捷方式或服务启动，父进程未必
    设置 Android SDK 环境变量。按显式配置、环境变量、PATH 中的 adb、Android
    Studio 默认目录依次查找，并且只接受包含 ``platform-tools`` 的 SDK 目录。
    """
    candidates: list[str | Path] = []
    if explicit is not None and str(explicit).strip():
        candidates.append(explicit)
    for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(name)
        if value:
            candidates.append(value)

    adb = shutil.which("adb")
    if adb:
        adb_path = Path(adb).resolve()
        if adb_path.parent.name.lower() == "platform-tools":
            candidates.append(adb_path.parent.parent)

    local_app_data = os.environ.get("LOCALAPPDATA")
    user_profile = os.environ.get("USERPROFILE")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Android" / "Sdk")
    if user_profile:
        candidates.append(Path(user_profile) / "AppData" / "Local" / "Android" / "Sdk")

    seen: set[str] = set()
    for raw in candidates:
        expanded = os.path.expandvars(str(raw).strip().strip('"'))
        path = Path(expanded).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_dir() and (resolved / "platform-tools").is_dir():
            return resolved
    return None


class AppiumError(Exception):
    pass


class AppiumServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4723,
        appium_bin: str | None = None,
        node_bin: str | None = None,
        appium_js: str | None = None,
        log_dir: Path | None = None,
        ready_timeout: float = 60.0,
        command: list[str] | None = None,
        probe: Callable[[str], bool] | None = None,
        android_sdk_root: str | Path | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.appium_bin = appium_bin
        self.node_bin = node_bin
        self.appium_js = appium_js
        self.log_dir = log_dir
        self.ready_timeout = ready_timeout
        self.command = command
        self.android_sdk_root = android_sdk_root
        self._probe_fn = probe or self._probe
        self.process: subprocess.Popen | None = None
        self._log_handle = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _command(self) -> list[str]:
        if self.command:
            return list(self.command)
        if self.appium_bin:
            resolved = shutil.which(self.appium_bin)
            if resolved:
                return [resolved, "--port", str(self.port), "--address", self.host]
            return [self.appium_bin, "--port", str(self.port), "--address", self.host]
        if self.node_bin and self.appium_js:
            return [self.node_bin, str(self.appium_js), "--port", str(self.port), "--address", self.host]
        bundled = bundled_appium() or dev_bundled_appium()
        if bundled:
            node_bin, main_js, _ = bundled
            return [str(node_bin), str(main_js), "--port", str(self.port), "--address", self.host]
        resolved = shutil.which("appium")
        if not resolved:
            raise AppiumError("未找到 appium 可执行文件（请配置 appium_bin 或 node_bin/appium_js）")
        return [resolved, "--port", str(self.port), "--address", self.host]

    def start(self) -> None:
        """隐藏启动 Appium（已启动则复用）。子进程 stdio 走日志文件，避免管道阻塞。"""
        if self.running:
            return
        command = self._command()
        log_file = None
        if self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_file = (self.log_dir / "appium.log").open("ab")
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        packed = bundled_appium()
        dev = packed or dev_bundled_appium()
        env = os.environ.copy()
        env_changed = False
        if dev:
            _, _, home = dev
            if home is not None:
                env["APPIUM_HOME"] = str(home)
                env_changed = True

        # Appium 的 uiautomator2 驱动必须通过环境变量找到 Android SDK。
        # 打包版 SDK 与 exe 同级；源码运行则自动发现本机 SDK，也支持配置显式指定。
        sdk_root = self.android_sdk_root
        if packed and sdk_root is None:
            sdk_root = Path(sys.executable).resolve().parent
        if self.android_sdk_root is not None:
            resolved_sdk = resolve_android_sdk_root(self.android_sdk_root)
        else:
            resolved_sdk = resolve_android_sdk_root(sdk_root)
        if self.android_sdk_root is not None and resolved_sdk is None:
            raise AppiumError(
                f"Android SDK 路径无效或缺少 platform-tools: {self.android_sdk_root}"
            )
        if resolved_sdk is not None:
            env["ANDROID_HOME"] = str(resolved_sdk)
            env["ANDROID_SDK_ROOT"] = str(resolved_sdk)
            path_parts = [
                str(resolved_sdk / "platform-tools"),
                str(resolved_sdk / "emulator"),
            ]
            existing_path = env.get("PATH", "")
            env["PATH"] = ";".join(path_parts + ([existing_path] if existing_path else []))
            env_changed = True
        if env_changed:
            kwargs["env"] = env
        self.process = subprocess.Popen(
            command,
            shell=False,
            stdout=log_file or subprocess.DEVNULL,
            stderr=log_file or subprocess.DEVNULL,
            **kwargs,
        )
        self._log_handle = log_file
        logger.info("Appium 已启动: %s (pid=%s)", " ".join(command), self.process.pid)

    async def wait_ready(self) -> None:
        """轮询 http://host:port/status 直到就绪或超时。"""
        if not self.running:
            raise AppiumError("Appium 进程未运行")
        url = f"http://{self.host}:{self.port}/status"
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if not self.running:
                raise AppiumError("Appium 进程提前退出")
            try:
                ok = await asyncio.to_thread(self._probe_fn, url)
                if ok:
                    logger.info("Appium 就绪: %s", url)
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)
        raise AppiumError(f"Appium 启动超时（>{self.ready_timeout}s）")

    @staticmethod
    def _probe(url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def stop(self) -> None:
        """终止 Appium 及其子进程树。"""
        proc = self.process
        self.process = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
        if proc is None or proc.poll() is not None:
            return
        logger.info("正在停止 Appium (pid=%s)", proc.pid)
        try:
            if os.name == "nt":
                # 终止整个进程树（Appium 会派生 driver 子进程）
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                    timeout=10,
                )
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception as exc:
            logger.warning("Appium 终止异常: %s", exc)
