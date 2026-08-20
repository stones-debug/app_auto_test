import argparse
import asyncio
import logging
from collections.abc import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import worker_service

logger = logging.getLogger("worker")


def _job(fn: Callable) -> Callable:
    async def _run() -> None:
        async with SessionLocal() as db:
            await fn(db)

    return _run


async def claim_loop(worker_id: str) -> None:
    while True:
        try:
            async with SessionLocal() as db:
                item = await worker_service.claim_next_queue(db, worker_id)
                if item is not None:
                    await worker_service.run_execution(db, item.execution_id, worker_id)
        except Exception:
            logger.exception("worker 处理任务异常")
        await asyncio.sleep(settings.worker_poll_interval)


def start_scans(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_job(_job(worker_service.reclaim_stale_claimed), "interval", seconds=60, id="reclaim_claimed")
    scheduler.add_job(_job(worker_service.timeout_scan), "interval", seconds=60, id="timeout_scan")
    scheduler.add_job(
        _job(worker_service.agent_heartbeat_scan), "interval", seconds=60, id="agent_heartbeat_scan"
    )
    scheduler.start()
    logger.info("扫描任务已启用（reclaim/timeout/heartbeat）")


async def main() -> None:
    parser = argparse.ArgumentParser(description="APP 自动化测试平台 Worker")
    parser.add_argument("--worker-id", default="worker-001")
    parser.add_argument("--enable-scans", action="store_true", help="多实例时仅 worker-001 启用扫描")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Worker %s 启动（%s）", args.worker_id, args.enable_scans and "扫描开启" or "仅消费")

    scheduler: AsyncIOScheduler | None = None
    if args.enable_scans:
        scheduler = AsyncIOScheduler()
        start_scans(scheduler)

    try:
        await claim_loop(args.worker_id)
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
