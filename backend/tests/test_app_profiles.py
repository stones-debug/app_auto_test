"""Step B5：APP 档案与发布版本 CRUD + 权限矩阵（方案 §11.2 子集）。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import AppProfileAuditLog, AppProfileSuiteCaseVariableOverride

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
    membership_id = (await client.post(f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h)).json()[0]["id"]

    resp = await client.post(
        f"/api/app-profiles/{pid2}/skip-rules/batch",
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported", "note": "DVR 无此功能"},
            "targets": [
                {"type": "suite", "suite_id": suite_id},
                {"type": "case", "suite_case_id": membership_id},
                {
                    "type": "step",
                    "suite_case_id": membership_id,
                    "node_key": node_key,
                },
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
    suite_id = (await client.post(f"/api/projects/{pid}/suites", json={"name": "覆盖套件"}, headers=h)).json()["id"]
    membership_id = (await client.post(
        f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h,
    )).json()[0]["id"]
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
    r = await client.put(f"/api/app-profiles/{profile_id}/node-overrides/{membership_id}/step/{node_key}", json={"expected_revision": 3, "patch": {"params": {"wait_timeout": 20}}}, headers=h)
    assert r.status_code == 200
    assert r.json()["revision"] == 4
    listed = await client.get(f"/api/app-profiles/{profile_id}/overrides", headers=h)
    assert listed.status_code == 200
    assert listed.json()["elements"][0]["locator_value"] == "dvr_id"
    assert listed.json()["variables"][0]["name"] == "PKG"
    assert listed.json()["nodes"][0]["suite_id"] == suite_id
    assert listed.json()["nodes"][0]["node_key"] == node_key
    without_nodes = await client.get(
        f"/api/app-profiles/{profile_id}/overrides?include_nodes=false", headers=h
    )
    assert without_nodes.status_code == 200
    assert without_nodes.json()["elements"][0]["locator_value"] == "dvr_id"
    assert without_nodes.json()["variables"][0]["name"] == "PKG"
    assert without_nodes.json()["nodes"] == []
    # 非法 patch（改 order）→ 422
    bad = await client.put(f"/api/app-profiles/{profile_id}/node-overrides/{membership_id}/step/{node_key}", json={"expected_revision": 4, "patch": {"order": 5}}, headers=h)
    assert bad.status_code == 422
    missing = await client.put(
        f"/api/app-profiles/{profile_id}/node-overrides/{membership_id}/step/{uuid.uuid4()}",
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

    async with SessionLocal() as db:
        db.add(AppProfileSuiteCaseVariableOverride(
            profile_id=profile_id, suite_case_id=membership_id, name="PKG", value="occurrence"
        ))
        await db.commit()
    counted = await client.get(f"/api/app-profiles/{profile_id}", headers=h)
    assert counted.json()["override_counts"]["variable"] == 2


async def test_node_override_api_rejects_variable_overrides(client: AsyncClient):
    """节点覆盖只允许正常节点字段，变量必须走 occurrence API。"""
    token = await _register(client, {"username": f"pytest_var_{uuid.uuid4().hex[:8]}", "email": f"var_{uuid.uuid4().hex[:8]}@tl-tek.com", "password": "test123"})
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "步骤变量项目"}, headers=h)).json()["id"]
    profile_id = (await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "V", "code": f"v{uuid.uuid4().hex[:6]}"}, headers=h)).json()["id"]
    node_key = str(uuid.uuid4())
    case_resp = await client.post(
        f"/api/projects/{pid}/cases", headers=h,
        json={"name": "变量步骤", "steps": [
            {"key": node_key, "order": 1, "action": "launch_app", "params": {"package": "${pkg}"}},
        ]},
    )
    assert case_resp.status_code == 201, case_resp.text
    case_id = case_resp.json()["id"]
    suite_id = (await client.post(f"/api/projects/{pid}/suites", json={"name": "变量套件"}, headers=h)).json()["id"]
    membership_id = (await client.post(
        f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h,
    )).json()[0]["id"]
    valid = await client.put(
        f"/api/app-profiles/{profile_id}/node-overrides/{membership_id}/step/{node_key}",
        json={"expected_revision": 1, "patch": {"variable_overrides": {"pkg": ""}}}, headers=h,
    )
    assert valid.status_code == 422, valid.text
    unknown = await client.put(
        f"/api/app-profiles/{profile_id}/node-overrides/{membership_id}/step/{node_key}",
        json={"expected_revision": 1, "patch": {"variable_overrides": {"other": "x"}}}, headers=h,
    )
    assert unknown.status_code == 422


async def test_node_override_uses_exact_duplicate_occurrence(client: AsyncClient):
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "精确 occurrence"}, headers=h)).json()["id"]
    profile_id = (await client.post(
        f"/api/projects/{pid}/app-profiles",
        json={"name": "精确", "code": f"exact{uuid.uuid4().hex[:6]}"}, headers=h,
    )).json()["id"]
    element_id = (await client.post(
        f"/api/projects/{pid}/elements",
        json={"name": "按钮", "locator_type": "id", "locator_value": "button"}, headers=h,
    )).json()["id"]
    node_key = str(uuid.uuid4())
    case_id = (await client.post(
        f"/api/projects/{pid}/cases",
        json={"name": "重复用例", "steps": [{"key": node_key, "order": 1, "action": "click", "element_id": element_id, "params": {}}]},
        headers=h,
    )).json()["id"]
    suite_id = (await client.post(
        f"/api/projects/{pid}/suites", json={"name": "重复套件"}, headers=h,
    )).json()["id"]
    first = (await client.post(
        f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h,
    )).json()[0]["id"]
    second = (await client.post(
        f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h,
    )).json()[0]["id"]
    patched = await client.put(
        f"/api/app-profiles/{profile_id}/node-overrides/{second}/step/{node_key}",
        json={"expected_revision": 1, "patch": {"params": {"wait_timeout": 20}}}, headers=h,
    )
    assert patched.status_code == 200, patched.text
    first_nodes = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=case&parent_id={case_id}&ancestor_suite_id={suite_id}&suite_case_id={first}",
        headers=h,
    )
    second_nodes = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=case&parent_id={case_id}&ancestor_suite_id={suite_id}&suite_case_id={second}",
        headers=h,
    )
    assert first_nodes.json()["items"][0]["override_template"]["params"].get("wait_timeout") != 20
    assert second_nodes.json()["items"][0]["override_template"]["params"]["wait_timeout"] == 20
    restored = await client.request(
        "DELETE", f"/api/app-profiles/{profile_id}/node-overrides/{second}/step/{node_key}",
        json={"expected_revision": 2}, headers=h,
    )
    assert restored.status_code == 204


async def test_element_override_smart(client: AsyncClient):
    """smart 元素覆盖 upsert 成功返回完整 config；缺 config/普通带 config 被拒。

    model_dump 会显式序列化默认 None 字段（index），SMART_OVERRIDE_CONFIG 与其一致以便精确断言。
    """
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "智能覆盖项目"}, headers=h)).json()["id"]
    code = f"sm{uuid.uuid4().hex[:6]}"
    profile_id = (await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "SM", "code": code}, headers=h)).json()["id"]
    el_id = (await client.post(f"/api/projects/{pid}/elements", json={"name": "按钮", "locator_type": "id", "locator_value": "common"}, headers=h)).json()["id"]

    smart_config = {
        "version": 1,
        "alternatives": [
            {
                "anchor": [{"attribute": "text", "operator": "equals", "value": "登录"}],
                "path": [{"axis": "parent", "depth": 1}],
                "target": [{"attribute": "resource_id", "operator": "contains", "value": "btn"}],
            }
        ],
        "search": {"scroll": True, "direction": "up", "max_swipes": 8, "duration_ms": 500, "settle_ms": 300},
        "selection": {"policy": "unique", "index": None},
    }

    # 合法 smart 覆盖
    r = await client.put(
        f"/api/app-profiles/{profile_id}/element-overrides/{el_id}",
        json={"request_id": str(uuid.uuid4()), "expected_revision": 1, "locator_type": "smart", "locator_config": smart_config},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["revision"] == 2
    assert r.json()["locator_type"] == "smart"

    # 列表返回的覆盖含完整 locator_config
    listed = await client.get(f"/api/app-profiles/{profile_id}/overrides", headers=h)
    assert listed.status_code == 200
    override = listed.json()["elements"][0]
    assert override["locator_type"] == "smart"
    assert override["locator_value"] is None
    assert override["locator_config"] == smart_config

    # smart 缺 locator_config → 422
    missing = await client.put(
        f"/api/app-profiles/{profile_id}/element-overrides/{el_id}",
        json={"request_id": str(uuid.uuid4()), "expected_revision": 2, "locator_type": "smart"},
        headers=h,
    )
    assert missing.status_code == 422

    # 普通覆盖携带 locator_config → 422
    bad = await client.put(
        f"/api/app-profiles/{profile_id}/element-overrides/{el_id}",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_revision": 2,
            "locator_type": "resource_id",
            "locator_value": "dvr_id",
            "locator_config": smart_config,
        },
        headers=h,
    )
    assert bad.status_code == 422


async def test_workspace_and_nodes(client: AsyncClient):
    """工作台套件分页 + 用例懒加载 + 差异扁平列表。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "工作台项目"}, headers=h)).json()["id"]
    code = f"w{uuid.uuid4().hex[:6]}"
    profile_id = (await client.post(f"/api/projects/{pid}/app-profiles", json={"name": "W", "code": code}, headers=h)).json()["id"]
    suite_id = (await client.post(f"/api/projects/{pid}/suites", json={"name": "套件W"}, headers=h)).json()["id"]
    el_id = (await client.post(f"/api/projects/{pid}/elements", json={"name": "按钮W", "locator_type": "id", "locator_value": "btn_w"}, headers=h)).json()["id"]
    case_id = (await client.post(f"/api/projects/{pid}/cases", json={"name": "用W", "steps": [{"order": 1, "key": str(uuid.uuid4()), "action": "click", "element_id": el_id, "params": {}}], "assertions": []}, headers=h)).json()["id"]
    membership_id = (await client.post(f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h)).json()[0]["id"]

    ws = await client.get(f"/api/app-profiles/{profile_id}/workspace", headers=h)
    assert ws.status_code == 200
    assert ws.json()["total"] == 1
    assert ws.json()["execution_selectable_total"] == 1
    assert ws.json()["items"][0]["node_type"] == "suite"

    nodes = await client.get(f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=suite&parent_id={suite_id}", headers=h)
    assert nodes.status_code == 200
    assert nodes.json()["items"][0]["node_type"] == "case"

    step_nodes = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=case&parent_id={case_id}&ancestor_suite_id={suite_id}&suite_case_id={membership_id}",
        headers=h,
    )
    assert step_nodes.status_code == 200
    step_item = next(item for item in step_nodes.json()["items"] if item["node_type"] == "step")
    assert step_item["element_id"] == el_id
    assert step_item["element_name"] == "按钮W"

    # 跳过一个用例后差异列表出现
    case_node = (await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={"expected_revision": 1, "operation": "skip", "reason": {"code": "unsupported"}, "targets": [{"type": "case", "suite_case_id": membership_id}]},
        headers=h,
    )).json()
    assert case_node["changed"] == 1
    diff = await client.get(f"/api/app-profiles/{profile_id}/differences?type=skipped", headers=h)
    assert diff.status_code == 200
    assert diff.json()["total"] >= 1
    assert diff.json()["items"][0]["target_type"] == "case"

    # 可执行总数不随状态筛选变化；直接跳过套件才从全档案选择基数移除。
    suite_skip = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={
            "expected_revision": case_node["revision_after"],
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "suite", "suite_id": suite_id}],
        },
        headers=h,
    )
    assert suite_skip.status_code == 200, suite_skip.text
    skipped_page = await client.get(
        f"/api/app-profiles/{profile_id}/workspace?effective_status=skipped",
        headers=h,
    )
    assert skipped_page.status_code == 200
    assert skipped_page.json()["total"] == 1
    assert skipped_page.json()["execution_selectable_total"] == 0
    enabled_page = await client.get(
        f"/api/app-profiles/{profile_id}/workspace?effective_status=enabled",
        headers=h,
    )
    assert enabled_page.status_code == 200
    assert enabled_page.json()["total"] == 0
    assert enabled_page.json()["execution_selectable_total"] == 0


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
    membership_id = (await client.post(f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h)).json()[0]["id"]

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
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=case&parent_id={case_id}&ancestor_suite_id={suite_id}&suite_case_id={membership_id}",
        headers=h,
    )
    node = nodes.json()["items"][0]
    assert node["effective_status"] == "skipped"
    assert node["status_source"] == "inherited"
    assert node["registry_key"] == "sleep"
    assert node["override_template"] == {"params": {"duration": 1}}
    assert "action" not in node["override_template"]
    assert "order" not in node["override_template"]


async def test_shared_case_skip_is_scoped_to_selected_suite(client: AsyncClient):
    """回归：同一用例属于多个套件时，只跳过工作台中选中的套件节点。"""
    token = await _register(client, OWNER)
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (
        await client.post("/api/projects", json={"name": "共享用例项目"}, headers=headers)
    ).json()["id"]
    profile_id = (
        await client.post(
            f"/api/projects/{project_id}/app-profiles",
            json={"name": "共享档案", "code": f"shared{uuid.uuid4().hex[:6]}"},
            headers=headers,
        )
    ).json()["id"]
    case_id = (
        await client.post(
            f"/api/projects/{project_id}/cases",
            json={
                "name": "公共登录用例",
                "steps": [
                    {
                        "order": 1,
                        "key": str(uuid.uuid4()),
                        "action": "sleep",
                        "params": {"duration": 1},
                    }
                ],
            },
            headers=headers,
        )
    ).json()["id"]
    suite_ids = []
    membership_ids = []
    for name in ("DVR 套件", "部标机套件"):
        suite_id = (
            await client.post(
                f"/api/projects/{project_id}/suites",
                json={"name": name},
                headers=headers,
            )
        ).json()["id"]
        membership_ids.append((await client.post(
            f"/api/suites/{suite_id}/cases",
            json={"case_id": case_id},
            headers=headers,
            )).json()[0]["id"])
        suite_ids.append(suite_id)

    skipped = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [
                {"type": "case", "suite_case_id": membership_ids[0]}
            ],
        },
        headers=headers,
    )
    assert skipped.status_code == 200, skipped.text

    first = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes"
        f"?parent_type=suite&parent_id={suite_ids[0]}",
        headers=headers,
    )
    second = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes"
        f"?parent_type=suite&parent_id={suite_ids[1]}",
        headers=headers,
    )
    assert first.json()["items"][0]["effective_status"] == "skipped"
    assert first.json()["items"][0]["status_source"] == "direct"
    assert second.json()["items"][0]["effective_status"] == "enabled"
    assert second.json()["items"][0]["status_source"] == "none"


async def test_case_skip_rejects_unrelated_suite_context(client: AsyncClient):
    """套件上下文必须真实包含目标用例，禁止伪造 suite_id。"""
    token = await _register(client, OWNER)
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (
        await client.post("/api/projects", json={"name": "关系校验项目"}, headers=headers)
    ).json()["id"]
    profile_id = (
        await client.post(
            f"/api/projects/{project_id}/app-profiles",
            json={"name": "关系档案", "code": f"relation{uuid.uuid4().hex[:6]}"},
            headers=headers,
        )
    ).json()["id"]
    case_id = (
        await client.post(
            f"/api/projects/{project_id}/cases",
            json={"name": "未入套件用例", "steps": []},
            headers=headers,
        )
    ).json()["id"]
    suite_id = (
        await client.post(
            f"/api/projects/{project_id}/suites",
            json={"name": "空套件"},
            headers=headers,
        )
    ).json()["id"]

    response = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "case", "suite_id": suite_id, "case_id": case_id}],
        },
        headers=headers,
    )

    assert response.status_code == 422


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
    membership_id = (await client.post(f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h)).json()[0]["id"]

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
        json={"expected_revision": 1, "operation": "skip", "reason": {"code": "unsupported"}, "targets": [{"type": "case", "suite_case_id": membership_id}]},
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


async def _seed_suite_steps(client: AsyncClient, h: dict, pid: int) -> tuple[int, str, str]:
    """建含前后置步骤的套件，返回 (suite_id, setup_key, teardown_key)。"""
    setup_key = str(uuid.uuid4())
    teardown_key = str(uuid.uuid4())
    suite_id = (
        await client.post(
            f"/api/projects/{pid}/suites",
            json={
                "name": "前后置套件",
                "setup_steps": [{"order": 1, "key": setup_key, "action": "sleep", "params": {"duration": 1}}],
                "teardown_steps": [{"order": 1, "key": teardown_key, "action": "sleep", "params": {"duration": 1}}],
            },
            headers=h,
        )
    ).json()["id"]
    return suite_id, setup_key, teardown_key


async def test_suite_steps_workspace_query(client: AsyncClient):
    """套件前后置步骤工作台：套件计数 + suite-steps 节点列表 + 跳过状态。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "前后置工作台"}, headers=h)).json()["id"]
    profile_id = (
        await client.post(
            f"/api/projects/{pid}/app-profiles",
            json={"name": "SS", "code": f"ss{uuid.uuid4().hex[:6]}"},
            headers=h,
        )
    ).json()["id"]
    suite_id, setup_key, teardown_key = await _seed_suite_steps(client, h, pid)

    ws = await client.get(f"/api/app-profiles/{profile_id}/workspace", headers=h)
    assert ws.status_code == 200
    suite_node = next(n for n in ws.json()["items"] if n["id"] == suite_id)
    assert suite_node["setup_step_count"] == 1
    assert suite_node["teardown_step_count"] == 1
    assert suite_node["has_children"] is True

    setup = await client.get(f"/api/app-profiles/{profile_id}/suite-steps/{suite_id}?phase=suite_setup", headers=h)
    assert setup.status_code == 200
    assert setup.json()["total"] == 1
    item = setup.json()["items"][0]
    assert item["node_type"] == "suite_step"
    assert item["id"] == suite_id
    assert item["node_key"] == setup_key
    assert item["phase"] == "suite_setup"
    assert item["effective_status"] == "enabled"
    assert item["status_source"] == "none"
    assert item["registry_key"] == "sleep"
    assert item["override_template"] == {"params": {"duration": 1}}
    assert "action" not in item["override_template"]

    teardown = await client.get(f"/api/app-profiles/{profile_id}/suite-steps/{suite_id}?phase=suite_teardown", headers=h)
    assert teardown.status_code == 200
    assert teardown.json()["items"][0]["phase"] == "suite_teardown"
    assert teardown.json()["items"][0]["node_key"] == teardown_key

    # 缺省 phase 返回全部（前后置各一条）
    all_phases = await client.get(f"/api/app-profiles/{profile_id}/suite-steps/{suite_id}", headers=h)
    assert all_phases.status_code == 200
    assert all_phases.json()["total"] == 2
    invalid_phase = await client.get(f"/api/app-profiles/{profile_id}/suite-steps/{suite_id}?phase=bogus", headers=h)
    assert invalid_phase.status_code == 422

    # 跳过前置步骤 → 状态变 skipped
    skip = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported", "note": "不支持"},
            "targets": [{"type": "suite_step", "suite_id": suite_id, "node_key": setup_key}],
        },
        headers=h,
    )
    assert skip.status_code == 200, skip.text
    assert skip.json()["changed"] == 1
    after = await client.get(f"/api/app-profiles/{profile_id}/suite-steps/{suite_id}?phase=suite_setup", headers=h)
    assert after.json()["items"][0]["effective_status"] == "skipped"
    assert after.json()["items"][0]["status_source"] == "direct"

    # 带 case_id 套件步骤目标 → 422
    bad = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={
            "expected_revision": 2,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "suite_step", "suite_id": suite_id, "case_id": 1, "node_key": teardown_key}],
        },
        headers=h,
    )
    assert bad.status_code == 422


async def test_workspace_step_element_name_uses_node_patch_and_missing_fallback(client: AsyncClient):
    """工作台批量返回步骤元素；普通与套件步骤均使用生效的 element_id。"""
    token = await _register(client, {"username": f"pytest_element_{uuid.uuid4().hex[:8]}", "email": f"element_{uuid.uuid4().hex[:8]}@tl-tek.com", "password": "test123"})
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "工作台元素"}, headers=h)).json()["id"]
    profile_id = (await client.post(
        f"/api/projects/{pid}/app-profiles", json={"name": "E", "code": f"e{uuid.uuid4().hex[:6]}"}, headers=h,
    )).json()["id"]
    first = (await client.post(
        f"/api/projects/{pid}/elements", json={"name": "初始元素", "locator_type": "id", "locator_value": "first"}, headers=h,
    )).json()["id"]
    second = (await client.post(
        f"/api/projects/{pid}/elements", json={"name": "覆盖元素", "locator_type": "id", "locator_value": "second"}, headers=h,
    )).json()["id"]
    node_key = str(uuid.uuid4())
    case_id = (await client.post(
        f"/api/projects/{pid}/cases", json={"name": "元素用例", "steps": [{
            "key": node_key, "order": 1, "action": "click", "element_id": first, "params": {},
        }]}, headers=h,
    )).json()["id"]
    suite_id = (await client.post(f"/api/projects/{pid}/suites", json={"name": "元素套件"}, headers=h)).json()["id"]
    membership_id = (await client.post(
        f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=h,
    )).json()[0]["id"]

    initial = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=case&parent_id={case_id}&ancestor_suite_id={suite_id}&suite_case_id={membership_id}", headers=h,
    )
    step = initial.json()["items"][0]
    assert (step["element_id"], step["element_name"]) == (first, "初始元素")

    patched = await client.put(
        f"/api/app-profiles/{profile_id}/node-overrides/{membership_id}/step/{node_key}",
        json={"expected_revision": 1, "patch": {"element_id": second}}, headers=h,
    )
    assert patched.status_code == 200, patched.text
    after = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=case&parent_id={case_id}&ancestor_suite_id={suite_id}&suite_case_id={membership_id}", headers=h,
    )
    step = after.json()["items"][0]
    assert (step["element_id"], step["element_name"]) == (second, "覆盖元素")

    missing = await client.put(
        f"/api/app-profiles/{profile_id}/node-overrides/{membership_id}/step/{node_key}",
        json={"expected_revision": 2, "patch": {"element_id": 999999999}}, headers=h,
    )
    assert missing.status_code == 200, missing.text
    unknown = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=case&parent_id={case_id}&ancestor_suite_id={suite_id}&suite_case_id={membership_id}", headers=h,
    )
    assert (unknown.json()["items"][0]["element_id"], unknown.json()["items"][0]["element_name"]) == (999999999, None)

    suite_step_key = str(uuid.uuid4())
    suite_with_step = (await client.post(
        f"/api/projects/{pid}/suites", json={"name": "元素前置", "setup_steps": [{
            "key": suite_step_key, "order": 1, "action": "click", "element_id": first, "params": {},
        }]}, headers=h,
    )).json()["id"]
    suite_initial = await client.get(
        f"/api/app-profiles/{profile_id}/suite-steps/{suite_with_step}?phase=suite_setup", headers=h,
    )
    assert (suite_initial.json()["items"][0]["element_id"], suite_initial.json()["items"][0]["element_name"]) == (first, "初始元素")
    suite_patch = await client.put(
        f"/api/app-profiles/{profile_id}/suite-step-overrides/{suite_with_step}/{suite_step_key}",
        json={"expected_revision": 3, "patch": {"element_id": second}}, headers=h,
    )
    assert suite_patch.status_code == 200, suite_patch.text
    suite_after = await client.get(
        f"/api/app-profiles/{profile_id}/suite-steps/{suite_with_step}?phase=suite_setup", headers=h,
    )
    assert (suite_after.json()["items"][0]["element_id"], suite_after.json()["items"][0]["element_name"]) == (second, "覆盖元素")


async def test_suite_step_override_roundtrip(client: AsyncClient):
    """套件步骤覆盖 upsert + restore + 非法 patch 拒绝。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "前后置覆盖"}, headers=h)).json()["id"]
    profile_id = (
        await client.post(
            f"/api/projects/{pid}/app-profiles",
            json={"name": "SO", "code": f"so{uuid.uuid4().hex[:6]}"},
            headers=h,
        )
    ).json()["id"]
    suite_id, setup_key, _teardown_key = await _seed_suite_steps(client, h, pid)

    request_id = str(uuid.uuid4())
    body = {"request_id": request_id, "expected_revision": 1, "patch": {"params": {"duration": 5}}}
    r = await client.put(f"/api/app-profiles/{profile_id}/suite-step-overrides/{suite_id}/{setup_key}", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 2
    assert r.json()["node_type"] == "suite_step"
    replay = await client.put(f"/api/app-profiles/{profile_id}/suite-step-overrides/{suite_id}/{setup_key}", json=body, headers=h)
    assert replay.status_code == 200
    assert replay.json() == r.json()

    listed = await client.get(f"/api/app-profiles/{profile_id}/suite-steps/{suite_id}?phase=suite_setup", headers=h)
    assert listed.json()["items"][0]["effective_status"] == "overridden"
    assert listed.json()["items"][0]["override_count"] == 1

    # 非法 patch（改 order）→ 422，revision 不变
    bad = await client.put(
        f"/api/app-profiles/{profile_id}/suite-step-overrides/{suite_id}/{setup_key}",
        json={"expected_revision": 2, "patch": {"order": 5}},
        headers=h,
    )
    assert bad.status_code == 422
    missing = await client.put(
        f"/api/app-profiles/{profile_id}/suite-step-overrides/{suite_id}/{uuid.uuid4()}",
        json={"expected_revision": 2, "patch": {"params": {"duration": 10}}},
        headers=h,
    )
    assert missing.status_code == 422

    # restore → 状态恢复 enabled
    restored = await client.request(
        "DELETE",
        f"/api/app-profiles/{profile_id}/suite-step-overrides/{suite_id}/{setup_key}",
        json={"expected_revision": 2},
        headers=h,
    )
    assert restored.status_code == 204
    after = await client.get(f"/api/app-profiles/{profile_id}/suite-steps/{suite_id}?phase=suite_setup", headers=h)
    assert after.json()["items"][0]["effective_status"] == "enabled"
    assert after.json()["items"][0]["override_count"] == 0


async def test_differences_suite_step(client: AsyncClient):
    """差异清单包含套件步骤的跳过（前置）与覆盖（后置）行。"""
    token = await _register(client, OWNER)
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post("/api/projects", json={"name": "前后置差异"}, headers=h)).json()["id"]
    profile_id = (
        await client.post(
            f"/api/projects/{pid}/app-profiles",
            json={"name": "SD", "code": f"sd{uuid.uuid4().hex[:6]}"},
            headers=h,
        )
    ).json()["id"]
    suite_id, setup_key, teardown_key = await _seed_suite_steps(client, h, pid)

    skip = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "suite_step", "suite_id": suite_id, "node_key": setup_key}],
        },
        headers=h,
    )
    assert skip.status_code == 200
    override = await client.put(
        f"/api/app-profiles/{profile_id}/suite-step-overrides/{suite_id}/{teardown_key}",
        json={"expected_revision": 2, "patch": {"params": {"duration": 5}}},
        headers=h,
    )
    assert override.status_code == 200

    skipped = await client.get(f"/api/app-profiles/{profile_id}/differences?type=skipped", headers=h)
    assert skipped.status_code == 200
    skip_rows = [r for r in skipped.json()["items"] if r["target_type"] == "suite_step"]
    assert len(skip_rows) == 1
    assert "前置" in skip_rows[0]["path"]

    overridden = await client.get(f"/api/app-profiles/{profile_id}/differences?type=overridden", headers=h)
    assert overridden.status_code == 200
    ov_rows = [r for r in overridden.json()["items"] if r["target_type"] == "suite_step"]
    assert len(ov_rows) == 1
    assert "后置" in ov_rows[0]["path"]

    all_rows = await client.get(f"/api/app-profiles/{profile_id}/differences?type=all&target_type=suite_step", headers=h)
    assert all_rows.status_code == 200
    assert all_rows.json()["total"] == 2
