"""Windows 方案 §3.4：安装包 manifest、下载令牌、流式下载与路径穿越测试。"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app
from app.services import release_service

REG = {"username": "pytest_release_user", "email": "rl@tl-tek.com", "password": "test123"}

MANIFEST = {
    "version": "1.1.0",
    "filename": "app-auto-test-agent-1.1.0-windows-x64-setup.exe",
    "sha256": "a" * 64,
    "size": 123456,
    "published_at": "2026-08-21T00:00:00Z",
}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def release_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "agent_releases_path", str(tmp_path))
    (tmp_path / "latest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    exe = tmp_path / MANIFEST["filename"]
    exe.write_bytes(b"fake-installer-bytes")
    return tmp_path


async def _login(client: AsyncClient) -> dict:
    await client.post("/api/auth/register", json=REG)
    login = await client.post("/api/auth/login", json={"username": REG["username"], "password": REG["password"]})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_latest_and_download_flow(client: AsyncClient, release_dir: Path):
    headers = await _login(client)

    latest = await client.get("/api/agent/releases/latest", headers=headers)
    assert latest.status_code == 200
    assert latest.json()["version"] == "1.1.0"
    assert latest.json()["filename"] == MANIFEST["filename"]

    token_resp = await client.post("/api/agent/releases/latest/download-token", headers=headers)
    assert token_resp.status_code == 200
    token = token_resp.json()["token"]

    download = await client.get(
        f"/api/agent/releases/download/{MANIFEST['filename']}?token={token}"
    )
    assert download.status_code == 200
    assert download.content == b"fake-installer-bytes"
    assert "attachment" in download.headers.get("content-disposition", "")
    assert "app-auto-test-agent-1.1.0" in download.headers.get("content-disposition", "")


async def test_download_requires_login(client: AsyncClient, release_dir: Path):
    resp = await client.get("/api/agent/releases/latest")
    assert resp.status_code == 401


async def test_download_rejects_wrong_filename_token(client: AsyncClient, release_dir: Path):
    headers = await _login(client)
    token = (await client.post("/api/agent/releases/latest/download-token", headers=headers)).json()["token"]
    # 令牌限定文件名：换文件名 → 403
    resp = await client.get(f"/api/agent/releases/download/other.exe?token={token}")
    assert resp.status_code == 403


async def test_download_rejects_expired_token(client: AsyncClient, release_dir: Path):
    await _login(client)
    expired = jwt.encode(
        {
            "sub": MANIFEST["filename"],
            "type": "download",
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    resp = await client.get(f"/api/agent/releases/download/{MANIFEST['filename']}?token={expired}")
    assert resp.status_code == 403


async def test_download_rejects_path_traversal(client: AsyncClient, release_dir: Path):
    headers = await _login(client)
    token = (await client.post("/api/agent/releases/latest/download-token", headers=headers)).json()["token"]
    resp = await client.get(f"/api/agent/releases/download/..%2F..%2Fetc%2Fpasswd?token={token}")
    assert resp.status_code in (403, 404)


def test_resolve_release_file_rejects_escape(release_dir: Path):
    with pytest.raises(release_service.ReleaseNotFound):
        release_service.resolve_release_file("..\\..\\outside.exe")
    with pytest.raises(release_service.ReleaseNotFound):
        release_service.resolve_release_file("missing.exe")


def test_verify_download_token_limits_filename(release_dir: Path):
    token = release_service.issue_download_token(MANIFEST["filename"])
    assert release_service.verify_download_token(token, MANIFEST["filename"]) is True
    assert release_service.verify_download_token(token, "other.exe") is False
    assert release_service.verify_download_token("garbage", MANIFEST["filename"]) is False
