"""CR-21：部署安全基线（限流 / 版本校验 / 生产默认密钥拒绝）。"""
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings, validate_security_baseline, weak_secret_names
from app.core.database import SessionLocal
from app.core.ratelimit import reset_rate_limits
from app.main import app
from app.models import Agent
from app.ws.handlers import handle_register, version_supported

REG = {"username": "pytest_sec_user", "email": "sec@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------- 限流 ----------


async def test_login_rate_limited(client: AsyncClient):
    """CR-21：认证接口超限返回 429。"""
    old = settings.rate_limit_auth_per_minute
    settings.rate_limit_auth_per_minute = 3
    try:
        codes = []
        for _ in range(4):
            resp = await client.post(
                "/api/auth/login", json={"username": "nobody", "password": "wrong"}
            )
            codes.append(resp.status_code)
        assert codes[:3] == [401, 401, 401]  # 认证失败本身 401
        assert codes[3] == 429  # 第 4 次被限流
    finally:
        settings.rate_limit_auth_per_minute = old
        reset_rate_limits()


async def test_rate_limits_reset_between_tests(client: AsyncClient):
    """限流计数在测试间被清空（conftest）。"""
    resp = await client.post(
        "/api/auth/login", json={"username": "nobody", "password": "wrong"}
    )
    assert resp.status_code == 401  # 未被上一测试的限流影响


# ---------- Agent 版本比较 ----------


def test_version_supported_semver():
    assert version_supported("1.0.0", "1.0.0") is True
    assert version_supported("1.2.3", "1.0.0") is True
    assert version_supported("0.9.9", "1.0.0") is False
    assert version_supported("1.1.0-beta.1", "1.0.0") is True
    assert version_supported(None, "1.0.0") is True  # 未上报版本宽松放行
    assert version_supported("2.0", "1.0.0") is True


class FakeWS:
    def __init__(self) -> None:
        self.closed: tuple[int, str] | None = None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


async def test_register_rejects_old_agent_version():
    """CR-21：低于 min_agent_version 的 Agent 注册被拒绝。"""
    from sqlalchemy import delete

    from app.core.security import hash_psk

    async with SessionLocal() as db:
        agent = Agent(agent_key=hash_psk("sk-sec"), agent_id="pytest_sec_agent", status="offline")
        db.add(agent)
        await db.commit()
        agent_id_db = agent.id

    try:
        # 旧版本 → 拒绝（min_agent_version 随 Registry 升到 2.2.0）
        async with SessionLocal() as db:
            ws_old = FakeWS()
            reply = await handle_register(
                db, ws_old,
                {"type": "register", "agent_id": "pytest_sec_agent", "agent_key": "sk-sec", "version": "1.9.0"},
            )
        assert reply is None
        assert ws_old.closed is not None and ws_old.closed[0] == 1008

        # 新版本 → 接受
        async with SessionLocal() as db:
            ws_new = FakeWS()
            reply = await handle_register(
                db, ws_new,
                {"type": "register", "agent_id": "pytest_sec_agent", "agent_key": "sk-sec", "version": "2.2.0"},
            )
        assert reply is not None and reply["status"] == "ok"
        assert ws_new.closed is None
    finally:
        async with SessionLocal() as cleanup:
            await cleanup.execute(delete(Agent).where(Agent.id == agent_id_db))
            await cleanup.commit()


# ---------- 生产环境基线 ----------


def test_validate_security_baseline_production_rejects_defaults(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    try:
        with pytest.raises(RuntimeError, match="部署安全基线未通过"):
            validate_security_baseline()
    finally:
        monkeypatch.setattr(settings, "environment", "development")


def test_validate_security_baseline_production_accepts_strong(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", "x" * 40)
    monkeypatch.setattr(settings, "internal_token", "x" * 40)
    monkeypatch.setattr(settings, "agent_user_key_encryption_key", "x" * 40)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:strong-pw@db:5432/test_platform")
    try:
        validate_security_baseline()  # 不应抛异常
    finally:
        monkeypatch.setattr(settings, "environment", "development")
        monkeypatch.setattr(settings, "jwt_secret_key", "dev-secret-key-change-me-in-production-at-least-32-chars")
        monkeypatch.setattr(settings, "internal_token", "dev-internal-token-change-me")
        monkeypatch.setattr(settings, "agent_user_key_encryption_key", "")
        monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://dev:dev123@127.0.0.1:5432/test_platform")


def test_validate_security_baseline_production_rejects_weak_encryption_key(monkeypatch):
    """Windows 方案 §3.2：生产环境要求 AGENT_USER_KEY_ENCRYPTION_KEY >= 32 字符。"""
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", "x" * 40)
    monkeypatch.setattr(settings, "internal_token", "x" * 40)
    monkeypatch.setattr(settings, "agent_user_key_encryption_key", "short")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:strong-pw@db:5432/test_platform")
    try:
        with pytest.raises(RuntimeError, match="agent_user_key_encryption_key"):
            validate_security_baseline()
    finally:
        monkeypatch.setattr(settings, "environment", "development")
        monkeypatch.setattr(settings, "jwt_secret_key", "dev-secret-key-change-me-in-production-at-least-32-chars")
        monkeypatch.setattr(settings, "internal_token", "dev-internal-token-change-me")
        monkeypatch.setattr(settings, "agent_user_key_encryption_key", "")
        monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://dev:dev123@127.0.0.1:5432/test_platform")


def test_validate_security_baseline_development_always_passes(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    validate_security_baseline()  # 默认密钥在 development 下允许


# ---------- PSK 哈希健壮性 ----------


def test_verify_psk_invalid_hash_returns_false():
    """历史明文/损坏哈希不崩溃，返回 False（InvalidHashError 被吞掉）。"""
    from app.core.security import hash_psk, verify_psk

    assert verify_psk("sk-any", "sk-plaintext-legacy") is False  # 非 Argon2 格式
    assert verify_psk("sk-wrong", hash_psk("sk-right")) is False  # 哈希不匹配
    assert verify_psk("sk-right", hash_psk("sk-right")) is True


# ---------- Step 5：恒定时间比较 ----------


def test_constant_time_equals():
    """Step 5：内部令牌比较改为恒定时间，且非 ASCII 不得抛异常。"""
    from app.core.security import constant_time_equals

    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False
    assert constant_time_equals("abc", "abcd") is False
    assert constant_time_equals("", "") is True
    # hmac.compare_digest 的 str 版遇非 ASCII 会抛 TypeError，必须走字节比较
    assert constant_time_equals("令牌值", "令牌值") is True
    assert constant_time_equals("令牌值", "令牌其他") is False


# ---------- Step 5：CORS 通配护栏与弱配置可见性 ----------


def test_validate_security_baseline_production_rejects_wildcard_cors(monkeypatch):
    """Step 5：allow_credentials=True 下通配源等于对任意站点开放，生产必须拒绝。"""
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", "x" * 40)
    monkeypatch.setattr(settings, "internal_token", "x" * 40)
    monkeypatch.setattr(settings, "agent_user_key_encryption_key", "x" * 40)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:strong-pw@db:5432/test_platform")
    monkeypatch.setattr(settings, "cors_origins", ["*"])
    with pytest.raises(RuntimeError, match="cors_origins"):
        validate_security_baseline()


def test_weak_secret_names_detects_defaults(monkeypatch):
    """Step 5：默认/弱配置可被逐项检出（development 告警的输入）。"""
    monkeypatch.setattr(settings, "jwt_secret_key", "dev-secret-key-change-me-in-production-at-least-32-chars")
    monkeypatch.setattr(settings, "internal_token", "dev-internal-token-change-me")
    monkeypatch.setattr(settings, "agent_user_key_encryption_key", "")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://dev:dev123@127.0.0.1:5432/test_platform")
    assert set(weak_secret_names()) == {
        "jwt_secret_key",
        "internal_token",
        "agent_user_key_encryption_key",
        "database_url(默认密码 dev123)",
    }


def test_weak_secret_names_empty_when_hardened(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "x" * 40)
    monkeypatch.setattr(settings, "internal_token", "x" * 40)
    monkeypatch.setattr(settings, "agent_user_key_encryption_key", "x" * 40)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:strong-pw@db:5432/test_platform")
    assert weak_secret_names() == []


def test_development_warns_on_weak_secrets(monkeypatch, caplog):
    """Step 5：development 不阻断启动，但弱配置必须留下可见告警。"""
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "jwt_secret_key", "dev-secret-key-change-me-in-production-at-least-32-chars")
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        validate_security_baseline()
    assert any("jwt_secret_key" in record.getMessage() for record in caplog.records)


def test_development_stays_silent_when_hardened(monkeypatch, caplog):
    """Step 5：配置已加固时不产生噪音告警。"""
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "jwt_secret_key", "x" * 40)
    monkeypatch.setattr(settings, "internal_token", "x" * 40)
    monkeypatch.setattr(settings, "agent_user_key_encryption_key", "x" * 40)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:strong-pw@db:5432/test_platform")
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        validate_security_baseline()
    assert caplog.records == []
