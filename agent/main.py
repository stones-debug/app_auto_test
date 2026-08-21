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
        self.stop_event = asyncio.Event()
        self.uploader: Uploader | None = None

    async def run_execution(self, message: dict) -> None:
        execution_id = message["execution_id"]
        session_token = message.get("session_token")
        parameters = message.get("parameters") or {}
        cases = message.get("cases") or []
        mode = self.config.get("driver", "mock")
        driver = create_driver(mode)
        self.stop_event.clear()
        with tempfile.TemporaryDirectory(prefix=f"exec_{execution_id}_") as tmpdir:
            screenshots_dir = Path(tmpdir) / "screenshots"
            try:
                runner = TestRunner(
                    driver,
                    self.client.send,
                    execution_id,
                    parameters,
                    should_stop=self.stop_event.is_set,
                    screenshots_dir=screenshots_dir,
                    session_token=session_token,
                )
                overall = "passed"
                for case in cases:
                    if self.stop_event.is_set():
                        raise StopRequested("执行被用户停止")
                    status = await runner.run_case(case)
                    if status == "failed":
                        overall = "failed"
                await self.client.send(
                    {"type": "execution_result", "execution_id": execution_id, "session_token": session_token, "status": overall}
                )
                logger.info("execution=%s 完成: %s", execution_id, overall)
            except StopRequested:
                await self.client.send(
                    {"type": "execution_result", "execution_id": execution_id, "session_token": session_token, "status": "stopped"}
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

    async def send_device_list(self, _reply: dict | None = None) -> None:
        devices = discover_devices(self.config)
        await self.client.send({"type": "device_list", "devices": devices})
        logger.info("已上报 %s 台设备", len(devices))

    async def on_message(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == "start_test":
            await self.run_execution(message)
        elif msg_type == "stop_test":
            logger.info("收到 stop_test: execution=%s", message.get("execution_id"))
            self.stop_event.set()
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
