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
