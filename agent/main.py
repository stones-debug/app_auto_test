import argparse
import asyncio
import json
import logging
import tempfile
from pathlib import Path

import yaml

from appium_lifecycle import AppiumServer
from binding import BindingManager
from credentials import CredentialStore
from devices.registry import DeviceRegistry
from executor import StopRequested, TestRunner, create_driver
from state import load_or_create_install_id
from uploader import Uploader
from ws_client import AgentWSClient, AuthError

logger = logging.getLogger("agent.main")


def load_config(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def http_base_url(ws_url: str) -> str:
    """ws://host:port/xxx → http://host:port（截图上传走 HTTP，CR-07）。"""
    return ws_url.replace("ws://", "http://", 1).replace("wss://", "https://", 1).split("/")[0]


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
        # CR-06：execution_id → 执行任务 / 取消事件 / 驱动（stop_test 立即生效）
        self.executions: dict[int, asyncio.Task] = {}
        self.cancel_events: dict[int, asyncio.Event] = {}
        self.drivers: dict[int, object] = {}
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
        self._appium_refs = 0

    # ---------- Windows 方案 §4.1：设备上报与 Appium 生命周期 ----------

    async def start_device_reporting(self, _reply: dict | None = None) -> None:
        """注册成功后启动设备注册表轮询（3s 变更即报 / 30s 全量）。"""
        await self.registry.start(self.send_device_list)

    async def stop_device_reporting(self) -> None:
        await self.registry.stop()

    async def _ensure_appium(self) -> None:
        """需要执行时隐藏启动 Appium（引用计数，并发执行复用）。"""
        if self._appium_refs == 0:
            await asyncio.to_thread(self.appium.start)
        self._appium_refs += 1
        try:
            await self.appium.wait_ready()
        except Exception:
            self._appium_refs = max(0, self._appium_refs - 1)
            raise

    async def _release_appium(self) -> None:
        self._appium_refs = max(0, self._appium_refs - 1)
        if self._appium_refs == 0:
            await asyncio.to_thread(self.appium.stop)

    async def _run_execution(self, message: dict) -> None:
        execution_id = message["execution_id"]
        session_token = message.get("session_token")
        parameters = message.get("parameters") or {}
        cases = message.get("cases") or []
        cancel_event = self.cancel_events[execution_id]
        mode = self.config.get("driver", "mock")
        if mode == "appium":
            # Windows 方案 §4.1：Appium 按需隐藏启动，执行结束后清理
            await self._ensure_appium()
        # CR-08：用配置的 host/port/capabilities + Worker 下发的设备信息构造驱动
        driver = create_driver(mode, config=self.config, device=message.get("device"))
        self.drivers[execution_id] = driver
        with tempfile.TemporaryDirectory(prefix=f"exec_{execution_id}_") as tmpdir:
            screenshots_dir = Path(tmpdir) / "screenshots"
            try:
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
                for case in cases:
                    if cancel_event.is_set():
                        raise StopRequested("执行被用户停止")
                    status = await runner.run_case(case)
                    if status == "failed":
                        overall = "failed"
                await self.client.send(
                    {
                        "type": "execution_result",
                        "execution_id": execution_id,
                        "session_token": session_token,
                        "status": overall,
                    }
                )
                logger.info("execution=%s 完成: %s", execution_id, overall)
            except asyncio.CancelledError:
                # CR-06：stop_test 取消任务 → 立即上报 stopped（不重抛，任务正常结束）
                await self.client.send(
                    {
                        "type": "execution_result",
                        "execution_id": execution_id,
                        "session_token": session_token,
                        "status": "stopped",
                    }
                )
                logger.info("execution=%s 被取消", execution_id)
            except StopRequested:
                await self.client.send(
                    {
                        "type": "execution_result",
                        "execution_id": execution_id,
                        "session_token": session_token,
                        "status": "stopped",
                    }
                )
                logger.info("execution=%s 已停止", execution_id)
            except Exception as exc:
                logger.exception("execution=%s 异常", execution_id)
                await self.client.send(
                    {
                        "type": "execution_result",
                        "execution_id": execution_id,
                        "session_token": session_token,
                        "status": "error",
                        "error_message": str(exc),
                    }
                )
            finally:
                # Windows 方案 §2：Appium 清理可能阻塞，进入工作线程
                await asyncio.to_thread(driver.quit)
                self.drivers.pop(execution_id, None)
                if mode == "appium":
                    await self._release_appium()

    async def send_device_list(self, _reply: dict | None = None) -> None:
        if self.client is None:
            return
        devices = self.registry.current()
        await self.client.send({"type": "device_list", "devices": devices})
        logger.info("已上报 %s 台设备", len(devices))

    async def on_message(self, message: dict) -> None:
        """CR-06：接收循环只做分发；start_test 独立 task，stop_test 立即生效。"""
        msg_type = message.get("type")
        if msg_type == "start_test":
            execution_id = message.get("execution_id")
            if execution_id in self.executions:
                logger.warning("execution=%s 已在执行中，忽略重复 start_test", execution_id)
                return
            self.cancel_events[execution_id] = asyncio.Event()
            task = asyncio.create_task(self._run_execution(message))
            self.executions[execution_id] = task

            def _done(_task: asyncio.Task, _eid: int = execution_id) -> None:
                self.executions.pop(_eid, None)
                self.cancel_events.pop(_eid, None)

            task.add_done_callback(_done)
        elif msg_type == "stop_test":
            execution_id = message.get("execution_id")
            logger.info("收到 stop_test: execution=%s", execution_id)
            event = self.cancel_events.get(execution_id)
            if event is not None:
                event.set()
            driver = self.drivers.get(execution_id)
            if driver is not None and hasattr(driver, "interrupt"):
                try:
                    # Windows 方案 §2：终止 Appium 会话可能阻塞，进入工作线程
                    await asyncio.to_thread(driver.interrupt)
                except Exception as exc:
                    logger.warning("interrupt 失败: %s", exc)
            task = self.executions.get(execution_id)
            if task is not None and not task.done():
                task.cancel()
        else:
            logger.debug("忽略消息: %s", msg_type)


async def run_bind(bindings: BindingManager, user_key: str) -> None:
    result = await bindings.bind(user_key)
    print(json.dumps({"status": "ok", "agent_id": result.get("agent_id"), "user_id": result.get("user_id")}, ensure_ascii=False))


async def main() -> None:
    parser = argparse.ArgumentParser(description="APP 自动化测试平台 Device Agent")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--bind", metavar="USER_KEY", help="绑定用户 Key 后退出（无头绑定）")
    parser.add_argument("--state-dir", help="状态目录覆盖（测试用）")
    args = parser.parse_args()

    config = load_config(args.config)
    logging.basicConfig(
        level=getattr(logging, (config.get("log_level") or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    state = Path(args.state_dir) if args.state_dir else None
    install_id = load_or_create_install_id(state)
    creds = CredentialStore(path=(state / "credentials") if state else None)
    base_url = http_base_url(config["server"])
    bindings = BindingManager(base_url, install_id, creds)

    if args.bind:
        await run_bind(bindings, args.bind)
        return

    app = AgentApp(config, bindings=bindings)
    # Windows 方案 §3.2：优先使用绑定产生的机器 PSK；兼容旧配置 agent_key
    agent_key = config.get("agent_key") or bindings.machine_psk() or ""
    client = AgentWSClient(
        url=config["server"],
        agent_key=agent_key,
        agent_id=config.get("agent_id") or install_id,
        heartbeat_interval=config.get("heartbeat_interval", 30),
    )
    app.client = client
    app.uploader = Uploader(
        base_url=base_url,
        agent_key=agent_key,
        agent_id=config.get("agent_id") or install_id,
    )
    client.on_message = app.on_message
    client.on_registered = app.start_device_reporting

    if not agent_key:
        logger.error("未配置 agent_key 且本机无机器 PSK，无法注册；请先执行 --bind <用户Key> 或配置 agent_key")

    try:
        await client.run_forever()
    except AuthError:
        logger.error("Agent 启动失败：认证失败")
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，退出")
    finally:
        await app.stop_device_reporting()
        if app.appium.running:
            await asyncio.to_thread(app.appium.stop)
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
