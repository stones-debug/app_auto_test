import argparse
import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

import yaml

from executor import StopRequested, TestRunner, create_driver
from uploader import Uploader
from ws_client import AgentWSClient, AuthError

logger = logging.getLogger("agent.main")


def load_config(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def http_base_url(ws_url: str) -> str:
    """ws://host:port/xxx → http://host:port（截图上传走 HTTP，CR-07）。"""
    return ws_url.replace("ws://", "http://", 1).replace("wss://", "https://", 1).split("/")[0]


def discover_devices(config: dict) -> list[dict]:
    if config.get("driver") != "appium":
        return config.get("mock_devices") or []
    devices: list[dict] = []
    try:
        import subprocess

        out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) == 2 and parts[1] == "device":
                devices.append(
                    {"udid": parts[0], "name": parts[0], "platform": "android", "device_type": "real"}
                )
    except Exception as exc:
        logger.warning("ADB 不可用，设备列表为空: %s", exc)
    return devices


class AgentApp:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.client: AgentWSClient | None = None
        self.uploader: Uploader | None = None
        # CR-06：execution_id → 执行任务 / 取消事件 / 驱动（stop_test 立即生效）
        self.executions: dict[int, asyncio.Task] = {}
        self.cancel_events: dict[int, asyncio.Event] = {}
        self.drivers: dict[int, object] = {}

    async def _run_execution(self, message: dict) -> None:
        execution_id = message["execution_id"]
        session_token = message.get("session_token")
        parameters = message.get("parameters") or {}
        cases = message.get("cases") or []
        cancel_event = self.cancel_events[execution_id]
        mode = self.config.get("driver", "mock")
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
                driver.quit()
                self.drivers.pop(execution_id, None)

    async def send_device_list(self, _reply: dict | None = None) -> None:
        devices = discover_devices(self.config)
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
                    driver.interrupt()
                except Exception as exc:
                    logger.warning("interrupt 失败: %s", exc)
            task = self.executions.get(execution_id)
            if task is not None and not task.done():
                task.cancel()
        else:
            logger.debug("忽略消息: %s", msg_type)


async def main() -> None:
    parser = argparse.ArgumentParser(description="APP 自动化测试平台 Device Agent")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    args = parser.parse_args()

    config = load_config(args.config)
    logging.basicConfig(
        level=getattr(logging, (config.get("log_level") or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = AgentApp(config)
    client = AgentWSClient(
        url=config["server"],
        agent_key=config["agent_key"],
        agent_id=config.get("agent_id") or f"agent-{uuid.uuid4().hex[:8]}",
        heartbeat_interval=config.get("heartbeat_interval", 30),
    )
    app.client = client
    app.uploader = Uploader(
        base_url=http_base_url(config["server"]),
        agent_key=config["agent_key"],
        agent_id=config.get("agent_id") or "",
    )
    client.on_message = app.on_message
    client.on_registered = app.send_device_list

    try:
        await client.run_forever()
    except AuthError:
        logger.error("Agent 启动失败：认证失败")
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，退出")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
