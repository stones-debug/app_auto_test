"""桌面控制器（Windows 方案 §4.1）。

- Tkinter 主窗口 + pystray 托盘；asyncio 网络循环运行在独立后台线程；
- UI 与后台经 AsyncBridge（run_coroutine_threadsafe）通信；
- 窗口内容：服务器地址、Agent ID/版本/在线/Appium 状态、绑定管理、
  本地设备表、无线连接对话框、手动刷新/断开/日志目录/退出。
"""

import asyncio
import logging
import sys
import threading
from pathlib import Path

from version import __version__

logger = logging.getLogger("agent.desktop")

USER_KEY_FILENAME = "user_key.txt"


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


def default_runtime_dir() -> Path:
    """返回运行目录：打包版取 EXE 目录，源码版取当前工作目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def load_user_key(runtime_dir: Path) -> str:
    """从运行目录读取上次成功绑定的用户 Key。"""
    file = runtime_dir / USER_KEY_FILENAME
    if not file.exists():
        return ""
    try:
        return file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        logger.warning("用户 Key 读取失败: %s", exc)
        return ""


def save_user_key(runtime_dir: Path, key: str) -> None:
    """将用户 Key 明文保存到运行目录，供桌面输入框下次回填。"""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / USER_KEY_FILENAME).write_text(key.strip(), encoding="utf-8")


class DesktopController:
    """托盘 + 窗口控制器。run() 必须在主线程调用（Tk 要求）。"""

    def __init__(
        self,
        app,
        bridge: AsyncBridge,
        state_dir: Path,
        log_dir: Path,
        server_url: str,
        runtime_dir: Path | None = None,
    ) -> None:
        self.app = app
        self.bridge = bridge
        self.state_dir = state_dir
        self.log_dir = log_dir
        self.server_url = server_url
        self.runtime_dir = runtime_dir or default_runtime_dir()
        self._root = None
        self._tray = None
        self._device_rows: dict[str, int] = {}
        self._refresh_job: str | None = None
        self._key_visible = False

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
        root.geometry("900x700")
        root.minsize(820, 620)
        root.resizable(True, True)
        root.configure(background="#f4f6fb")

        try:
            from PIL import ImageTk

            from desktop.logo import icon

            self._window_icon = ImageTk.PhotoImage(icon(64))
            root.iconphoto(True, self._window_icon)
        except Exception as exc:
            logger.debug("窗口图标加载失败: %s", exc)

        self._build_ui(root, ttk)
        self._start_tray()
        self._refresh_all()
        self._schedule_refresh()
        root.protocol("WM_DELETE_WINDOW", self._on_close_window)
        root.mainloop()

    def _build_ui(self, root, ttk) -> None:
        from tkinter import StringVar

        self._configure_styles(root, ttk)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        page = ttk.Frame(root, style="Page.TFrame", padding=(24, 20, 24, 18))
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(4, weight=1)

        # 品牌标题
        header = ttk.Frame(page, style="Page.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header.columnconfigure(0, weight=1)
        title_box = ttk.Frame(header, style="Page.TFrame")
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text="APP 自动化测试平台", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            title_box,
            text="Device Agent · 管理连接、用户绑定与本地测试设备",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, text=f"v{__version__}", style="Version.TLabel").grid(
            row=0, column=1, sticky="ne", padx=(12, 0)
        )

        # 运行状态概览
        status_card = ttk.Frame(page, style="Card.TFrame", padding=(18, 14))
        status_card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        for index in range(3):
            status_card.columnconfigure(index, weight=1)
        self.agent_id_var = StringVar(value="-")
        self.connection_var = StringVar(value="正在连接")
        self.appium_var = StringVar(value="已停止")
        self.status_var = StringVar(value="状态：初始化…")
        self._status_item(status_card, ttk, 0, "AGENT ID", self.agent_id_var)
        self._status_item(status_card, ttk, 1, "服务器连接", self.connection_var)
        self._status_item(status_card, ttk, 2, "APPIUM 服务", self.appium_var)

        settings = ttk.Frame(page, style="Page.TFrame")
        settings.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        settings.columnconfigure(0, weight=1)
        settings.columnconfigure(1, weight=1)

        # 服务器设置
        server_card = ttk.Frame(settings, style="Card.TFrame", padding=(18, 16))
        server_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        server_card.columnconfigure(0, weight=1)
        ttk.Label(server_card, text="服务器连接", style="SectionTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            server_card, text="修改后重启 Agent 生效", style="Hint.TLabel"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 10))
        self.server_var = StringVar(value=self.server_url)
        ttk.Entry(server_card, textvariable=self.server_var, style="Field.TEntry").grid(
            row=2, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(
            server_card, text="保存地址", command=self._on_save_server, style="Secondary.TButton"
        ).grid(row=2, column=1)

        # 用户 Key 与绑定
        bind_card = ttk.Frame(settings, style="Card.TFrame", padding=(18, 16))
        bind_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        bind_card.columnconfigure(0, weight=1)
        ttk.Label(bind_card, text="用户绑定", style="SectionTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        self.bindings_var = StringVar(value="正在读取绑定信息…")
        ttk.Label(bind_card, textvariable=self.bindings_var, style="Hint.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(3, 10)
        )
        self.key_var = StringVar(value=load_user_key(self.runtime_dir))
        self.key_entry = ttk.Entry(
            bind_card, textvariable=self.key_var, show="•", style="Field.TEntry"
        )
        self.key_entry.grid(row=2, column=0, sticky="ew", padx=(0, 6))
        self.key_visibility_button = ttk.Button(
            bind_card,
            text="显示",
            command=self._toggle_key_visibility,
            style="Ghost.TButton",
            width=5,
        )
        self.key_visibility_button.grid(row=2, column=1, padx=(0, 6))
        ttk.Button(
            bind_card, text="绑定", command=self._on_bind, style="Accent.TButton", width=7
        ).grid(row=2, column=2)
        ttk.Label(
            bind_card,
            text=f"Key 明文保存在运行目录的 {USER_KEY_FILENAME}",
            style="Micro.TLabel",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # 设备表
        device_card = ttk.Frame(page, style="Card.TFrame", padding=(18, 16, 18, 14))
        device_card.grid(row=4, column=0, sticky="nsew")
        device_card.columnconfigure(0, weight=1)
        device_card.rowconfigure(2, weight=1)
        ttk.Label(device_card, text="本地设备", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.device_count_var = StringVar(value="正在扫描设备…")
        ttk.Label(device_card, textvariable=self.device_count_var, style="Hint.TLabel").grid(
            row=1, column=0, sticky="w", pady=(3, 10)
        )
        table = ttk.Frame(device_card, style="Card.TFrame")
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = ("name", "udid", "address", "conn", "status")
        tree = ttk.Treeview(table, columns=columns, show="headings", height=8)
        for col, title, width in (
            ("name", "设备名称", 180),
            ("udid", "序列号 / 地址", 220),
            ("address", "网络地址", 150),
            ("conn", "连接方式", 90),
            ("status", "当前状态", 110),
        ):
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree = tree

        # 操作按钮
        actions = ttk.Frame(page, style="Page.TFrame")
        actions.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(
            actions, text="刷新设备", command=self._refresh_all, style="Secondary.TButton"
        ).pack(side="left")
        ttk.Button(
            actions, text="连接无线设备", command=self._open_wifi_dialog, style="Secondary.TButton"
        ).pack(side="left", padx=8)
        ttk.Button(
            actions, text="打开日志目录", command=self._open_log_dir, style="Secondary.TButton"
        ).pack(side="left")
        ttk.Label(actions, text="设备每 3 秒自动刷新", style="Micro.TLabel").pack(
            side="left", padx=12
        )
        ttk.Button(actions, text="退出 Agent", command=self._on_quit, style="Danger.TButton").pack(
            side="right"
        )

    @staticmethod
    def _configure_styles(root, ttk) -> None:
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font)
        style.configure("Page.TFrame", background="#f4f6fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure(
            "Title.TLabel",
            background="#f4f6fb",
            foreground="#172033",
            font=("Microsoft YaHei UI", 20, "bold"),
        )
        style.configure("Subtitle.TLabel", background="#f4f6fb", foreground="#64748b")
        style.configure(
            "Version.TLabel",
            background="#e9e7ff",
            foreground="#4f46e5",
            padding=(10, 5),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "SectionTitle.TLabel",
            background="#ffffff",
            foreground="#172033",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure("Hint.TLabel", background="#ffffff", foreground="#64748b")
        style.configure(
            "Micro.TLabel",
            background="#ffffff",
            foreground="#94a3b8",
            font=("Microsoft YaHei UI", 8),
        )
        style.configure(
            "StatusName.TLabel",
            background="#ffffff",
            foreground="#94a3b8",
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        style.configure(
            "StatusValue.TLabel",
            background="#ffffff",
            foreground="#26324a",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure("Field.TEntry", fieldbackground="#f8fafc", padding=(10, 8))
        style.configure("TButton", padding=(12, 8), borderwidth=0)
        style.configure("Secondary.TButton", background="#eef2f7", foreground="#334155")
        style.map("Secondary.TButton", background=[("active", "#e2e8f0")])
        style.configure("Ghost.TButton", background="#f8fafc", foreground="#4f46e5")
        style.map("Ghost.TButton", background=[("active", "#eef2ff")])
        style.configure("Accent.TButton", background="#4f46e5", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#4338ca")])
        style.configure("Danger.TButton", background="#fff1f2", foreground="#be123c")
        style.map("Danger.TButton", background=[("active", "#ffe4e6")])
        style.configure(
            "Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#334155",
            rowheight=34,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#f8fafc",
            foreground="#64748b",
            padding=(8, 8),
            relief="flat",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("Treeview", background=[("selected", "#e9e7ff")], foreground=[("selected", "#312e81")])

    @staticmethod
    def _status_item(parent, ttk, column: int, title: str, variable) -> None:
        box = ttk.Frame(parent, style="Card.TFrame", padding=(8, 0))
        box.grid(row=0, column=column, sticky="ew")
        ttk.Label(box, text=title, style="StatusName.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(box, textvariable=variable, style="StatusValue.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )

    # ---------- 托盘 ----------

    def _start_tray(self) -> None:
        try:
            import pystray
        except ImportError:
            logger.warning("pystray 不可用，仅窗口模式运行")
            return
        try:
            from desktop.logo import tray_icon

            image = tray_icon()
        except Exception:
            logger.warning("品牌图标生成失败，使用默认图标")
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (64, 64), "#4f46e5")
            ImageDraw.Draw(image).rectangle((16, 16, 48, 48), fill="white")
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
        ws = getattr(app.client, "ws", None) if app.client else None
        online = "在线" if ws is not None else "等待连接"
        appium = "运行中" if app.appium.running else "已停止"
        self.status_var.set(f"Agent: {agent_id} | 连接: {online} | Appium: {appium}")
        self.agent_id_var.set(str(agent_id))
        self.connection_var.set(online)
        self.appium_var.set(appium)

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
        count = len(devices)
        self.device_count_var.set(
            f"已发现 {count} 台设备 · 每 3 秒自动刷新" if count else "暂未发现设备，请通过 USB 或无线方式连接"
        )

    # ---------- 操作 ----------

    def _on_save_server(self) -> None:
        url = self.server_var.get().strip()
        if not url.startswith(("ws://", "wss://")):
            from tkinter import messagebox

            messagebox.showwarning("提示", "服务器地址必须以 ws:// 或 wss:// 开头")
            return
        save_server_url(self.state_dir, url)
        update_server_url = getattr(self.app, "update_server_url", None)
        if update_server_url is not None:
            try:
                self.bridge.call(lambda: update_server_url(url))
            except Exception as exc:
                logger.warning("服务器地址已保存，但当前连接切换失败: %s", exc)
                from tkinter import messagebox

                messagebox.showwarning("提示", f"服务器地址已保存，请重启 Agent：{exc}")
                return
        from tkinter import messagebox

        messagebox.showinfo("提示", "服务器地址已保存，当前绑定和连接已切换")

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
        try:
            save_user_key(self.runtime_dir, key)
        except OSError as exc:
            messagebox.showwarning(
                "绑定成功",
                f"用户已绑定，但 Key 无法保存到运行目录：{exc}",
            )
        else:
            messagebox.showinfo(
                "绑定成功",
                f"已绑定用户（Agent: {result.get('agent_id')}）\nKey 已保存并以掩码显示。",
            )
        self._refresh_bindings()

    def _toggle_key_visibility(self) -> None:
        """切换 Key 明文/掩码显示，默认始终为掩码。"""
        self._key_visible = not self._key_visible
        self.key_entry.configure(show="" if self._key_visible else "•")
        self.key_visibility_button.configure(text="隐藏" if self._key_visible else "显示")

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
