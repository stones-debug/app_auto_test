"""Step S1：监控/限流/快照上限——metrics 渲染、preview 限流、SNAPSHOT_TOO_LARGE。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.metrics import inc_rule, observe_snapshot, render_metrics


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_render_metrics_text_format():
    inc_rule("case")
    inc_rule("case")
    observe_snapshot(1024, 3.5)
    text = render_metrics()
    assert 'profile_rules_total{target_type="case"} 2' in text
    assert "execution_snapshot_bytes_count " in text
    assert "execution_snapshot_create_duration_seconds_sum" in text


async def test_metrics_endpoint(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "profile_resolve_total" in resp.text
