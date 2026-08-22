import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

REG = {"username": "pytest_user", "email": "pytest@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _ensure_user(client: AsyncClient) -> None:
    resp = await client.post("/api/auth/register", json=REG)
    assert resp.status_code == 201


async def test_health(client: AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_register_login_me(client: AsyncClient):
    reg = await client.post("/api/auth/register", json=REG)
    assert reg.status_code == 201
    assert reg.json()["user"]["username"] == "pytest_user"

    login = await client.post(
        "/api/auth/login",
        json={"username": REG["username"], "password": REG["password"]},
    )
    assert login.status_code == 200
    access_token = login.json()["access_token"]

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "pytest_user"


async def test_me_unauthorized(client: AsyncClient):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_refresh_flow(client: AsyncClient):
    await _ensure_user(client)
    login = await client.post(
        "/api/auth/login",
        json={"username": REG["username"], "password": REG["password"]},
    )
    assert login.status_code == 200
    refresh_token = login.json()["refresh_token"]

    refresh = await client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh.status_code == 200
    assert refresh.json()["token_type"] == "bearer"

    # 已被轮换的 refresh token 再次使用应失败
    reuse = await client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert reuse.status_code == 401


async def test_register_without_email(client: AsyncClient):
    reg = await client.post(
        "/api/auth/register",
        json={"username": "pytest_noemail", "password": "test123"},
    )
    assert reg.status_code == 201
    body = reg.json()
    assert body["user"]["username"] == "pytest_noemail"
    assert body["user"]["email"] is None
    assert body["access_token"] and body["refresh_token"]


async def test_register_duplicate_username(client: AsyncClient):
    await _ensure_user(client)
    reg = await client.post(
        "/api/auth/register",
        json={"username": REG["username"], "password": "test456"},
    )
    assert reg.status_code == 409
    assert reg.json()["detail"] == "用户名已存在"
