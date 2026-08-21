"""桌面控制器（Windows 方案 §4.1）。

- Tkinter 主窗口 + pystray 托盘；asyncio 网络循环运行在独立后台线程；
- UI 与后台经 AsyncBridge（run_coroutine_threadsafe）通信；
- 窗口内容：服务器地址、Agent ID/版本/在线/Appium 状态、绑定管理、
  本地设备表、无线连接对话框、手动刷新/断开/日志目录/退出。
"""

import asyncio
import logging
import threading
from pathlib import Path

logger = logging.getLogger("agent.desktop")


class AsyncBridge:
    """把 async 调用投递到后台事件循环线程并等待结果（Tk 回调中调用）。"""

    def __init__(self, loop: asyncio.AbstractEventLoop, timeout: float = 30.0) -> None:
        self.loop = loop
        self.timeout = timeout

    def call(self, coro_factory) -> object:
        future = asyncio.run_coroutine_threadsafe(coro_factory(), self.loop)
        return future.result(timeout=self.timeout)

    def call_async(self, coro_factory) -> asyncio.Future:
        """不等待结果（fire-and-forget），异常记日志。"""
        future = asyncio.run_coroutine_threadsafe(coro_factory(), self.loop)

        def _done(f):
            try:
                f.result()
            except Exception as exc:
                logger.warning("后台任务失败: %s", exc)

        future.add_done_callback(_done)
        return future


def load_server_url(state_dir: Path, default: str) -> str:
    file = state_dir / "server_url.txt"
    if file.exists():
        value = file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return default


def save_server_url(state_dir: Path, url: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "server_url.txt").write_text(url.strip(), encoding="utf-8")


class DesktopController:
    """托盘 + 窗口控制器。run() 必须在主线程调用（Tk 要求）。"""

    def __init__(
        self,
        app,
        bridge: AsyncBridge,
        state_dir: Path,
        log_dir: Path,
        server_url: str,
    ) -> None:
        self.app = app
        self.bridge = bridge
        self.state_dir = state_dir
        self.log_dir = log_dir
        self.server_url = server_url
        self._root = None
        self._tray = None
        self._device_rows: dict[str, int] = {}
        self._refresh_job: str | None = None

    # ---------- 启动 ----------

    def run(self) -> None:
        try:
            import tkinter as tk
        except ImportError as exc:
            raise RuntimeError("当前环境无 tkinter，无法启动桌面窗口") from exc
        from tkinter import ttk

        root = tk.Tk()
        self._root = root
        root.title("APP 自动化测试平台 Agent")
        root.geometry("760x560")
        root.resizable(True, True)

        self._build_ui(root, ttk)
        self._start_tray()
        self._refresh_all()
        self._schedule_refresh()
        root.protocol("WM_DELETE_WINDOW", self._on_close_window)
        root.mainloop()

    def _build_ui(self, root, ttk) -> None:
        from tkinter import StringVar

        pad = {"padx": 8, "pady": 4}

        # 顶部：服务器 + 状态
        top = ttk.Frame(root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="服务器:").pack(side="left")
        self.server_var = StringVar(value=self.server_url)
        entry = ttk.Entry(top, textvariable=self.server_var, width=40)
        entry.pack(side="left", padx=4)
        ttk.Button(top, text="保存", command=self._on_save_server).pack(side="left")
        self.status_var = StringVar(value="状态：初始化…")
        ttk.Label(top, textvariable=self.status_var, foreground="#666").pack(side="left", padx=12)

        # 绑定区
        bind_frame = ttk.LabelFrame(root, text="已绑定用户 / 绑定")
        bind_frame.pack(fill="x", **pad)
        self.key_var = StringVar()
        ttk.Entry(bind_frame, textvariable=self.key_var, width=52, show="•").pack(side="left", padx=4)
        ttk.Button(bind_frame, text="绑定 Key", command=self._on_bind).pack(side="left")
        self.bindings_var = StringVar(value="未绑定")
        ttk.Label(bind_frame, textvariable=self.bindings_var, foreground="#555").pack(side="left", padx=12)

        # 设备表
        device_frame = ttk.LabelFrame(root, text="本地设备（3 秒自动刷新）")
        device_frame.pack(fill="both", expand=True, **pad)
        columns = ("name", "udid", "address", "conn", "status")
        tree = ttk.Treeview(device_frame, columns=columns, show="headings", height=8)
        for col, title, width in (
            ("name", "名称", 160),
            ("udid", "序列号/地址", 180),
            ("address", "地址", 140),
            ("conn", "连接", 70),
            ("status", "状态", 110),
        ):
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree = tree

        # 操作按钮
        actions = ttk.Frame(root)
        actions.pack(fill="x", **pad)
        ttk.Button(actions, text="手动刷新", command=self._refresh_all).pack(side="left")
        ttk.Button(actions, text="连接无线设备", command=self._open_wifi_dialog).pack(side="left", padx=4)
        ttk.Button(actions, text="打开日志目录", command=self._open_log_dir).pack(side="left", padx=4)
        ttk.Button(actions, text="退出", command=self._on_quit).pack(side="right")

    # ---------- 托盘 ----------

    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            logger.warning("pystray/Pillow 不可用，仅窗口模式运行")
            return
        image = Image.new("RGB", (64, 64), "#409EFF")
        draw = ImageDraw.Draw(image)
        draw.rectangle((16, 16, 48, 48), fill="white")
        menu = pystray.Menu(
            pystray.MenuItem("打开窗口", self._show_window, default=True),
            pystray.MenuItem("退出", self._on_quit),
        )
        self._tray = pystray.Icon("app-auto-test-agent", image, "APP 自动化测试平台 Agent", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _show_window(self, _icon=None, _item=None) -> None:
        if self._root is not None:
            self._root.after(0, self._root.deiconify)
            self._root.after(0, self._root.lift)

    # ---------- 刷新 ----------

    def _schedule_refresh(self) -> None:
        if self._root is not None:
            self._refresh_job = self._root.after(3000, self._on_tick)

    def _on_tick(self) -> None:
        self._refresh_devices()
        self._schedule_refresh()

    def _refresh_all(self) -> None:
        self._refresh_status()
        self._refresh_bindings()
        self._refresh_devices()

    def _refresh_status(self) -> None:
        app = self.app
        agent_id = getattr(app.client, "agent_id", "-") if app.client else "-"
        online = "在线" if app.client is not None else "离线"
        appium = "运行中" if app.appium.running else "停止"
        self.status_var.set(f"Agent: {agent_id} | 连接: {online} | Appium: {appium}")

    def _refresh_bindings(self) -> None:
        async def _load():
            if self.app.bindings is None:
                return []
            try:
                return await self.app.bindings.list_users()
            except Exception as exc:
                logger.warning("绑定列表刷新失败: %s", exc)
                return []

        try:
            users = self.bridge.call(_load)
        except Exception as exc:
            # 桥接异常（如后台循环未就绪）不得打断 UI 启动
            logger.warning("绑定列表刷新异常: %s", exc)
            users = []
        if not users:
            self.bindings_var.set("未绑定（输入上方 Key 后点击“绑定 Key”）")
        else:
            self.bindings_var.set("、".join(u.get("username", "?") for u in users))

    def _refresh_devices(self) -> None:
        devices = self.app.registry.current()
        tree = self.tree
        if tree is None:
            return
        seen: set[str] = set()
        for device in devices:
            seen.add(device["udid"])
            iid = f"d{device['udid']}"
            values = (
                device.get("name", ""),
                device.get("udid", ""),
                device.get("address") or "",
                device.get("connection_type", "usb"),
                device.get("status", ""),
            )
            if tree.exists(iid):
                tree.item(iid, values=values)
            else:
                tree.insert("", "end", iid=iid, values=values)
        for iid in list(tree.get_children("")):
            if iid not in {f"d{u}" for u in seen}:
                tree.delete(iid)

    # ---------- 操作 ----------

    def _on_save_server(self) -> None:
        url = self.server_var.get().strip()
        if not url.startswith(("ws://", "wss://")):
            from tkinter import messagebox

            messagebox.showwarning("提示", "服务器地址必须以 ws:// 或 wss:// 开头")
            return
        save_server_url(self.state_dir, url)
        from tkinter import messagebox

        messagebox.showinfo("提示", "服务器地址已保存，重启 Agent 后生效")

    def _on_bind(self) -> None:
        from tkinter import messagebox

        key = self.key_var.get().strip()
        if not key:
            messagebox.showwarning("提示", "请输入用户 Key（uak_…）")
            return
        try:
            result = self.bridge.call(lambda: self.app.bindings.bind(key))
        except Exception as exc:
            messagebox.showerror("绑定失败", str(exc))
            return
        self.key_var.set("")
        messagebox.showinfo("绑定成功", f"已绑定用户（Agent: {result.get('agent_id')}）")
        self._refresh_bindings()

    def _open_wifi_dialog(self) -> None:
        from tkinter import StringVar, Toplevel, ttk

        from devices.adb import connect, pair, validate_host, validate_port

        dialog = Toplevel(self._root)
        dialog.title("连接无线设备")
        dialog.geometry("420x260")
        pad = {"padx": 8, "pady": 4}
        f = ttk.Frame(dialog)
        f.pack(fill="both", expand=True, **pad)

        ttk.Label(f, text="设备地址:").grid(row=0, column=0, sticky="e")
        host_var = StringVar()
        ttk.Entry(f, textvariable=host_var, width=24).grid(row=0, column=1, sticky="w")
        ttk.Label(f, text="连接端口:").grid(row=0, column=2, sticky="e")
        port_var = StringVar(value="5555")
        ttk.Entry(f, textvariable=port_var, width=8).grid(row=0, column=3, sticky="w")

        ttk.Label(f, text="配对地址(可选):").grid(row=1, column=0, sticky="e")
        pair_host_var = StringVar()
        ttk.Entry(f, textvariable=pair_host_var, width=24).grid(row=1, column=1, sticky="w")
        ttk.Label(f, text="配对端口:").grid(row=1, column=2, sticky="e")
        pair_port_var = StringVar(value="37000")
        ttk.Entry(f, textvariable=pair_port_var, width=8).grid(row=1, column=3, sticky="w")

        ttk.Label(f, text="配对码(可选):").grid(row=2, column=0, sticky="e")
        code_var = StringVar()
        ttk.Entry(f, textvariable=code_var, width=24).grid(row=2, column=1, sticky="w")

        result_var = StringVar(value="")
        ttk.Label(f, textvariable=result_var, foreground="#666", wraplength=380).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=6
        )

        def _do_connect():
            host = host_var.get().strip()
            port = port_var.get().strip()
            if not validate_host(host) or not validate_port(port):
                result_var.set("设备地址或端口无效")
                return
            try:
                if pair_host_var.get().strip() and code_var.get().strip():
                    result_var.set(
                        pair(
                            pair_host_var.get().strip(),
                            int(pair_port_var.get().strip() or 37000),
                            code_var.get().strip(),
                        )
                    )
                result_var.set(connect(host, int(port)))
                self._refresh_devices()
            except Exception as exc:
                result_var.set(str(exc))

        ttk.Button(f, text="连接", command=_do_connect).grid(row=4, column=0, columnspan=2, pady=8)
        ttk.Button(f, text="关闭", command=dialog.destroy).grid(row=4, column=2, columnspan=2)

    def _open_log_dir(self) -> None:
        import os
        import subprocess

        if os.name == "nt":
            subprocess.Popen(["explorer", str(self.log_dir)], shell=False)
        else:
            logger.info("日志目录: %s", self.log_dir)

    def _on_close_window(self) -> None:
        # 关闭窗口只隐藏到托盘
        if self._root is not None:
            self._root.withdraw()
            if self._tray is None:
                self._on_quit()

    def _on_quit(self, _icon=None, _item=None) -> None:
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        if self._root is not None:
            self._root.after(0, self._root.destroy)
