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
        concurrency: int | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.enable_scans = enable_scans
        self.agent_sender = agent_sender
        self.poll_interval = (
            float(settings.worker_poll_interval) if poll_interval is None else poll_interval
        )
        self.execution_poll_interval = execution_poll_interval
        self.concurrency = settings.worker_concurrency if concurrency is None else concurrency
        if not 1 <= self.concurrency <= 32:
            raise ValueError("Worker concurrency must be between 1 and 32")
        self._stop_event = asyncio.Event()
        self._consumer_task: asyncio.Task | None = None
        self._execution_tasks: dict[int, asyncio.Task] = {}
        self._execution_started: dict[int, bool] = {}
        self._wake_event = asyncio.Event()
        self._scheduler: AsyncIOScheduler | None = None
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._consumer_task is not None and not self._consumer_task.done()

    @property
    def scans_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    @property
    def active_count(self) -> int:
        return len(self._execution_tasks)

    async def start(self) -> None:
        """幂等启动；扫描任务先注册，消费者随后开始认领队列。"""
        if self.running:
            return
        self._stop_event = asyncio.Event()
        self._stopping = False
        if self.enable_scans:
            self._start_scans()
            # 后端停机/重启期间 agent 无法心跳，DB 中残留 online 状态。
            # 立即执行一次心跳扫描，避免重启后等到下一个 60s 周期才置离线。
            await self._session_job(worker_service.agent_heartbeat_scan)()
        self._consumer_task = asyncio.create_task(
            self._claim_loop(), name=f"worker-consumer-{self.worker_id}"
        )
        logger.info(
            "Worker runtime %s 已启动（scans=%s, concurrency=%s）",
            self.worker_id,
            self.enable_scans,
            self.concurrency,
        )

    async def stop(self) -> None:
        """幂等停止，不再认领新任务并回收调度器与消费协程。"""
        scheduler = self._scheduler
        self._scheduler = None
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)

        self._stop_event.set()
        self._wake_event.set()
        self._stopping = True
        task = self._consumer_task
        self._consumer_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        active_tasks = list(self._execution_tasks.items())
        started_ids = {
            execution_id
            for execution_id, _task in active_tasks
            if self._execution_started.get(execution_id, False)
        }
        if active_tasks:
            done, pending = await asyncio.wait(
                [execution_task for _execution_id, execution_task in active_tasks],
                timeout=float(settings.worker_shutdown_grace_seconds),
            )
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            if pending:
                for execution_task in pending:
                    execution_task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                # 任务可能尚未开始执行，因而没有机会运行 run_reserved_execution
                # 的 CancelledError 清理分支；这类任务一定尚未下发给 Agent。
                for execution_id, _execution_task in active_tasks:
                    if (
                        execution_id not in started_ids
                        and not self._execution_started.get(execution_id, False)
                    ):
                        try:
                            await worker_service.restore_reserved_execution(
                                execution_id, self.worker_id
                            )
                        except Exception:
                            logger.exception(
                                "Worker %s 恢复未启动执行失败 execution=%s",
                                self.worker_id,
                                execution_id,
                            )
        self._execution_started.clear()
        logger.info("Worker runtime %s 已停止", self.worker_id)

    async def _claim_loop(self) -> None:
        while not self._stop_event.is_set():
            # 清除必须发生在容量检查和认领之前；等待函数不能再次清除，
            # 这样完成回调在检查与等待之间 set 事件时不会丢失唤醒。
            self._wake_event.clear()
            while not self._stop_event.is_set() and self.active_count < self.concurrency:
                try:
                    async with SessionLocal() as db:
                        item = await worker_service.claim_next_queue(db, self.worker_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # 单次数据库/调度异常只影响本轮，后台消费必须继续存活。
                    logger.exception("Worker %s 认领任务异常", self.worker_id)
                    break
                if item is None:
                    try:
                        async with SessionLocal() as db:
                            rejected = await worker_service.settle_next_unavailable_queue(db)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Worker %s 收敛不可用任务异常", self.worker_id)
                        break
                    if rejected:
                        continue
                    break
                execution_id = item.execution_id
                execution_task = asyncio.create_task(
                    self._run_reserved_execution(execution_id),
                    name=f"worker-execution-{self.worker_id}-{execution_id}",
                )
                self._execution_tasks[execution_id] = execution_task
                execution_task.add_done_callback(
                    lambda completed, eid=execution_id: self._execution_done(eid, completed)
                )

            if self._stop_event.is_set():
                break
            await self._wait_for_wakeup()

    async def _run_reserved_execution(self, execution_id: int) -> None:
        self._execution_started[execution_id] = True
        await worker_service.run_reserved_execution(
            None,
            execution_id,
            self.worker_id,
            agent_sender=self.agent_sender,
            poll_interval=self.execution_poll_interval,
        )

    def _execution_done(self, execution_id: int, task: asyncio.Task) -> None:
        self._execution_tasks.pop(execution_id, None)
        if not self._stopping:
            self._execution_started.pop(execution_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            # 读取任务异常，避免 asyncio 输出 "Task exception was never retrieved"，
            # 同时保证单个执行不会退出消费者。
            logger.exception(
                "Worker %s 执行任务异常 execution=%s", self.worker_id, execution_id
            )
        self._wake_event.set()

    async def _wait_for_wakeup(self) -> None:
        stop_waiter = asyncio.create_task(self._stop_event.wait())
        wake_waiter = asyncio.create_task(self._wake_event.wait())
        try:
            await asyncio.wait(
                [stop_waiter, wake_waiter],
                timeout=self.poll_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for waiter in (stop_waiter, wake_waiter):
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(stop_waiter, wake_waiter, return_exceptions=True)

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
                await worker_service.commit(db)

        return _run

    @staticmethod
    async def _daily_cleanup(db) -> None:
        await cleanup_service.cleanup_old_reports(db)
        await cleanup_service.cleanup_old_logs(db)
        await cleanup_service.cleanup_expired_prepares(db)
        logger.info("每日清理完成")
