import argparse
import asyncio
import logging

from app.core.config import settings
from app.services.worker_runtime import WorkerRuntime

logger = logging.getLogger("worker")


async def main() -> int:
    parser = argparse.ArgumentParser(description="APP 自动化测试平台 Worker")
    parser.add_argument("--worker-id", default="worker-001")
    parser.add_argument("--enable-scans", action="store_true", help="多实例时仅 worker-001 启用扫描")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if settings.worker_mode != "external":
        logger.error(
            "当前 WORKER_MODE=%s，独立 Worker 仅允许在 WORKER_MODE=external 时启动",
            settings.worker_mode,
        )
        return 2

    runtime = WorkerRuntime(args.worker_id, enable_scans=args.enable_scans)
    await runtime.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
