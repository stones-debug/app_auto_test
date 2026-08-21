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


def bundled_appium() -> tuple[Path, Path, Path | None] | None:
    """打包安装版随附的便携 Node + Appium + 驱动目录（exe 旁 appium/，publish.ps1 生成）。

    返回 (node.exe, appium main.js, APPIUM_HOME 驱动目录)；未随包时返回 None。
    """
    root = Path(sys.executable).resolve().parent
    node = root / "appium" / "node" / "node.exe"
    main_js = root / "appium" / "appium" / "node_modules" / "appium" / "build" / "lib" / "main.js"
    if node.is_file() and main_js.is_file():
        home = root / "appium" / "appium-home"
        return node, main_js, home if home.is_dir() else None
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
    ) -> None:
        self.host = host
        self.port = port
        self.appium_bin = appium_bin
        self.node_bin = node_bin
        self.appium_js = appium_js
        self.log_dir = log_dir
        self.ready_timeout = ready_timeout
        self.command = command
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
        bundled = bundled_appium()
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
        bundled = bundled_appium()
        if bundled:
            _, _, home = bundled
            env = os.environ.copy()
            if home is not None:
                env["APPIUM_HOME"] = str(home)
            # uiautomator2 驱动需要 ANDROID_HOME 定位 platform-tools/adb（随包在 exe 同级）
            root = Path(sys.executable).resolve().parent
            env["ANDROID_HOME"] = str(root)
            env["ANDROID_SDK_ROOT"] = str(root)
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
