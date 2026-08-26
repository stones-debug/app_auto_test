import argparse
import asyncio
import json
import logging
import logging.handlers
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml

from appium_lifecycle import AppiumServer
from binding import BindingManager
from credentials import CredentialStore
from devices.adb import heal_offline_emulator
from devices.registry import DeviceRegistry
from execution_supervisor import ExecutionRuntime
from executor import StopRequested, TestRunner, create_driver
from executor.protocol import protocol_version, verify_registry_matches_manifest
from executor.protocol_messages import DeviceListMessage, ExecutionResultMessage
from state import load_or_create_install_id, state_dir
from uploader import Uploader
from version import __version__
from ws_client import AgentWSClient, AuthError

logger = logging.getLogger("agent.main")


def load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        # 打包环境可能无 config.yaml，返回空配置由调用方回退状态目录
        return {}
    with config_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def http_origin(ws_url: str) -> str:
    """ws://host:port/xxx → http://host:port（截图上传走 HTTP，CR-07）。

    只接受 ws/wss：解析 netloc 并在 http/https 之间切换，丢弃 path/query/fragment。
    """
    parsed = urlsplit(ws_url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError("server 必须是有效的 ws:// 或 wss:// 地址")
    if parsed.username or parsed.password:
        raise ValueError("server URL 不允许内嵌凭据")
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunsplit((scheme, parsed.netloc, "", "", ""))


def _out(text: str) -> None:
    """窗口化打包（console=False）下 sys.stdout 为 None，print 会崩溃；统一安全输出。"""
    if sys.stdout is not None:
        print(text)


BANNER = r"""
    _     ____   ____
   / \   |  _ \ |  _ \
  / _ \  | |_) || |_) |
 / ___ \ |  __/ |  __/
/_/   \_\|_|    |_|
"""


def print_banner() -> None:
    """控制台启动横幅：logo 字样 + 名称版本（桌面打包版无控制台时静默）。"""
    _out(BANNER)
    _out(f"APP 自动化测试平台 · Device Agent v{__version__}")


def should_run_desktop(explicit_desktop: bool, *, frozen: bool | None = None) -> bool:
    """决定是否启动桌面界面。

    源码运行默认保持无头模式，便于开发和服务化部署；PyInstaller 打包后的
    ``console=False`` EXE 没有控制台，因此无参数启动（双击/开始菜单）必须默认
    进入桌面模式。``frozen`` 参数仅用于让该打包分支可稳定测试。
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    return explicit_desktop or frozen


class AgentApp:
    def __init__(
        self,
        config: dict,
        registry: DeviceRegistry | None = None,
        bindings: BindingManager | None = None,
        appium: AppiumServer | None = None,
    ) -> None:
        self.config = config
        self.client: AgentWSClient | None = None
        self.uploader: Uploader | None = None
        # CR-06：execution_id → 单一运行时（取消事件/任务/驱动不拆分字典）
        self.runtimes: dict[int, ExecutionRuntime] = {}
        # Windows 方案 §4.1：设备注册表 / Appium 生命周期
        self.registry = registry or DeviceRegistry(
            poll_interval=float(config.get("registry_poll_interval", 3.0)),
            full_interval=float(config.get("registry_full_interval", 30.0)),
        )
        self.bindings = bindings
        self.appium = appium or AppiumServer(
            host=config.get("appium_host", "127.0.0.1"),
            port=int(config.get("appium_port", 4723)),
            appium_bin=config.get("appium_bin"),
            node_bin=config.get("node_bin"),
            appium_js=config.get("appium_js"),
            command=config.get("appium_command"),
            log_dir=Path(config.get("log_dir", tempfile.gettempdir())) / "appium",
            ready_timeout=float(config.get("appium_ready_timeout", 60)),
        )
        self._appium_lock = asyncio.Lock()
        self._appium_refs = 0

    # ---------- Windows 方案 §4.1：设备上报与 Appium 生命周期 ----------

    async def start_device_reporting(self, _reply: dict | None = None) -> None:
        """注册成功后启动设备注册表轮询（3s 变更即报 / 30s 全量）。"""
        await self.registry.start(self.send_device_list)

    async def stop_device_reporting(self) -> None:
        await self.registry.stop()

    async def _ensure_appium(self) -> None:
        """需要执行时隐藏启动 Appium（引用计数，并发执行复用）。

        引用计数在同一锁内维护：并发 start 只启动一次 Server。
        """
        async with self._appium_lock:
            if self._appium_refs == 0:
                await asyncio.to_thread(self.appium.start)
            self._appium_refs += 1
            try:
                await self.appium.wait_ready()
            except Exception:
                self._appium_refs = max(0, self._appium_refs - 1)
                raise

    async def _release_appium(self) -> None:
        """并发执行全部结束后才停止 Server（引用计数在同一锁内维护）。"""
        async with self._appium_lock:
            self._appium_refs = max(0, self._appium_refs - 1)
            if self._appium_refs == 0:
                await asyncio.to_thread(self.appium.stop)

    async def _send_execution_result_safe(
        self,
        execution_id: int,
        session_token: str | None,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """上报终态。WS 断开时记录未上报结果，绝不让清理过程抛未检索异常。"""
        payload: ExecutionResultMessage = {
            "type": "execution_result",
            "execution_id": execution_id,
            "session_token": session_token,
            "status": status,
        }
        if error_message:
            payload["error_message"] = error_message
        if self.client is None:
            logger.warning("WS 未连接，execution=%s 结果未上报: %s", execution_id, status)
            return
        try:
            await self.client.send(payload)
        except Exception as exc:
            logger.warning("execution=%s 结果上报失败（%s）: %s", execution_id, status, exc)

    async def _run_execution(self, message: dict) -> None:
        execution_id = message["execution_id"]
        session_token = message.get("session_token")
        parameters = message.get("parameters") or {}
        suites = message.get("suites")
        mode = self.config.get("driver", "mock")
        runtime = self.runtimes[execution_id]
        cancel_event = runtime.cancel_event
        driver = None
        runner = None
        tmpdir = None
        completed_suite_ids: set[int] = set()
        started_suite_ids: set[int] = set()
        # 最外层 try/except/finally 覆盖 _ensure_appium/create_driver/临时目录创建
        try:
            if not isinstance(suites, list):
                raise ValueError("start_test 缺少 suites 字段（协议 V2）")
            if mode == "appium":
                # Windows 方案 §4.1：Appium 按需隐藏启动，执行结束后清理
                await self._ensure_appium()
            # 模拟器双 adb 通道互踢时设备可能 offline：先自愈再构造驱动
            device = message.get("device") or {}
            udid = device.get("udid")
            if udid:
                healed = await asyncio.to_thread(heal_offline_emulator, udid)
                if healed != udid:
                    logger.info("设备 %s 离线，已切换至在线通道 %s", udid, healed)
                    device = {**device, "udid": healed}
            # CR-08：用配置的 host/port/capabilities + Worker 下发的设备信息构造驱动
            driver = create_driver(mode, config=self.config, device=device)
            runtime.driver = driver
            if parameters.get("attach_to_current_app"):
                await asyncio.to_thread(driver.attach_to_current_app)
            tmpdir = tempfile.TemporaryDirectory(prefix=f"exec_{execution_id}_")
            screenshots_dir = Path(tmpdir.name) / "screenshots"
            runner = TestRunner(
                driver,
                self.client.send,
                execution_id,
                parameters,
                should_stop=cancel_event.is_set,
                screenshots_dir=screenshots_dir,
                session_token=session_token,
                uploader=self.uploader,
            )
            overall = "passed"
            for suite in suites:
                if cancel_event.is_set():
                    raise StopRequested("执行被用户停止")
                suite_id = int(suite.get("execution_suite_id") or 0)
                started_suite_ids.add(suite_id)
                status = await runner.run_suite(suite)
                completed_suite_ids.add(suite_id)
                if status == "failed":
                    overall = "failed"
                elif status == "error":
                    overall = "error"
                elif status == "stopped":
                    overall = "stopped"
            await self._send_execution_result_safe(execution_id, session_token, overall)
            logger.info("execution=%s 完成: %s", execution_id, overall)
        except asyncio.CancelledError:
            # CR-06：stop_test 取消任务 → 立即收敛套件状态并上报 stopped（不重抛，任务正常结束）
            await self._converge_stopped_suites(
                runner, suites, completed_suite_ids, started_suite_ids
            )
            await self._send_execution_result_safe(execution_id, session_token, "stopped")
            logger.info("execution=%s 被取消", execution_id)
        except StopRequested:
            await self._converge_stopped_suites(
                runner, suites, completed_suite_ids, started_suite_ids
            )
            await self._send_execution_result_safe(execution_id, session_token, "stopped")
            logger.info("execution=%s 已停止", execution_id)
        except Exception as exc:
            logger.exception("execution=%s 异常", execution_id)
            await self._send_execution_result_safe(execution_id, session_token, "error", str(exc))
        finally:
            # 清理顺序固定：driver.quit → 清 runtime.driver → release Appium → 从 map 删除
            if driver is not None:
                try:
                    # Windows 方案 §2：Appium 清理可能阻塞，进入工作线程
                    await asyncio.to_thread(driver.quit)
                except Exception as exc:
                    logger.warning("execution=%s driver.quit 失败: %s", execution_id, exc)
            if runtime.driver is not None:
                runtime.driver = None
            if mode == "appium":
                try:
                    await self._release_appium()
                except Exception as exc:
                    logger.warning("execution=%s release appium 失败: %s", execution_id, exc)
            self.runtimes.pop(execution_id, None)
            if tmpdir is not None:
                try:
                    tmpdir.cleanup()
                except Exception as exc:
                    logger.warning("execution=%s 临时目录清理失败: %s", execution_id, exc)

    async def _converge_stopped_suites(
        self,
        runner,
        suites,
        completed_suite_ids: set[int],
        started_suite_ids: set[int],
    ) -> None:
        """停止收敛：已在执行但未完成的套件 → stopped，未开始的套件 → skipped。

        - 已正常完成（completed）的套件不动；
        - 已进入执行（started）但因停止被打断的套件上报 stopped（用例终态由后端收敛）；
        - 尚未开始的套件整体标记 skipped。
        """
        if runner is None or not suites:
            return
        for suite in suites:
            suite_id = int(suite.get("execution_suite_id") or 0)
            if suite_id in completed_suite_ids:
                continue
            if suite_id in started_suite_ids:
                await runner.stop_suite(suite)
            else:
                await runner.skip_suite(suite)

    async def stop_all_executions(self) -> None:
        """取消全部执行并等待收敛（serve_agent / 桌面退出时调用）。"""
        for runtime in self.runtimes.values():
            runtime.cancel_event.set()
            task = runtime.task
            if task is not None and not task.done():
                task.cancel()
        pending = [r.task for r in self.runtimes.values() if r.task is not None and not r.task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.runtimes.clear()

    async def send_device_list(self, _reply: dict | None = None) -> None:
        if self.client is None:
            return
        devices = self.registry.current()
        msg: DeviceListMessage = {"type": "device_list", "devices": devices}
        await self.client.send(msg)
        logger.info("已上报 %s 台设备", len(devices))

    async def on_message(self, message: dict) -> None:
        """CR-06：接收循环只做分发；start_test 独立 task，stop_test 立即生效。"""
        msg_type = message.get("type")
        if msg_type == "start_test":
            execution_id = message.get("execution_id")
            if execution_id is None:
                logger.warning("start_test 缺少 execution_id，忽略")
                return
            # Step 10：protocol_version 不兼容时上报明确 error，不启动执行
            worker_protocol = message.get("protocol_version")
            if worker_protocol is not None and worker_protocol != protocol_version():
                await self._send_execution_result_safe(
                    execution_id,
                    message.get("session_token"),
                    "error",
                    f"协议版本不兼容: Agent 支持 {protocol_version()}，Worker 下发 {worker_protocol}",
                )
                return
            if execution_id in self.runtimes:
                # CR-06：重复 start_test 保持原任务，不得覆盖 runtime
                logger.warning("execution=%s 已在执行中，忽略重复 start_test", execution_id)
                return
            runtime = ExecutionRuntime(
                execution_id=execution_id,
                session_token=message.get("session_token"),
            )
            self.runtimes[execution_id] = runtime
            task = asyncio.create_task(self._run_execution(message))
            runtime.task = task

            def _done(_task: asyncio.Task, _eid: int = execution_id) -> None:
                self.runtimes.pop(_eid, None)
                try:
                    exc = _task.exception()
                    if exc is not None:
                        logger.warning("execution=%s 任务异常未上报: %s", _eid, exc)
                except asyncio.CancelledError:
                    pass

            task.add_done_callback(_done)
        elif msg_type == "stop_test":
            execution_id = message.get("execution_id")
            logger.info("收到 stop_test: execution=%s", execution_id)
            runtime = self.runtimes.get(execution_id)
            if runtime is None:
                logger.warning("stop_test 但 execution=%s 不在运行", execution_id)
                return
            # 顺序固定：set cancel event → 线程中 interrupt driver → cancel task
            runtime.cancel_event.set()
            driver = runtime.driver
            if driver is not None and hasattr(driver, "interrupt"):
                try:
                    # Windows 方案 §2：终止 Appium 会话可能阻塞，进入工作线程
                    await asyncio.to_thread(driver.interrupt)
                except Exception as exc:
                    logger.warning("interrupt 失败: %s", exc)
            task = runtime.task
            if task is not None and not task.done():
                task.cancel()
        else:
            logger.debug("忽略消息: %s", msg_type)


async def run_bind(bindings: BindingManager, user_key: str) -> None:
    result = await bindings.bind(user_key)
    _out(json.dumps({"status": "ok", "agent_id": result.get("agent_id"), "user_id": result.get("user_id")}, ensure_ascii=False))


def build_agent_app(
    config: dict, install_id: str, bindings: BindingManager
) -> tuple[AgentApp, AgentWSClient]:
    """使用安装 ID 与绑定生成的机器 PSK 构造 AgentApp + WS 客户端。

    机器 PSK 以回调形式传入：绑定 Key 后无需重启，重连即使用新凭据。
    """
    app = AgentApp(config, bindings=bindings)

    def key_provider() -> str:
        return bindings.machine_psk() or ""

    base_url = http_origin(config["server"])
    client = AgentWSClient(
        url=config["server"],
        agent_key=key_provider,
        agent_id=install_id,
        version=__version__,
        heartbeat_interval=config.get("heartbeat_interval", 30),
    )
    app.client = client
    app.uploader = Uploader(
        base_url=base_url,
        agent_key=key_provider,
        agent_id=install_id,
    )
    client.on_message = app.on_message
    client.on_registered = app.start_device_reporting
    if not key_provider():
        logger.error("本机无机器 PSK，无法注册；请先执行 --bind <用户Key>")
    return app, client


def configure_file_logging(state: Path) -> None:
    """LocalAppData 日志目录 + 按大小轮转（2MB × 5，Windows 方案 §4.1）。"""
    log_dir = state / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "agent.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


async def serve_agent(app: AgentApp, client: AgentWSClient, retry_on_auth: bool = False) -> None:
    """连接循环 + 退出清理。retry_on_auth：桌面模式认证失败不退出（绑定后自动重连）。"""
    try:
        await client.run_forever(retry_on_auth=retry_on_auth)
    except AuthError:
        logger.error("Agent 启动失败：认证失败")
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，退出")
    finally:
        # 先收敛执行任务，避免 pending task 在 loop 结束时被销毁
        await app.stop_all_executions()
        await app.stop_device_reporting()
        if app.appium.running:
            try:
                await asyncio.to_thread(app.appium.stop)
            except Exception as exc:
                logger.warning("Appium 停止失败: %s", exc)
        await client.close()


def run_desktop(app: AgentApp, client: AgentWSClient, state: Path, server_url: str) -> None:
    """Windows 方案 §4.1：托盘/窗口模式——asyncio 循环在后台线程，Tk 主循环在主线程。"""
    from desktop.controller import AsyncBridge, DesktopController, load_server_url

    loop = asyncio.new_event_loop()
    shutdown_done = threading.Event()

    async def _shutdown() -> None:
        """桌面退出清理：取消全部执行并等待收敛后再停循环。"""
        try:
            await app.stop_all_executions()
        except Exception as exc:
            logger.warning("桌面退出清理异常: %s", exc)
        finally:
            shutdown_done.set()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(serve_agent(app, client, retry_on_auth=True))
        except RuntimeError:
            # 桌面退出时 loop.stop() 可能打断 run_until_complete，属正常路径
            pass
        finally:
            loop.close()

    loop_thread = threading.Thread(target=_run_loop, daemon=True)
    loop_thread.start()
    bridge = AsyncBridge(loop)
    controller = DesktopController(
        app, bridge, state, state / "logs", load_server_url(state, server_url)
    )
    try:
        controller.run()
    finally:
        # 先提交异步 shutdown 并等待完成，再停止 loop，最后 join 后台线程
        try:
            bridge.call(_shutdown)
        except Exception as exc:
            # 后台循环已退出/关闭时无法投递协程；等待标记后继续
            logger.warning("桌面退出异步清理不可用: %s", exc)
            shutdown_done.set()
        shutdown_done.wait(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)


async def main() -> None:
    parser = argparse.ArgumentParser(description="APP 自动化测试平台 Device Agent")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--bind", metavar="USER_KEY", help="绑定用户 Key 后退出（无头绑定）")
    parser.add_argument("--state-dir", help="状态目录覆盖（测试用）")
    parser.add_argument("--desktop", action="store_true", help="托盘/窗口模式（打包安装版默认）")
    parser.add_argument("--self-check", action="store_true", help="安装自检后退出")
    args = parser.parse_args()

    config = load_config(args.config)
    logging.basicConfig(
        level=getattr(logging, (config.get("log_level") or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    state = Path(args.state_dir) if args.state_dir else None
    if state is None and not args.self_check:
        # 打包/常规模式：日志必须落盘（Windows 方案 §4.1，LocalAppData\\AppAutoTestAgent\\logs）
        state = state_dir()
    if state is not None:
        configure_file_logging(state)

    if not config.get("server"):
        # 打包环境无 config.yaml：服务器地址来自状态目录（桌面窗口可修改并持久化）
        from desktop.controller import load_server_url

        config["server"] = load_server_url(state or state_dir(state), "ws://127.0.0.1:8001/ws/agent")
        config.setdefault("driver", "appium")
        logger.info("未找到配置文件，使用状态目录服务器地址: %s", config["server"])

    if args.self_check:
        from selfcheck import run_self_check

        result = run_self_check(args.config, state)
        _out(json.dumps(result, ensure_ascii=False, indent=2))
        # Windows 方案 §4.2：自检失败以非零码退出（CI / publish.ps1 门禁）
        sys.exit(0 if result["ok"] else 1)

    print_banner()
    # Step 10：Registry 与 manifest 单一来源一致性断言（不一致拒绝启动）
    verify_registry_matches_manifest()

    install_id = load_or_create_install_id(state)
    creds = CredentialStore(path=(state / "credentials") if state else None)
    base_url = http_origin(config["server"])
    bindings = BindingManager(base_url, install_id, creds)

    if args.bind:
        await run_bind(bindings, args.bind)
        return

    app, client = build_agent_app(config, install_id, bindings)
    if should_run_desktop(args.desktop):
        run_desktop(app, client, state or state_dir(state), config["server"])
        return
    await serve_agent(app, client)


if __name__ == "__main__":
    asyncio.run(main())
