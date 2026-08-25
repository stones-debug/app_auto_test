"""Step B13：档案配置 WS 通知——profile_config_manager 分组广播。"""

from app.ws.managers import profile_config_manager


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


async def test_broadcast_to_project_members():
    ws1, ws2 = FakeWS(), FakeWS()
    await profile_config_manager.connect(1, ws1)
    await profile_config_manager.connect(1, ws2)
    await profile_config_manager.broadcast(
        1,
        {
            "type": "profile_revision_changed",
            "project_id": 1,
            "profile_id": 12,
            "profile_revision": 23,
            "changed_by": 7,
        },
    )
    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1
    assert ws1.sent[0]["type"] == "profile_revision_changed"
    assert ws1.sent[0]["profile_revision"] == 23


async def test_disconnect_removes_group():
    ws = FakeWS()
    await profile_config_manager.connect(2, ws)
    await profile_config_manager.disconnect(2, ws)
    await profile_config_manager.broadcast(2, {"type": "x"})
    assert ws.sent == []


async def test_broadcast_no_group_is_noop():
    await profile_config_manager.broadcast(3, {"type": "x"})
