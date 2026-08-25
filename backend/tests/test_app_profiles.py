"""Step B5：APP 档案与发布版本 CRUD + 权限矩阵（方案 §11.2 子集）。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import AppProfileAuditLog

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
    create_body = {"request_id": str(uuid.uuid4()), "name": "DVR", "code": code}
    created = await client.post(f"/api/projects/{pid}/app-profiles", json=create_body, headers=h)
    assert created.status_code == 201
    profile = created.json()
    assert profile["name"] == "DVR"
    assert profile["code"] == code
    profile_id = profile["id"]
    assert profile["revision"] == 1
    replay = await client.post(f"/api/projects/{pid}/app-profiles", json=create_body, headers=h)
    assert replay.status_code == 201
    assert replay.json()["id"] == profile_id

    # 同名重复 → 409
    dup = await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "DVR", "code": code}, headers=h)
    assert dup.status_code == 409
    duplicate_code = await client.post(
        f"/api/projects/{pid}/app-profiles",
        json={"name": "另一个名称", "code": code},
        headers=h,
    )
    assert duplicate_code.status_code == 409

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

    release_body = {"request_id": str(uuid.uuid4()), "version": "1.0"}
    created = await client.post(f"/api/app-profiles/{profile_id}/releases", json=release_body, headers=h)
    assert created.status_code == 201
    release_id = created.json()["id"]
    replay = await client.post(f"/api/app-profiles/{profile_id}/releases", json=release_body, headers=h)
    assert replay.status_code == 201
    assert replay.json()["id"] == release_id

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
    node_key = str(uuid.uuid4())
    suite_id = (await client.post(f"/api/projects/{pid}/suites", json={"name": "套件A"}, headers=h)).json()["id"]
    case_id = (await client.post(f"/api/projects/{pid}/cases", json={"name": "用A", "steps": [{"key": node_key, "order": 1, "action": "sleep", "params": {"duration": 1}}], "assertions": []}, headers=h)).json()["id"]
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


async def test_overrides(client: AsyncClient):
    """元素/变量/节点覆盖 upsert + restore + 非法 patch 拒绝。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "覆盖项目"}, headers=h)).json()["id"]
    code = f"o{uuid.uuid4().hex[:6]}"
    profile_id = (await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "O", "code": code}, headers=h)).json()["id"]
    el_id = (await client.post(f"/api/projects/{pid}/elements", json={"name": "按钮", "locator_type": "id", "locator_value": "common"}, headers=h)).json()["id"]
    case_id = (await client.post(f"/api/projects/{pid}/cases", json={"name": "用B", "steps": [{"order": 1, "action": "click", "element_id": el_id, "params": {}}], "assertions": []}, headers=h)).json()["id"]
    node_key = str(uuid.uuid4())
    # 给用例加一个含该 node_key 的步骤
    await client.put(f"/api/cases/{case_id}", json={"steps": [{"order": 1, "key": node_key, "action": "click", "element_id": el_id, "params": {}}]}, headers=h)

    # 元素覆盖
    request_id = str(uuid.uuid4())
    element_body = {"request_id": request_id, "expected_revision": 1, "locator_type": "resource_id", "locator_value": "dvr_id"}
    r = await client.put(f"/api/app-profiles/{profile_id}/element-overrides/{el_id}", json=element_body, headers=h)
    assert r.status_code == 200
    assert r.json()["revision"] == 2
    replay = await client.put(f"/api/app-profiles/{profile_id}/element-overrides/{el_id}", json=element_body, headers=h)
    assert replay.status_code == 200
    assert replay.json() == r.json()
    async with SessionLocal() as db:
        audit_rows = (
            await db.execute(
                select(AppProfileAuditLog).where(
                    AppProfileAuditLog.profile_id == profile_id,
                    AppProfileAuditLog.request_id == request_id,
                )
            )
        ).scalars().all()
    assert len(audit_rows) == 1
    assert audit_rows[0].client_ip
    assert "httpx" in (audit_rows[0].user_agent or "")
    # 变量覆盖
    r = await client.put(f"/api/app-profiles/{profile_id}/variable-overrides/PKG", json={"expected_revision": 2, "value": "com.dvr"}, headers=h)
    assert r.status_code == 200
    assert r.json()["revision"] == 3
    # 节点覆盖
    r = await client.put(f"/api/app-profiles/{profile_id}/node-overrides/{case_id}/step/{node_key}", json={"expected_revision": 3, "patch": {"params": {"wait_timeout": 20}}}, headers=h)
    assert r.status_code == 200
    assert r.json()["revision"] == 4
    listed = await client.get(f"/api/app-profiles/{profile_id}/overrides", headers=h)
    assert listed.status_code == 200
    assert listed.json()["elements"][0]["locator_value"] == "dvr_id"
    assert listed.json()["variables"][0]["name"] == "PKG"
    assert listed.json()["nodes"][0]["node_key"] == node_key
    # 非法 patch（改 order）→ 422
    bad = await client.put(f"/api/app-profiles/{profile_id}/node-overrides/{case_id}/step/{node_key}", json={"expected_revision": 4, "patch": {"order": 5}}, headers=h)
    assert bad.status_code == 422
    missing = await client.put(
        f"/api/app-profiles/{profile_id}/node-overrides/{case_id}/step/{uuid.uuid4()}",
        json={"expected_revision": 4, "patch": {"params": {"wait_timeout": 10}}},
        headers=h,
    )
    assert missing.status_code == 422

    # restore 元素覆盖（当前 revision=4，非法 patch 已回滚未递增）
    r = await client.request("DELETE", f"/api/app-profiles/{profile_id}/element-overrides/{el_id}", json={"expected_revision": 4}, headers=h)
    assert r.status_code == 204
    got = await client.get(f"/api/app-profiles/{profile_id}", headers=h)
    assert got.status_code == 200
    assert got.json()["revision"] == 5
    repeated_restore = await client.request(
        "DELETE",
        f"/api/app-profiles/{profile_id}/element-overrides/{el_id}",
        json={"expected_revision": 5},
        headers=h,
    )
    assert repeated_restore.status_code == 204
    got = await client.get(f"/api/app-profiles/{profile_id}", headers=h)
    assert got.json()["revision"] == 5


async def test_workspace_and_nodes(client: AsyncClient):
    """工作台套件分页 + 用例懒加载 + 差异扁平列表。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "工作台项目"}, headers=h)).json()["id"]
    code = f"w{uuid.uuid4().hex[:6]}"
    profile_id = (await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "W", "code": code}, headers=h)).json()["id"]
    suite_id = (await client.post(f"/api/projects/{pid}/suites", json={"name": "套件W"}, headers=h)).json()["id"]
    case_id = (await client.post(f"/api/projects/{pid}/cases", json={"name": "用W", "steps": [{"order": 1, "key": str(uuid.uuid4()), "action": "click", "params": {}}], "assertions": []}, headers=h)).json()["id"]
    await client.post(f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h)

    ws = await client.get(f"/api/app-profiles/{profile_id}/workspace", headers=h)
    assert ws.status_code == 200
    assert ws.json()["total"] == 1
    assert ws.json()["items"][0]["node_type"] == "suite"

    nodes = await client.get(f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=suite&parent_id={suite_id}", headers=h)
    assert nodes.status_code == 200
    assert nodes.json()["items"][0]["node_type"] == "case"

    # 跳过一个用例后差异列表出现
    case_node = (await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={"expected_revision": 1, "operation": "skip", "reason": {"code": "unsupported"}, "targets": [{"type": "case", "case_id": case_id}]},
        headers=h,
    )).json()
    assert case_node["changed"] == 1
    diff = await client.get(f"/api/app-profiles/{profile_id}/differences?type=skipped", headers=h)
    assert diff.status_code == 200
    assert diff.json()["total"] >= 1
    assert diff.json()["items"][0]["target_type"] == "case"


async def test_workspace_nodes_enforce_project_and_inherit_parent_skip(client: AsyncClient):
    """子节点必须属于档案项目，并展示套件/用例级继承跳过状态。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "层级项目"}, headers=h)).json()["id"]
    other_pid = (await client.post("/api/projects", json={"name": "其他项目"}, headers=h)).json()["id"]
    profile_id = (
        await client.post(
            f"/api/projects/{pid}/app-profiles",
            json={"name": "层级档案", "code": f"tree{uuid.uuid4().hex[:6]}"},
            headers=h,
        )
    ).json()["id"]
    suite_id = (await client.post(f"/api/projects/{pid}/suites", json={"name": "层级套件"}, headers=h)).json()["id"]
    node_key = str(uuid.uuid4())
    case_id = (
        await client.post(
            f"/api/projects/{pid}/cases",
            json={"name": "层级用例", "steps": [{"order": 1, "key": node_key, "action": "sleep", "params": {"duration": 1}}]},
            headers=h,
        )
    ).json()["id"]
    await client.post(f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h)

    other_case_id = (
        await client.post(
            f"/api/projects/{other_pid}/cases",
            json={"name": "不可读取", "steps": [{"order": 1, "action": "sleep", "params": {"duration": 1}}]},
            headers=h,
        )
    ).json()["id"]
    leaked = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=case&parent_id={other_case_id}",
        headers=h,
    )
    assert leaked.status_code == 404

    skipped = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported", "note": "整套不支持"},
            "targets": [{"type": "suite", "suite_id": suite_id}],
        },
        headers=h,
    )
    assert skipped.status_code == 200
    cases = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=suite&parent_id={suite_id}",
        headers=h,
    )
    assert cases.json()["items"][0]["effective_status"] == "skipped"
    assert cases.json()["items"][0]["status_source"] == "inherited"
    nodes = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=case&parent_id={case_id}&ancestor_suite_id={suite_id}",
        headers=h,
    )
    assert nodes.json()["items"][0]["effective_status"] == "skipped"
    assert nodes.json()["items"][0]["status_source"] == "inherited"


async def test_execution_preview(client: AsyncClient):
    """执行预检：返回双 revision + 计数 + 排除项；空档案返回 PROFILE_EMPTY。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "预检项目"}, headers=h)).json()["id"]
    profile_id = (await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "PV", "code": f"pv{uuid.uuid4().hex[:6]}"}, headers=h)).json()["id"]
    release_id = (
        await client.post(
            f"/api/app-profiles/{profile_id}/releases",
            json={"version": "1.0"},
            headers=h,
        )
    ).json()["id"]
    case_id = (await client.post(f"/api/projects/{pid}/cases", json={"name": "预检用例", "steps": [{"order": 1, "key": str(uuid.uuid4()), "action": "sleep", "params": {"duration": 1}}], "assertions": []}, headers=h)).json()["id"]
    suite_id = (await client.post(f"/api/projects/{pid}/suites", json={"name": "预检套件"}, headers=h)).json()["id"]
    await client.post(f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h)

    resp = await client.post(
        "/api/executions/preview",
        json={"project_id": pid, "target": {"type": "suite", "ids": [suite_id]}, "app_profile_id": profile_id, "app_release_id": release_id, "device_id": None},
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_revision"] == 1
    assert data["test_asset_revision"] >= 4
    assert data["counts"]["executable_cases"] == 1
    assert data["counts"]["executable_steps"] == 1

    # 空档案（所有用例跳过）→ 400 PROFILE_EMPTY
    await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={"expected_revision": 1, "operation": "skip", "reason": {"code": "unsupported"}, "targets": [{"type": "case", "case_id": case_id}]},
        headers=h,
    )
    empty = await client.post(
        "/api/executions/preview",
        json={"project_id": pid, "target": {"type": "suite", "ids": [suite_id]}, "app_profile_id": profile_id, "app_release_id": release_id},
        headers=h,
    )
    assert empty.status_code == 400
    assert empty.json()["detail"]["code"] == "PROFILE_EMPTY"


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
