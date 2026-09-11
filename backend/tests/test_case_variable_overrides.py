"""套件与 APP 档案用例变量快捷展示及覆盖（方案：编排项覆盖 + occurrence 隔离）。

覆盖：
- 动作/断言参数递归引用提取（去重、首次顺序）
- 套件用例列表摘要（最多 2 项）与变量详情
- 同一用例两个编排项独立覆盖，快照互不影响
- 执行参数最高优先级
- 断言变量覆盖生效
- 空串覆盖与恢复继承
- 随机变量只展示规则、不提前生成
- APP 档案重复编排节点覆盖互不污染
- revision 冲突 / 权限 / 无效变量 / 批量事务回滚
- 删除编排项级联清理节点覆盖
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import (
    AppProfile,
    AppProfileNodeOverride,
    AppProfileRelease,
    Project,
    TestSuiteCase,
    User,
)
from app.services.case_variable_service import (
    apply_membership_updates,
    case_variable_reference_counts,
    validate_membership_updates,
)
from app.services.profile_resolver import ResolutionRequest, resolve
from app.services.profile_resolver_nodes import ProfileRuleError, variable_references

OWNER = {"username": "pytest_casevars", "email": "casevars@tl-tek.com", "password": "test123"}
MEMBER = {"username": "pytest_casevars_m", "email": "casevars_m@tl-tek.com", "password": "test123"}

K1 = str(uuid.uuid4())
K2 = str(uuid.uuid4())
K3 = str(uuid.uuid4())
A1 = str(uuid.uuid4())


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(client: AsyncClient, user: dict) -> dict:
    await client.post("/api/auth/register", json=user)
    token = (
        await client.post(
            "/api/auth/login", json={"username": user["username"], "password": "test123"}
        )
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _project_with_case(client: AsyncClient, headers: dict) -> dict:
    """建项目 + 元素 + 含 3 变量动作与 1 变量断言的用例。"""
    project_id = (
        await client.post("/api/projects", json={"name": "变量覆盖项目"}, headers=headers)
    ).json()["id"]
    element_id = (
        await client.post(
            f"/api/projects/{project_id}/elements",
            json={"name": "串口输入框", "locator_type": "id", "locator_value": "com_port"},
            headers=headers,
        )
    ).json()["id"]
    case_id = (
        await client.post(
            f"/api/projects/{project_id}/cases",
            json={
                "name": "串口配置",
                "steps": [
                    {
                        "key": K1,
                        "order": 1,
                        "action": "input",
                        "element_id": element_id,
                        "params": {"value": "${port_name}", "clear_first": True},
                        "assertions": [
                            {
                                "key": A1,
                                "order": 1,
                                "type": "text_contains",
                                "element_id": element_id,
                                "params": {"expected": "${expected_port}"},
                            }
                        ],
                    },
                    {
                        "key": K2,
                        "order": 2,
                        "action": "input",
                        "element_id": element_id,
                        "params": {"value": "${port_name}-${baud_rate}", "clear_first": False},
                    },
                    {
                        "key": K3,
                        "order": 3,
                        "action": "screenshot",
                        "params": {"filename": "${shot_name}"},
                    },
                ],
            },
            headers=headers,
        )
    ).json()["id"]
    return {"project_id": project_id, "element_id": element_id, "case_id": case_id}


async def _suite_with_memberships(
    client: AsyncClient, headers: dict, project_id: int, case_id: int, times: int = 1
) -> tuple[int, list[int]]:
    suite_id = (
        await client.post(
            f"/api/projects/{project_id}/suites", json={"name": "串口套件"}, headers=headers
        )
    ).json()["id"]
    for _ in range(times):
        await client.post(
            f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=headers
        )
    rows = (await client.get(f"/api/suites/{suite_id}/cases", headers=headers)).json()
    return suite_id, [row["id"] for row in rows]


async def _make_profile(db, project_id: int) -> tuple[int, int]:
    profile = AppProfile(project_id=project_id, name="变量档案", code=f"v{uuid.uuid4().hex[:6]}")
    db.add(profile)
    await db.flush()
    release = AppProfileRelease(profile_id=profile.id, version="1.0")
    db.add(release)
    await db.flush()
    return profile.id, release.id


async def _seed_variables(client: AsyncClient, headers: dict, project_id: int, names: list[str]) -> None:
    """给未被覆盖的引用变量提供项目级定义，避免解析时未定义变量报错。"""
    for name in names:
        response = await client.post(
            "/api/variables",
            json={"scope": "project", "project_id": project_id, "name": name, "value": f"base_{name}"},
            headers=headers,
        )
        assert response.status_code == 201


# ---------- 引用解析（前后端统一口径） ----------


def test_references_include_actions_and_assertions_in_first_order():
    from app.models import TestCase as CaseModel

    case = CaseModel(
        project_id=1,
        name="x",
        flow_nodes=[
            {"key": K1, "kind": "action", "action": "input", "params": {"value": "${a}/${b}"}},
            {
                "key": A1,
                "kind": "assertion",
                "type": "text_contains",
                "params": {"expected": "${b}/${c}", "nested": ["${d}"]},
            },
            {"key": K3, "kind": "action", "action": "screenshot", "params": {"filename": "${a}"}},
        ],
    )
    assert case_variable_reference_counts(case) == {"a": 2, "b": 2, "c": 1, "d": 1}
    assert variable_references(["${a}", {"k": "${b}"}]) == ["a", "b"]


def test_membership_update_validation_and_apply():
    from app.models import TestCase as CaseModel

    case = CaseModel(
        project_id=1,
        name="x",
        flow_nodes=[
            {"key": K1, "kind": "action", "action": "input", "params": {"value": "${port_name}"}}
        ],
    )
    with pytest.raises(ProfileRuleError):
        validate_membership_updates(case, {"unused": "x"})
    with pytest.raises(ProfileRuleError):
        validate_membership_updates(case, {"port_name": 1})
    assert validate_membership_updates(case, {"port_name": ""}) == {"port_name": ""}
    # null 删除覆盖并恢复继承；空串是合法覆盖值
    assert apply_membership_updates({"a": "1", "port_name": "COM1"}, {"port_name": None}) == {"a": "1"}
    assert apply_membership_updates({}, {"port_name": ""}) == {"port_name": ""}


# ---------- 套件用例列表与变量详情 ----------


async def test_suite_case_list_preview_and_variables_detail(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )

    rows = (await client.get(f"/api/suites/{suite_id}/cases", headers=headers)).json()
    assert len(rows) == 1
    assert rows[0]["variable_count"] == 4
    assert len(rows[0]["variables_preview"]) == 2
    assert [item["name"] for item in rows[0]["variables_preview"]] == ["port_name", "expected_port"]
    assert all(item["status"] == "undefined" for item in rows[0]["variables_preview"])

    detail = await client.get(
        f"/api/suites/{suite_id}/cases/{memberships[0]}/variables", headers=headers
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["total"] == 4
    assert [item["name"] for item in body["variables"]] == [
        "port_name", "expected_port", "baud_rate", "shot_name",
    ]
    assert body["variables"][0]["reference_count"] == 2
    assert body["variables"][0]["status"] == "undefined"
    assert body["test_asset_revision"] >= 1


async def test_membership_override_isolated_between_occurrences(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"], times=2
    )
    assert len(memberships) == 2

    first, second = memberships
    r1 = await client.patch(
        f"/api/suites/{suite_id}/cases/{first}/variable-overrides",
        json={"updates": {"port_name": "COM1"}},
        headers=headers,
    )
    assert r1.status_code == 200
    r2 = await client.patch(
        f"/api/suites/{suite_id}/cases/{second}/variable-overrides",
        json={"updates": {"port_name": "COM2"}},
        headers=headers,
    )
    assert r2.status_code == 200

    d1 = (
        await client.get(f"/api/suites/{suite_id}/cases/{first}/variables", headers=headers)
    ).json()
    d2 = (
        await client.get(f"/api/suites/{suite_id}/cases/{second}/variables", headers=headers)
    ).json()
    assert d1["variables"][0]["override_value"] == "COM1"
    assert d2["variables"][0]["override_value"] == "COM2"

    rows = (await client.get(f"/api/suites/{suite_id}/cases", headers=headers)).json()
    assert [row["variables_preview"][0]["display_value"] for row in rows] == ["COM1", "COM2"]
    assert all(row["variables_preview"][0]["status"] == "overridden" for row in rows)

    # 快照：同一用例两个编排项取到不同值，互不影响
    await _seed_variables(client, headers, ctx["project_id"], ["expected_port", "baud_rate", "shot_name"])
    async with SessionLocal() as db:
        profile_id, release_id = await _make_profile(db, ctx["project_id"])
        project = await db.get(Project, ctx["project_id"])
        result = await resolve(
            ResolutionRequest(
                project_id=ctx["project_id"],
                profile_id=profile_id,
                release_id=release_id,
                target_type="suite",
                target_ids=[suite_id],
                expected_profile_revision=1,
                expected_test_asset_revision=project.test_asset_revision if project else 1,
            ),
            db,
        )
    values = [case.flow_snapshot[0]["params"]["value"] for case in result.suites[0].cases]
    assert values == ["COM1", "COM2"]


async def test_membership_override_removed_restores_inheritance(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )
    membership = memberships[0]
    await client.post(
        "/api/variables",
        json={
            "scope": "project", "project_id": ctx["project_id"],
            "name": "port_name", "value": "INHERITED",
        },
        headers=headers,
    )
    patched = await client.patch(
        f"/api/suites/{suite_id}/cases/{membership}/variable-overrides",
        json={"updates": {"port_name": "COM9"}},
        headers=headers,
    )
    assert patched.json()["variables"][0]["display_value"] == "COM9"

    # 覆盖为空串也是合法覆盖值
    empty = await client.patch(
        f"/api/suites/{suite_id}/cases/{membership}/variable-overrides",
        json={"updates": {"port_name": ""}},
        headers=headers,
    )
    assert empty.json()["variables"][0]["status"] == "overridden"
    assert empty.json()["variables"][0]["display_value"] == ""

    restored = await client.patch(
        f"/api/suites/{suite_id}/cases/{membership}/variable-overrides",
        json={"updates": {"port_name": None}},
        headers=headers,
    )
    assert restored.json()["variables"][0]["status"] == "inherited"
    assert restored.json()["variables"][0]["display_value"] == "INHERITED"


async def test_membership_override_rejects_unknown_variable(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )
    response = await client.patch(
        f"/api/suites/{suite_id}/cases/{memberships[0]}/variable-overrides",
        json={"updates": {"not_referenced": "x"}},
        headers=headers,
    )
    assert response.status_code == 422


async def test_random_variable_preview_shows_rule_without_generating(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )
    created = await client.post(
        "/api/variables",
        json={
            "scope": "project", "project_id": ctx["project_id"], "name": "port_name",
            "value": "", "kind": "random_integer", "spec": {"min": 1, "max": 10},
        },
        headers=headers,
    )
    assert created.status_code == 201

    detail = (
        await client.get(f"/api/suites/{suite_id}/cases/{memberships[0]}/variables", headers=headers)
    ).json()
    first = detail["variables"][0]
    assert first["status"] == "random"
    assert first["display_value"] == "随机 1～10"

    # 开启编排项覆盖后转为固定值
    patched = await client.patch(
        f"/api/suites/{suite_id}/cases/{memberships[0]}/variable-overrides",
        json={"updates": {"port_name": "COM7"}},
        headers=headers,
    )
    assert patched.json()["variables"][0]["status"] == "overridden"
    assert patched.json()["variables"][0]["display_value"] == "COM7"


async def test_assertion_variable_override_applies_to_snapshot(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )
    await _seed_variables(client, headers, ctx["project_id"], ["port_name", "baud_rate", "shot_name"])
    async with SessionLocal() as db:
        profile_id, release_id = await _make_profile(db, ctx["project_id"])
        membership = await db.get(TestSuiteCase, memberships[0])
        assert membership is not None
        db.add(
            AppProfileNodeOverride(
                profile_id=profile_id,
                target_type="assertion",
                suite_id=suite_id,
                case_id=ctx["case_id"],
                suite_case_id=membership.id,
                node_key=A1,
                patch={"variable_overrides": {"expected_port": "ASSERT_VALUE"}},
            )
        )
        await db.commit()
        project = await db.get(Project, ctx["project_id"])
        result = await resolve(
            ResolutionRequest(
                project_id=ctx["project_id"], profile_id=profile_id, release_id=release_id,
                target_type="suite", target_ids=[suite_id],
                expected_profile_revision=1,
                expected_test_asset_revision=project.test_asset_revision if project else 1,
            ),
            db,
        )
    assertions = result.suites[0].cases[0].assertions_snapshot
    assert assertions[0]["params"]["expected"] == "ASSERT_VALUE"


async def test_execution_parameters_win_over_membership_override(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )
    await client.patch(
        f"/api/suites/{suite_id}/cases/{memberships[0]}/variable-overrides",
        json={"updates": {"port_name": "FROM_OVERRIDE"}},
        headers=headers,
    )
    await _seed_variables(client, headers, ctx["project_id"], ["expected_port", "baud_rate", "shot_name"])
    async with SessionLocal() as db:
        profile_id, release_id = await _make_profile(db, ctx["project_id"])
        project = await db.get(Project, ctx["project_id"])
        result = await resolve(
            ResolutionRequest(
                project_id=ctx["project_id"], profile_id=profile_id, release_id=release_id,
                target_type="suite", target_ids=[suite_id],
                expected_profile_revision=1,
                expected_test_asset_revision=project.test_asset_revision if project else 1,
                execution_variables={"port_name": "FROM_EXECUTION"},
            ),
            db,
        )
    assert result.suites[0].cases[0].flow_snapshot[0]["params"]["value"] == "FROM_EXECUTION"


# ---------- APP 档案节点级覆盖 ----------


async def _patch_profile_variables(
    client, headers, profile_id, suite_case_id, updates, revision=1, request_id=None
):
    body = {"expected_revision": revision, "updates": updates}
    if request_id:
        body["request_id"] = request_id
    return await client.patch(
        f"/api/app-profiles/{profile_id}/suite-cases/{suite_case_id}/variable-overrides",
        json=body,
        headers=headers,
    )


async def test_profile_duplicate_occurrences_do_not_pollute_each_other(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"], times=2
    )
    async with SessionLocal() as db:
        profile_id, _release_id = await _make_profile(db, ctx["project_id"])
        await db.commit()

    first, second = memberships
    r1 = await _patch_profile_variables(
        client, headers, profile_id, first,
        [{"node_type": "step", "node_key": K1, "name": "port_name", "value": "P1"}],
    )
    assert r1.status_code == 200
    assert r1.json()["profile_revision"] == 2

    r2 = await _patch_profile_variables(
        client, headers, profile_id, second,
        [{"node_type": "step", "node_key": K1, "name": "port_name", "value": "P2"}],
        revision=2,
    )
    assert r2.status_code == 200

    d1 = (
        await client.get(
            f"/api/app-profiles/{profile_id}/suite-cases/{first}/variables", headers=headers
        )
    ).json()
    d2 = (
        await client.get(
            f"/api/app-profiles/{profile_id}/suite-cases/{second}/variables", headers=headers
        )
    ).json()
    assert d1["variables"][0]["references"][0]["override_value"] == "P1"
    assert d2["variables"][0]["references"][0]["override_value"] == "P2"


async def test_profile_variable_batch_revision_conflict_and_invalid_rollback(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )
    async with SessionLocal() as db:
        profile_id, _release_id = await _make_profile(db, ctx["project_id"])
        await db.commit()
    membership = memberships[0]

    ok = await _patch_profile_variables(
        client, headers, profile_id, membership,
        [{"node_type": "step", "node_key": K1, "name": "port_name", "value": "A"}],
    )
    assert ok.status_code == 200

    conflict = await _patch_profile_variables(
        client, headers, profile_id, membership,
        [{"node_type": "step", "node_key": K1, "name": "port_name", "value": "B"}],
        revision=1,
    )
    assert conflict.status_code == 409

    # 第二条更新无效（变量未被该节点引用）→ 整体回滚，第一条不落库
    invalid = await _patch_profile_variables(
        client, headers, profile_id, membership,
        [
            {"node_type": "step", "node_key": K2, "name": "baud_rate", "value": "9600"},
            {"node_type": "step", "node_key": K2, "name": "shot_name", "value": "x"},
        ],
        revision=2,
    )
    assert invalid.status_code == 422

    detail = (
        await client.get(
            f"/api/app-profiles/{profile_id}/suite-cases/{membership}/variables", headers=headers
        )
    ).json()
    baud = next(item for item in detail["variables"] if item["name"] == "baud_rate")
    assert baud["references"][0]["override_enabled"] is False
    revisions = (await client.get(f"/api/app-profiles/{profile_id}", headers=headers)).json()
    assert revisions["revision"] == 2


async def test_profile_variable_batch_same_value_and_mixed_status(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )
    async with SessionLocal() as db:
        profile_id, _release_id = await _make_profile(db, ctx["project_id"])
        await db.commit()
    membership = memberships[0]

    # port_name 被 K1/K2 两个节点引用：先给不同值 → mixed
    response = await _patch_profile_variables(
        client, headers, profile_id, membership,
        [
            {"node_type": "step", "node_key": K1, "name": "port_name", "value": "A"},
            {"node_type": "step", "node_key": K2, "name": "port_name", "value": "B"},
        ],
    )
    assert response.status_code == 200
    port = next(item for item in response.json()["variables"] if item["name"] == "port_name")
    assert port["status"] == "mixed"
    assert port["reference_count"] == 2

    # 全部设为同一值 → overridden
    same = await _patch_profile_variables(
        client, headers, profile_id, membership,
        [
            {"node_type": "step", "node_key": K1, "name": "port_name", "value": "SAME"},
            {"node_type": "step", "node_key": K2, "name": "port_name", "value": "SAME"},
        ],
        revision=2,
    )
    port_same = next(item for item in same.json()["variables"] if item["name"] == "port_name")
    assert port_same["status"] == "overridden"

    # null 删除该节点覆盖，另一节点保留
    removed = await _patch_profile_variables(
        client, headers, profile_id, membership,
        [{"node_type": "step", "node_key": K1, "name": "port_name", "value": None}],
        revision=3,
    )
    refs = next(item for item in removed.json()["variables"] if item["name"] == "port_name")["references"]
    by_key = {ref["node_key"]: ref for ref in refs}
    assert by_key[K1]["override_enabled"] is False
    assert by_key[K2]["override_enabled"] is True


async def test_profile_variable_override_requires_owner_or_admin(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    _suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )
    async with SessionLocal() as db:
        profile_id, _release_id = await _make_profile(db, ctx["project_id"])
        await db.commit()

    member_headers = await _login(client, MEMBER)
    async with SessionLocal() as db:
        member = (
            await db.execute(select(User).where(User.username == MEMBER["username"]))
        ).scalar_one()
        member_id = member.id
    await client.post(
        f"/api/projects/{ctx['project_id']}/members",
        json={"user_id": member_id, "role": "member"},
        headers=headers,
    )
    # 读取允许项目成员
    readable = await client.get(
        f"/api/app-profiles/{profile_id}/suite-cases/{memberships[0]}/variables",
        headers=member_headers,
    )
    assert readable.status_code == 200
    # 写入维持 Owner/Admin
    forbidden = await _patch_profile_variables(
        client, member_headers, profile_id, memberships[0],
        [{"node_type": "step", "node_key": K1, "name": "port_name", "value": "X"}],
    )
    assert forbidden.status_code == 403


async def test_removing_membership_cascades_node_overrides(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"]
    )
    async with SessionLocal() as db:
        profile_id, _release_id = await _make_profile(db, ctx["project_id"])
        await db.commit()
    membership = memberships[0]
    saved = await _patch_profile_variables(
        client, headers, profile_id, membership,
        [{"node_type": "step", "node_key": K1, "name": "port_name", "value": "A"}],
    )
    assert saved.status_code == 200

    removed = await client.delete(
        f"/api/suites/{suite_id}/cases/{membership}", headers=headers
    )
    assert removed.status_code == 204

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(AppProfileNodeOverride).where(
                    AppProfileNodeOverride.suite_case_id == membership
                )
            )
        ).scalars().all()
    assert rows == [] or all(row.deleted_at is not None for row in rows)


async def test_workspace_case_rows_expose_suite_case_id(client: AsyncClient):
    headers = await _login(client, OWNER)
    ctx = await _project_with_case(client, headers)
    suite_id, memberships = await _suite_with_memberships(
        client, headers, ctx["project_id"], ctx["case_id"], times=2
    )
    async with SessionLocal() as db:
        profile_id, _release_id = await _make_profile(db, ctx["project_id"])
        await db.commit()
    rows = (
        await client.get(
            f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=suite&parent_id={suite_id}",
            headers=headers,
        )
    ).json()["items"]
    assert [row["suite_case_id"] for row in rows] == memberships
    assert all(row["variable_count"] == 4 for row in rows)

    # 按 suite_case_id 展开子节点：两个编排项各自独立
    nodes = (
        await client.get(
            f"/api/app-profiles/{profile_id}/workspace/nodes"
            f"?parent_type=case&parent_id={ctx['case_id']}&ancestor_suite_id={suite_id}"
            f"&suite_case_id={memberships[1]}",
            headers=headers,
        )
    ).json()["items"]
    assert any(node["node_key"] == K1 for node in nodes)
