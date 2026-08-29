"""Step S1：监控/限流/快照上限——metrics 渲染、preview 限流、SNAPSHOT_TOO_LARGE。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
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
    resp = await client.get("/metrics", headers={"X-Internal-Token": settings.internal_token})
    assert resp.status_code == 200
    assert "profile_resolve_total" in resp.text


# ---------- Step 5：/metrics 鉴权与来源限制 ----------


async def test_metrics_endpoint_requires_internal_token(client):
    """Step 5：指标端点不再裸奔——缺头与错误令牌一律 401。"""
    assert (await client.get("/metrics")).status_code == 401
    wrong = await client.get("/metrics", headers={"X-Internal-Token": "wrong"})
    assert wrong.status_code == 401


async def test_metrics_endpoint_allows_loopback_when_restricted():
    """Step 5：开启来源限制后，本机来源仍可访问（默认部署形态不被误伤）。"""
    settings.metrics_require_loopback = True
    try:
        transport = ASGITransport(app=app, client=("127.0.0.1", 123))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/metrics", headers={"X-Internal-Token": settings.internal_token}
            )
        assert resp.status_code == 200
    finally:
        settings.metrics_require_loopback = False


async def test_metrics_endpoint_rejects_remote_when_restricted():
    """Step 5：开启来源限制后，非本机来源返回 403（令牌正确也不放行）。"""
    settings.metrics_require_loopback = True
    try:
        transport = ASGITransport(app=app, client=("203.0.113.9", 45123))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/metrics", headers={"X-Internal-Token": settings.internal_token}
            )
        assert resp.status_code == 403
    finally:
        settings.metrics_require_loopback = False


def test_is_loopback_host():
    """Step 5：来源判定纯函数——缺失 client 信息一律视为非本机，避免误放行。"""
    from app.core.security import is_loopback_host

    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("203.0.113.9") is False
    assert is_loopback_host(None) is False
