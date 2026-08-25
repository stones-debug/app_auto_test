"""Step B5：APP 档案与发布版本 CRUD + 权限矩阵（方案 §11.2 子集）。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

OWNER = {"username": "pytest_profiles", "email": "profiles@tl-tek.com", "password": "test123"}
MEMBER = {"username": "pytest_profiles_member", "email": "profiles_m@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client: AsyncClient, user: dict) -> str:
    await client.post("/api/auth/register", json=user)
    return (await client.post("/api/auth/login", json={"username": user["username"], "password": "test123"})).json()["access_token"]


async def test_profile_crud(client: AsyncClient):
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "档案项目"}, headers=h)).json()["id"]

    code = f"dvr{uuid.uuid4().hex[:6]}"
    created = await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "DVR", "code": code}, headers=h)
    assert created.status_code == 201
    profile = created.json()
    assert profile["name"] == "DVR"
    assert profile["code"] == code
    profile_id = profile["id"]
    assert profile["revision"] == 1

    # 同名重复 → 409
    dup = await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "DVR", "code": code}, headers=h)
    assert dup.status_code == 409

    # 列表含 DVR + 计数
    listed = await client.get(f"/api/projects/{pid}/app-profiles", headers=h)
    assert listed.status_code == 200
    assert any(p["id"] == profile_id for p in listed.json())

    # 更新 revision+1
    updated = await client.patch(f"/api/app-profiles/{profile_id}", json={"expected_revision": 1, "description": "desc"}, headers=h)
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2

    # revision 冲突 → 409
    conflict = await client.patch(f"/api/app-profiles/{profile_id}", json={"expected_revision": 1, "description": "x"}, headers=h)
    assert conflict.status_code == 409

    # 删除（软删）
    deleted = await client.request("DELETE", f"/api/app-profiles/{profile_id}", json={"expected_revision": 2}, headers=h)
    assert deleted.status_code == 204
    got = await client.get(f"/api/app-profiles/{profile_id}", headers=h)
    assert got.status_code == 404


async def test_release_crud(client: AsyncClient):
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "版本项目"}, headers=h)).json()["id"]
    profile_id = (await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "V", "code": f"v{uuid.uuid4().hex[:6]}"}, headers=h)).json()["id"]

    created = await client.post(f"/api/app-profiles/{profile_id}/releases", json={"version": "1.0"}, headers=h)
    assert created.status_code == 201
    release_id = created.json()["id"]

    listed = await client.get(f"/api/app-profiles/{profile_id}/releases", headers=h)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    updated = await client.patch(f"/api/app-profile-releases/{release_id}", json={"version": "1.1"}, headers=h)
    assert updated.status_code == 200
    assert updated.json()["version"] == "1.1"

    deleted = await client.request("DELETE", f"/api/app-profile-releases/{release_id}", json={}, headers=h)
    assert deleted.status_code == 204


async def test_skip_batch(client: AsyncClient):
    """批量跳过：套件/用例/节点跳过、恢复、幂等 unchanged、revision 递增。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "跳过项目"}, headers=h)).json()["id"]
    code = f"s{uuid.uuid4().hex[:6]}"
    pid2 = (await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "S", "code": code}, headers=h)).json()["id"]

    # 建套件 + 用例
    suite_id = (await client.post(f"/api/projects/{pid}/suites", json={"name": "套件A"}, headers=h)).json()["id"]
    case_id = (await client.post(f"/api/projects/{pid}/cases", json={"name": "用A", "steps": [], "assertions": []}, headers=h)).json()["id"]
    node_key = str(uuid.uuid4())
    await client.post(f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h)

    resp = await client.post(
        f"/api/app-profiles/{pid2}/skip-rules/batch",
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported", "note": "DVR 无此功能"},
            "targets": [
                {"type": "suite", "suite_id": suite_id},
                {"type": "case", "case_id": case_id},
                {"type": "step", "case_id": case_id, "node_key": node_key},
            ],
        },
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["changed"] == 3
    assert data["revision_before"] == 1
    assert data["revision_after"] == 2

    # 幂等：重复提交相同 targets → unchanged
    idem = await client.post(
        f"/api/app-profiles/{pid2}/skip-rules/batch",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_revision": 2,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "suite", "suite_id": suite_id}],
        },
        headers=h,
    )
    assert idem.status_code == 200
    assert idem.json()["unchanged"] == 1
    assert idem.json()["changed"] == 0

    # 恢复套件
    restore = await client.post(
        f"/api/app-profiles/{pid2}/skip-rules/batch",
        json={
            "expected_revision": 3,
            "operation": "restore",
            "targets": [{"type": "suite", "suite_id": suite_id}],
        },
        headers=h,
    )
    assert restore.status_code == 200
    assert restore.json()["changed"] == 1

    # 跨项目目标 → 422
    other_pid = (await client.post("/api/projects", json={"name": "其他项目"}, headers=h)).json()["id"]
    bad_suite = (await client.post(f"/api/projects/{other_pid}/suites", json={"name": "跨项目套件"}, headers=h)).json()["id"]
    bad = await client.post(
        f"/api/app-profiles/{pid2}/skip-rules/batch",
        json={
            "expected_revision": 4,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "suite", "suite_id": bad_suite}],
        },
        headers=h,
    )
    assert bad.status_code == 422


async def test_member_cannot_manage_profile(client: AsyncClient):
    """Member 不能创建/修改档案（Owner/Admin only），但可读取。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "权限项目"}, headers=h)).json()["id"]

    member_token = await _register(client, MEMBER)
    # 获取 MEMBER user_id，作为项目成员加入
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {member_token}"})
    member_id = me.json()["id"]
    add = await client.post(f"/api/projects/{pid}/members", json={"user_id": member_id, "role": "member"}, headers=h)
    assert add.status_code == 201

    # Member 尝试创建 → 403
    resp = await client.post(
        f"/api/projects/{pid}/app-profiles",
        json={"name": "X", "code": f"x{uuid.uuid4().hex[:6]}"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert resp.status_code == 403

    # Member 可读取列表
    listed = await client.get(f"/api/projects/{pid}/app-profiles", headers={"Authorization": f"Bearer {member_token}"})
    assert listed.status_code == 200
