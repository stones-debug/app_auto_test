"""ADB 设备注册表（Windows 方案 §4.1）。

- 每 3 秒轮询 `adb devices -l`（在工作线程中执行，不阻塞事件循环）；
- 设备集合变化（增/删/状态变/名称变）→ 立即上报快照；
- 无变化时每 30 秒上报一次完整快照。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from devices.adb import list_devices

logger = logging.getLogger("agent.registry")

SnapshotFn = Callable[[list[dict]], Awaitable[None]]


def _signature(devices: list[dict]) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        (d.get("udid", ""), d.get("status", ""), d.get("name", "")) for d in devices
    )


class DeviceRegistry:
    """设备注册表：轮询 + 变更检测 + 快照回调。"""

    def __init__(
        self,
        poll_interval: float = 3.0,
        full_interval: float = 30.0,
        scanner: Callable[[], list[dict]] = list_devices,
    ) -> None:
        self.poll_interval = poll_interval
        self.full_interval = full_interval
        self.scanner = scanner
        self._devices: list[dict] = []
        self._last_signature: frozenset | None = None
        self._last_full_at = 0.0
        self._task: asyncio.Task | None = None
        self._on_snapshot: SnapshotFn | None = None
        self._stop = asyncio.Event()

    async def start(self, on_snapshot: SnapshotFn) -> None:
        """启动轮询。首次扫描立即上报一次全量快照。"""
        self._on_snapshot = on_snapshot
        self._stop.clear()
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def current(self) -> list[dict]:
        return list(self._devices)

    async def scan_now(self) -> list[dict]:
        """立即扫描一次并返回快照（不触发回调）。"""
        self._devices = await asyncio.to_thread(self.scanner)
        return self.current()

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                devices = await asyncio.to_thread(self.scanner)  # 阻塞命令进工作线程
            except Exception as exc:
                logger.warning("设备扫描失败: %s", exc)
                devices = []
            signature = _signature(devices)
            now = asyncio.get_event_loop().time()
            changed = signature != self._last_signature
            due_full = self._last_full_at == 0.0 or (now - self._last_full_at) >= self.full_interval
            if changed or due_full:
                self._devices = devices
                self._last_signature = signature
                self._last_full_at = now
                if self._on_snapshot is not None:
                    try:
                        await self._on_snapshot(self.current())
                    except Exception:
                        logger.exception("设备快照上报失败")
            await asyncio.sleep(self.poll_interval)
