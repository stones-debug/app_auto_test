import asyncio
import sys
from types import SimpleNamespace

import pytest

import worker as worker_cli
from app import main as app_main
from app.core.config import settings
from app.services import worker_runtime
from app.services.worker_runtime import WorkerRuntime


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


async def _no_queue(_db, _worker_id):
    return None


async def _noop(_db):
    return None


async def test_runtime_start_stop_are_idempotent(monkeypatch):
    monkeypatch.setattr(worker_runtime, "SessionLocal", FakeSession)
    monkeypatch.setattr(worker_runtime.worker_service, "claim_next_queue", _no_queue)
    runtime = WorkerRuntime("runtime-test", enable_scans=False, poll_interval=60)

    await runtime.start()
    first_task = runtime._consumer_task
    await runtime.start()

    assert runtime.running
    assert runtime._consumer_task is first_task
    await runtime.stop()
    await runtime.stop()
    assert not runtime.running
    assert first_task is not None and first_task.done()


async def test_runtime_injects_agent_sender(monkeypatch):
    claimed = False
    dispatched = asyncio.Event()
    captured = {}

    async def claim(_db, _worker_id):
        nonlocal claimed
        if claimed:
            return None
        claimed = True
        return SimpleNamespace(execution_id=42)

    async def run(_db, execution_id, worker_id, *, agent_sender, poll_interval):
        captured.update(
            execution_id=execution_id,
            worker_id=worker_id,
            agent_sender=agent_sender,
            poll_interval=poll_interval,
        )
        dispatched.set()

    async def sender(_agent_id: int, _payload: dict) -> bool:
        return True

    monkeypatch.setattr(worker_runtime, "SessionLocal", FakeSession)
    monkeypatch.setattr(worker_runtime.worker_service, "claim_next_queue", claim)
    monkeypatch.setattr(worker_runtime.worker_service, "run_execution", run)
    runtime = WorkerRuntime(
        "embedded-test",
        enable_scans=False,
        agent_sender=sender,
        poll_interval=0.01,
        execution_poll_interval=0.02,
    )

    await runtime.start()
    await asyncio.wait_for(dispatched.wait(), timeout=1)
    await runtime.stop()

    assert captured == {
        "execution_id": 42,
        "worker_id": "embedded-test",
        "agent_sender": sender,
        "poll_interval": 0.02,
    }


async def test_runtime_survives_claim_error(monkeypatch):
    calls = 0
    recovered = asyncio.Event()

    async def flaky_claim(_db, _worker_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary database error")
        recovered.set()
        return None

    monkeypatch.setattr(worker_runtime, "SessionLocal", FakeSession)
    monkeypatch.setattr(worker_runtime.worker_service, "claim_next_queue", flaky_claim)
    runtime = WorkerRuntime("resilient-test", enable_scans=False, poll_interval=0.01)

    await runtime.start()
    await asyncio.wait_for(recovered.wait(), timeout=1)
    assert runtime.running
    await runtime.stop()
    assert calls >= 2


async def test_runtime_starts_and_stops_scheduler(monkeypatch):
    monkeypatch.setattr(worker_runtime, "SessionLocal", FakeSession)
    monkeypatch.setattr(worker_runtime.worker_service, "claim_next_queue", _no_queue)
    # 启动时立即执行一次心跳扫描（停机恢复），此处替换为 no-op，避免依赖真实 DB 会话
    monkeypatch.setattr(worker_runtime.worker_service, "agent_heartbeat_scan", _noop)
    runtime = WorkerRuntime("scheduler-test", enable_scans=True, poll_interval=60)

    await runtime.start()
    assert runtime.scans_running
    await runtime.stop()
    assert not runtime.scans_running


async def test_lifespan_embedded_uses_local_agent_manager(monkeypatch):
    instances = []

    class FakeRuntime:
        def __init__(self, worker_id, *, enable_scans, agent_sender):
            self.worker_id = worker_id
            self.enable_scans = enable_scans
            self.agent_sender = agent_sender
            self.started = False
            self.stopped = False
            instances.append(self)

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(settings, "worker_mode", "embedded")
    monkeypatch.setattr(settings, "worker_id", "fastapi-test")
    monkeypatch.setattr(app_main, "WorkerRuntime", FakeRuntime)
    monkeypatch.setattr(app_main, "validate_security_baseline", lambda: None)

    async with app_main.lifespan(app_main.app):
        runtime = app_main.app.state.worker_runtime
        assert runtime is instances[0]
        assert runtime.started
        assert runtime.worker_id == "fastapi-test"
        assert runtime.enable_scans is True
        assert runtime.agent_sender == app_main.agent_manager.send

    assert instances[0].stopped
    assert app_main.app.state.worker_runtime is None


@pytest.mark.parametrize("mode", ["external", "disabled"])
async def test_lifespan_does_not_start_runtime_for_non_embedded_mode(monkeypatch, mode):
    class UnexpectedRuntime:
        def __init__(self, *args, **kwargs):
            raise AssertionError("非嵌入模式不得构造 WorkerRuntime")

    monkeypatch.setattr(settings, "worker_mode", mode)
    monkeypatch.setattr(app_main, "WorkerRuntime", UnexpectedRuntime)
    monkeypatch.setattr(app_main, "validate_security_baseline", lambda: None)

    async with app_main.lifespan(app_main.app):
        assert app_main.app.state.worker_runtime is None


async def test_external_cli_refuses_to_start_in_embedded_mode(monkeypatch):
    monkeypatch.setattr(settings, "worker_mode", "embedded")
    monkeypatch.setattr(sys, "argv", ["worker.py"])
    assert await worker_cli.main() == 2
