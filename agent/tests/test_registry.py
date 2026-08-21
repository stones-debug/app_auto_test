"""Windows 方案 §4.1：设备注册表（3s 轮询 / 变更即报 / 30s 全量快照）测试。"""

import asyncio
import time

from devices.registry import DeviceRegistry

D1 = {"udid": "emulator-5554", "name": "emu1", "status": "idle", "connection_type": "usb"}
D2 = {"udid": "192.168.1.5:5555", "name": "Pixel_7", "status": "idle", "connection_type": "wifi"}


class SequenceScanner:
    """按调用次数依次返回预设设备列表。"""

    def __init__(self, *sequences) -> None:
        self.sequences = list(sequences)
        self.calls = 0

    def __call__(self) -> list[dict]:
        idx = min(self.calls, len(self.sequences) - 1)
        self.calls += 1
        return [dict(d) for d in self.sequences[idx]]


async def _wait_snapshots(collect: list, n: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while len(collect) < n and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert len(collect) >= n, f"期望至少 {n} 次快照，实际 {len(collect)}"


async def test_first_scan_reports_full_snapshot():
    scanner = SequenceScanner([D1])
    reg = DeviceRegistry(poll_interval=0.01, full_interval=100, scanner=scanner)
    snapshots: list[list[dict]] = []

    async def on_snapshot(devices: list[dict]):
        snapshots.append(devices)

    await reg.start(on_snapshot)
    try:
        await _wait_snapshots(snapshots, 1)
        assert snapshots[0][0]["udid"] == "emulator-5554"
        assert reg.current()[0]["status"] == "idle"
    finally:
        await reg.stop()


async def test_change_reports_immediately():
    # 第 1 次扫描 [D1]（初始全量），随后持续 [D1]，第 3 次起变为 [D2] → 变更即报
    scanner = SequenceScanner([D1], [D1], [D2], [D2], [D2])
    reg = DeviceRegistry(poll_interval=0.01, full_interval=100, scanner=scanner)
    snapshots: list[list[dict]] = []

    async def on_snapshot(devices: list[dict]):
        snapshots.append(devices)

    await reg.start(on_snapshot)
    try:
        await _wait_snapshots(snapshots, 2)
        assert snapshots[-1][0]["udid"] == "192.168.1.5:5555"  # 变化后的最新快照
    finally:
        await reg.stop()


async def test_no_change_waits_for_full_interval():
    scanner = SequenceScanner([D1])
    reg = DeviceRegistry(poll_interval=0.01, full_interval=0.2, scanner=scanner)
    snapshots: list[list[dict]] = []

    async def on_snapshot(devices: list[dict]):
        snapshots.append(devices)

    await reg.start(on_snapshot)
    try:
        # 运行约 0.5s：应只有初始全量 + 0.2s/0.4s 两次周期全量（3 次），而非每次轮询都上报
        await asyncio.sleep(0.5)
        await reg.stop()
        # 轮询每 10ms 一次，0.5s 内约 50 次扫描；若变更误报会远大于 3
        assert len(snapshots) <= 4, f"无变化时不应频繁上报: {len(snapshots)}"
        assert len(snapshots) >= 2
    finally:
        await reg.stop()


async def test_scanner_error_does_not_kill_loop():
    class FlakyScanner:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> list[dict]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("adb 崩溃")
            return [D1]

    reg = DeviceRegistry(poll_interval=0.01, full_interval=100, scanner=FlakyScanner())
    snapshots: list[list[dict]] = []

    async def on_snapshot(devices: list[dict]):
        snapshots.append(devices)

    await reg.start(on_snapshot)
    try:
        await _wait_snapshots(snapshots, 1)  # 异常后恢复，仍能上报
    finally:
        await reg.stop()
