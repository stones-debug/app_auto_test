"""Worker 运行宿主。

队列消费和定时扫描只有一套实现，可由 FastAPI lifespan 嵌入运行，也可由
``worker.py`` 作为独立进程运行。业务事务仍由 ``worker_service`` 负责。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import cleanup_service, worker_service

logger = logging.getLogger("worker.runtime")

AgentSender = Callable[[int, dict], Awaitable[bool]]
ScheduledJob = Callable[..., Awaitable[object]]


class WorkerRuntime:
    """管理一个队列消费者以及可选的恢复/清理扫描调度器。"""

    def __init__(
        self,
        worker_id: str,
        *,
        enable_scans: bool,
        agent_sender: AgentSender | None = None,
        poll_interval: float | None = None,
        execution_poll_interval: float = 5.0,
    ) -> None:
        self.worker_id = worker_id
        self.enable_scans = enable_scans
        self.agent_sender = agent_sender
        self.poll_interval = (
            float(settings.worker_poll_interval) if poll_interval is None else poll_interval
        )
        self.execution_poll_interval = execution_poll_interval
        self._stop_event = asyncio.Event()
        self._consumer_task: asyncio.Task | None = None
        self._scheduler: AsyncIOScheduler | None = None

    @property
    def running(self) -> bool:
        return self._consumer_task is not None and not self._consumer_task.done()

    @property
    def scans_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    async def start(self) -> None:
        """幂等启动；扫描任务先注册，消费者随后开始认领队列。"""
        if self.running:
            return
        self._stop_event = asyncio.Event()
        if self.enable_scans:
            self._start_scans()
        self._consumer_task = asyncio.create_task(
            self._claim_loop(), name=f"worker-consumer-{self.worker_id}"
        )
        logger.info(
            "Worker runtime %s 已启动（scans=%s）",
            self.worker_id,
            self.enable_scans,
        )

    async def stop(self) -> None:
        """幂等停止，不再认领新任务并回收调度器与消费协程。"""
        scheduler = self._scheduler
        self._scheduler = None
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)

        self._stop_event.set()
        task = self._consumer_task
        self._consumer_task = None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        logger.info("Worker runtime %s 已停止", self.worker_id)

    async def _claim_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with SessionLocal() as db:
                    item = await worker_service.claim_next_queue(db, self.worker_id)
                    if item is not None:
                        await worker_service.run_execution(
                            db,
                            item.execution_id,
                            self.worker_id,
                            agent_sender=self.agent_sender,
                            poll_interval=self.execution_poll_interval,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                # 单次数据库/调度异常只影响本轮，后台消费必须继续存活。
                logger.exception("Worker %s 处理任务异常", self.worker_id)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

    def _start_scans(self) -> None:
        if self._scheduler is not None and self._scheduler.running:
            return
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            self._session_job(worker_service.reclaim_stale_claimed),
            "interval",
            seconds=60,
            id="reclaim_claimed",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            self._session_job(worker_service.timeout_scan),
            "interval",
            seconds=60,
            id="timeout_scan",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            self._session_job(worker_service.agent_heartbeat_scan),
            "interval",
            seconds=60,
            id="agent_heartbeat_scan",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            self._session_job(worker_service.finalize_unfinished_terminal),
            "interval",
            seconds=60,
            id="finalize_terminal",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            self._session_job(self._daily_cleanup),
            "cron",
            hour=3,
            id="daily_cleanup",
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        self._scheduler = scheduler
        logger.info("Worker %s 扫描任务已启用", self.worker_id)

    @staticmethod
    def _session_job(fn: ScheduledJob) -> Callable[[], Awaitable[None]]:
        async def _run() -> None:
            async with SessionLocal() as db:
                await fn(db)

        return _run

    @staticmethod
    async def _daily_cleanup(db) -> None:
        await cleanup_service.cleanup_old_reports(db)
        await cleanup_service.cleanup_old_logs(db)
        logger.info("每日清理完成")
