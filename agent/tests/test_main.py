import asyncio

from main import AgentApp


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)


def _sleep_case(duration: float = 5) -> list[dict]:
    return [
        {
            "case_id": 1,
            "case_name": "睡眠用例",
            "steps_snapshot": [{"order": 1, "action": "sleep", "params": {"duration": duration}}],
            "assertions_snapshot": [],
            "elements_snapshot": {},
        }
    ]


async def test_start_test_runs_in_background_task():
    """CR-06：start_test 创建独立任务，不阻塞接收循环。"""
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 1,
            "session_token": "t-1",
            "parameters": {},
            "device": {"udid": "u-1", "platform": "android"},
            "cases": _sleep_case(0.01),
        }
    )
    assert 1 in app.executions
    assert 1 in app.cancel_events
    await asyncio.wait_for(app.executions[1], timeout=2)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "passed"
    assert 1 not in app.executions  # 完成回调清理


async def test_stop_test_interrupts_blocking_execution():
    """CR-06：stop_test 立即生效（打断 sleep 等阻塞动作）并上报 stopped。"""
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    await app.on_message(
        {
            "type": "start_test",
            "execution_id": 7,
            "session_token": "t-7",
            "parameters": {},
            "device": {"udid": "u-7", "platform": "android"},
            "cases": _sleep_case(30),  # 长 sleep，等待 stop 打断
        }
    )
    await asyncio.sleep(0.05)
    await app.on_message({"type": "stop_test", "execution_id": 7})

    await asyncio.wait_for(app.executions[7], timeout=2)
    results = [m for m in app.client.sent if m["type"] == "execution_result"]
    assert results and results[0]["status"] == "stopped"
    assert 7 not in app.executions


async def test_duplicate_start_test_ignored():
    """CR-06：同一 execution 重复 start_test 被忽略。"""
    app = AgentApp({"driver": "mock"})
    app.client = FakeClient()
    msg = {
        "type": "start_test",
        "execution_id": 9,
        "session_token": "t-9",
        "parameters": {},
        "device": {"udid": "u-9", "platform": "android"},
        "cases": _sleep_case(0.01),
    }
    await app.on_message(msg)
    task = app.executions[9]
    await app.on_message(msg)  # 重复消息
    assert app.executions[9] is task  # 未创建新任务
    await asyncio.wait_for(task, timeout=2)
