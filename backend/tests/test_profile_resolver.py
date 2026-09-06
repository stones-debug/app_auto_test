"""Step B4：ProfileResolver 解析引擎行为验证（方案 §11.1）。

基础设施：直接以 ORM 造档案/规则/覆盖 + 公共用例，调用 ProfileResolver.preview。
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import (
    AppProfile,
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileRelease,
    AppProfileSkipRule,
    AppProfileVariableOverride,
    Execution,
    Project,
    Variable,
)
from app.models import TestSuite as SuiteModel
from app.models import TestSuiteCase as SuiteCaseModel
from app.services.profile_resolver import (
    ProfileEmpty,
    ProfileRevisionConflict,
    ProfileRuleError,
    ResolutionRequest,
    _select_steps_for_run,
    validate_node_patch,
    variable_references,
)

OWNER = {"username": "pytest_resolver", "email": "resolver@tl-tek.com", "password": "test123"}
K1 = str(uuid.uuid4())
K2 = str(uuid.uuid4())
K3 = str(uuid.uuid4())


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _base(client: AsyncClient) -> dict:
    """注册用户，返回 token/headers/project_id。"""
    await client.post("/api/auth/register", json=OWNER)
    token = (await client.post("/api/auth/login", json={"username": OWNER["username"], "password": "test123"})).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (await client.post("/api/projects", json={"name": "解析器项目"}, headers=headers)).json()["id"]
    return {"headers": headers, "project_id": project_id}


async def _setup_case_with_steps(client: AsyncClient, base: dict, name: str) -> int:
    """建一个含步骤/断言/变量的用例，返回 case_id。

    K1(setup launch_app package=${pkg}) / K2(main click) / K3(断言 element_exists)。
    """
    el = await client.post(
        f"/api/projects/{base['project_id']}/elements",
        headers=base["headers"],
        json={"name": "目标按钮", "locator_type": "id", "locator_value": "btn_go"},
    )
    assert el.status_code == 201
    element_id = el.json()["id"]
    resp = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={
            "name": name,
            "steps": [
                {"key": K1, "order": 1, "phase": "setup", "action": "launch_app", "params": {"package": "${pkg}"}},
                {
                    "key": K2,
                    "order": 2,
                    "phase": "main",
                    "action": "click",
                    "element_id": element_id,
                    "params": {"wait_timeout": 5},
                    # 断言下沉到步骤内（StepCreate.assertions），用例级 assertions 字段已不存在
                    "assertions": [
                        {"key": K3, "order": 1, "type": "element_exists", "element_id": element_id, "params": {}}
                    ],
                },
            ],
            "variables": {"pkg": "com.v"},
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _make_profile(db, base: dict) -> int:
    profile = AppProfile(project_id=base["project_id"], name="DVR", code="dvr")
    db.add(profile)
    await db.flush()
    db.add(AppProfileRelease(profile_id=profile.id, version="1.0"))
    await db.commit()
    return profile.id


async def _asset_revision(db, project_id: int) -> int:
    return int(await db.scalar(select(Project.test_asset_revision).where(Project.id == project_id)))


async def _release_id(db, profile_id: int) -> int:
    return int(
        await db.scalar(
            select(AppProfileRelease.id).where(AppProfileRelease.profile_id == profile_id)
        )
    )


async def _attach_case_to_suite(db, project_id: int, case_id: int, name: str) -> int:
    suite = SuiteModel(project_id=project_id, name=name)
    db.add(suite)
    await db.flush()
    db.add(SuiteCaseModel(suite_id=suite.id, case_id=case_id, sort_order=1))
    await db.flush()
    return suite.id


async def test_run_options_select_and_order_phases(client):
    """默认只执行 main；勾选前置后按 setup → main 连续编号。"""
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "无规则用例")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        asset_revision = await _asset_revision(db, base["project_id"])
        default_result = await resolve_compat(ResolutionRequest(
            project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
            target_type="case", target_ids=[case_id],
            expected_profile_revision=1, expected_test_asset_revision=asset_revision,
        ), db)
        result = await resolve_compat(ResolutionRequest(
            project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
            target_type="case", target_ids=[case_id],
            expected_profile_revision=1, expected_test_asset_revision=asset_revision,
            run_options={"use_pre_steps": True},
        ), db)
    assert [s["source_key"] for s in default_result.cases[0].steps_snapshot] == [K2]
    c = result.cases[0]
    assert len(c.steps_snapshot) == 2
    assert len(c.assertions_snapshot) == 1
    assert c.steps_snapshot[0]["source_key"] == K1
    assert c.steps_snapshot[1]["source_key"] == K2
    assert c.steps_snapshot[0]["order"] == 1
    assert c.steps_snapshot[1]["order"] == 2


async def test_case_skip_excluded(client):
    """用例级跳过：整用例进入 exclusions，不生成快照。"""
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "被跳用例")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        suite_id = await _attach_case_to_suite(
            db, base["project_id"], case_id, "被跳套件"
        )
        db.add(AppProfileSkipRule(profile_id=profile_id, target_type="case", suite_id=suite_id, case_id=case_id, reason_code="unsupported"))
        await db.commit()
        request = ResolutionRequest(
            project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
            target_type="suite", target_ids=[suite_id],
            expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
        )
        with pytest.raises(ProfileEmpty):
            await resolve_compat(request, db)


async def test_step_skip_and_override(client):
    """步骤跳过 + 节点参数覆盖仅作用于当前套件，共享用例的其他套件不受影响。"""
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "步骤跳过用例")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        suite_id = await _attach_case_to_suite(
            db, base["project_id"], case_id, "步骤跳过套件"
        )
        other_suite_id = await _attach_case_to_suite(
            db, base["project_id"], case_id, "共享用例的另一套件"
        )
        db.add(AppProfileSkipRule(profile_id=profile_id, target_type="step", suite_id=suite_id, case_id=case_id, node_key=K2, reason_code="unsupported", reason_note="n"))
        # 覆盖 K1(setup launch_app) 的 params.package
        db.add(AppProfileNodeOverride(profile_id=profile_id, target_type="step", suite_id=suite_id, case_id=case_id, node_key=K1, patch={"params": {"package": "patched_pkg"}}))
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
                target_type="suite", target_ids=[suite_id, other_suite_id],
                expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                run_options={"use_pre_steps": True},
            ),
            db,
        )
    assert len(result.suites) == 2
    steps = result.suites[0].cases[0].steps_snapshot
    assert len(steps) == 1  # K2 被跳过
    assert steps[0]["source_key"] == K1
    assert steps[0]["params"]["package"] == "patched_pkg"
    other_steps = result.suites[1].cases[0].steps_snapshot
    assert [step["source_key"] for step in other_steps] == [K1, K2]
    assert other_steps[0]["params"]["package"] == "com.v"


async def test_suite_step_override_applied_to_snapshot(client):
    """套件步骤覆盖（target_type='suite_step'）必须加载并作用到套件前置步快照。

    回归：解析器曾用 target_type == 'step' and case_id is None 判断套件步覆盖，
    而 DB 保存的类型是 suite_step，导致 suite_step_overrides 永远为空，
    覆盖的 params 不生效（现有测试仅覆盖保存/查询/恢复，未验证解析快照）。
    """
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "套件步覆盖用例")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        suite_setup_key = str(uuid.uuid4())
        suite = SuiteModel(
            project_id=base["project_id"],
            name="套件步覆盖套件",
            setup_steps=[{"order": 1, "key": suite_setup_key, "action": "sleep", "params": {"duration": 1}}],
            teardown_steps=[],
        )
        db.add(suite)
        await db.flush()
        db.add(SuiteCaseModel(suite_id=suite.id, case_id=case_id, sort_order=1))
        await db.flush()
        # 保存的口径与 API 一致：target_type='suite_step'（case_id 恒空）
        db.add(
            AppProfileNodeOverride(
                profile_id=profile_id,
                target_type="suite_step",
                suite_id=suite.id,
                case_id=None,
                node_key=suite_setup_key,
                patch={"params": {"duration": 5}},
            )
        )
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
                target_type="suite", target_ids=[suite.id],
                expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                run_options={"use_pre_steps": True},
            ),
            db,
        )
    assert len(result.suites) == 1
    assert len(result.suites[0].setup_steps_snapshot) == 1
    assert result.suites[0].setup_steps_snapshot[0]["source_key"] == suite_setup_key
    # 覆盖必须生效（此前 suite_step_overrides 为空时保持 duration=1）
    assert result.suites[0].setup_steps_snapshot[0]["params"]["duration"] == 5


async def test_suite_step_variable_override_is_rendered_per_step(client):
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "套件变量步骤")
    suite_setup_key = str(uuid.uuid4())
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        suite = SuiteModel(
            project_id=base["project_id"], name="套件变量覆盖",
            setup_steps=[{"order": 1, "key": suite_setup_key, "action": "launch_app", "params": {"package": "${pkg}"}}],
            teardown_steps=[],
        )
        db.add(suite)
        await db.flush()
        db.add(SuiteCaseModel(suite_id=suite.id, case_id=case_id, sort_order=1))
        db.add(AppProfileNodeOverride(
            profile_id=profile_id, target_type="suite_step", suite_id=suite.id, case_id=None,
            node_key=suite_setup_key, patch={"variable_overrides": {"pkg": "from_suite_step"}},
        ))
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id,
                release_id=await _release_id(db, profile_id), target_type="suite", target_ids=[suite.id],
                expected_profile_revision=1,
                expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                run_options={"use_pre_steps": True},
            ), db,
        )
    setup = result.suites[0].setup_steps_snapshot[0]
    assert setup["params"]["package"] == "from_suite_step"
    assert "variable_overrides" not in setup


async def test_variable_override_priority(client):
    """变量优先级：执行参数 > APP档案 > 套件 > 用例。"""
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "变量用例")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        db.add(AppProfileVariableOverride(profile_id=profile_id, name="pkg", value="from_profile"))
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
                target_type="case", target_ids=[case_id],
                expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                run_options={"use_pre_steps": True},
                execution_variables={"pkg": "from_exec"},
            ),
            db,
        )
    step0 = result.cases[0].steps_snapshot[0]
    assert step0["params"]["package"] == "from_exec"


async def test_step_variable_override_is_local_and_not_in_snapshot(client):
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "步骤变量覆盖")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        suite_id = await _attach_case_to_suite(db, base["project_id"], case_id, "步骤变量套件")
        db.add(
            AppProfileNodeOverride(
                profile_id=profile_id,
                target_type="step",
                suite_id=suite_id,
                case_id=case_id,
                node_key=K1,
                patch={"variable_overrides": {"pkg": "from_step"}},
            )
        )
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id,
                release_id=await _release_id(db, profile_id), target_type="suite", target_ids=[suite_id],
                expected_profile_revision=1,
                expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                run_options={"use_pre_steps": True},
            ),
            db,
        )
    step = result.suites[0].cases[0].steps_snapshot[0]
    assert step["params"]["package"] == "from_step"
    assert "variable_overrides" not in step


async def test_execution_variables_still_win_over_step_variable_override(client):
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "执行参数优先")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        suite_id = await _attach_case_to_suite(db, base["project_id"], case_id, "执行参数套件")
        db.add(
            AppProfileNodeOverride(
                profile_id=profile_id, target_type="step", suite_id=suite_id, case_id=case_id,
                node_key=K1, patch={"variable_overrides": {"pkg": "from_step"}},
            )
        )
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id,
                release_id=await _release_id(db, profile_id), target_type="suite", target_ids=[suite_id],
                expected_profile_revision=1,
                expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                run_options={"use_pre_steps": True}, execution_variables={"pkg": "from_execution"},
            ),
            db,
        )
    assert result.suites[0].cases[0].steps_snapshot[0]["params"]["package"] == "from_execution"


def test_variable_override_references_are_recursive_and_ordered():
    assert variable_references({"a": "${first}/${second}", "nested": ["${first}", {"x": "${third}"}]}) == [
        "first", "second", "third"
    ]


def test_variable_override_patch_validates_source_and_keeps_empty_values():
    source = {
        "key": K1,
        "action": "launch_app",
        "params": {"package": "${pkg}"},
    }
    patched = validate_node_patch("step", source, {"variable_overrides": {"pkg": ""}})
    assert patched["params"]["package"] == source["params"]["package"]
    with pytest.raises(ProfileRuleError, match="变量未被目标步骤引用"):
        validate_node_patch("step", source, {"variable_overrides": {"missing": "x"}})
    with pytest.raises(ProfileRuleError, match="变量覆盖值必须是字符串"):
        validate_node_patch("step", source, {"variable_overrides": {"pkg": 1}})
    with pytest.raises(ProfileRuleError, match="只允许动作步骤"):
        validate_node_patch("assertion", {"type": "element_exists", "params": {"x": "${pkg}"}}, {"variable_overrides": {"pkg": "x"}})


async def test_suite_variable_overrides_case_and_suite_order_is_preserved(client):
    """套件变量覆盖用例变量，解析结果严格遵守套件成员 sort_order。"""
    base = await _base(client)
    case_a = await _setup_case_with_steps(client, base, "顺序A")
    case_b = await _setup_case_with_steps(client, base, "顺序B")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        suite = SuiteModel(project_id=base["project_id"], name="有序套件")
        db.add(suite)
        await db.flush()
        db.add_all(
            [
                SuiteCaseModel(suite_id=suite.id, case_id=case_b, sort_order=1),
                SuiteCaseModel(suite_id=suite.id, case_id=case_a, sort_order=2),
                Variable(
                    scope="suite",
                    project_id=base["project_id"],
                    suite_id=suite.id,
                    name="pkg",
                    value="from_suite",
                ),
            ]
        )
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"],
                profile_id=profile_id,
                release_id=await _release_id(db, profile_id),
                target_type="suite",
                target_ids=[suite.id],
                expected_profile_revision=1,
                expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                run_options={"use_pre_steps": True},
            ),
            db,
        )
    assert [case.case_id for case in result.cases] == [case_b, case_a]
    assert result.cases[0].steps_snapshot[0]["params"]["package"] == "from_suite"


async def test_undefined_variable_rejected(client):
    """未定义变量 → PROFILE_VARIABLE_UNRESOLVED。"""
    base = await _base(client)
    resp = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={"name": "缺变量用例", "steps": [{"key": str(uuid.uuid4()), "order": 1, "action": "launch_app", "params": {"package": "${MISSING}"}}]},
    )
    assert resp.status_code == 201
    case_id = resp.json()["id"]
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        with pytest.raises(ProfileRuleError) as exc:
            await resolve_compat(
                ResolutionRequest(
                    project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
                    target_type="case", target_ids=[case_id],
                    expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                ),
                db,
            )
    assert exc.value.code == "PROFILE_VARIABLE_UNRESOLVED"


async def test_runtime_variable_from_get_text_is_deferred_until_agent(client):
    """get_text 产生的变量在预检时保留占位符，交给 Agent 执行时解析。"""
    base = await _base(client)
    element = await client.post(
        f"/api/projects/{base['project_id']}/elements",
        headers=base["headers"],
        json={"name": "动态文本元素", "locator_type": "id", "locator_value": "source"},
    )
    assert element.status_code == 201
    element_id = element.json()["id"]
    get_text_key = str(uuid.uuid4())
    input_key = str(uuid.uuid4())
    case = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={
            "name": "运行时变量用例",
            "flow_nodes": [
                {
                    "kind": "action", "key": get_text_key, "order": 1, "action": "get_text",
                    "element_id": element_id, "params": {"variable_name": "captured_text"},
                },
                {
                    "kind": "action", "key": input_key, "order": 2, "action": "input",
                    "element_id": element_id, "params": {"value": "${captured_text}"},
                },
            ],
        },
    )
    assert case.status_code == 201
    case_id = case.json()["id"]
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id,
                release_id=await _release_id(db, profile_id), target_type="case", target_ids=[case_id],
                expected_profile_revision=1,
                expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
            ),
            db,
        )

    nodes = result.cases[0].flow_snapshot
    assert nodes[1]["params"]["value"] == "${captured_text}"


async def test_revision_conflict(client):
    """expected revision 不匹配 → ProfileRevisionConflict。"""
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "冲突用例")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        request = ResolutionRequest(
            project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
            target_type="case", target_ids=[case_id],
            expected_profile_revision=99, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
        )
        with pytest.raises(ProfileRevisionConflict):
            await resolve_compat(request, db)


async def test_preview_allows_soft_deleted_element_referenced_by_case(client):
    """设计 §10.3：快照补全含已逻辑删除元素——已保存用例引用被删元素仍可预览。

    校验仅要求元素存在且属于本项目；元素被软删除不应让预览报 PROFILE_ELEMENT_MISSING。
    """
    base = await _base(client)
    el = await client.post(
        f"/api/projects/{base['project_id']}/elements",
        headers=base["headers"],
        json={"name": "登录按钮", "locator_type": "resource_id", "locator_value": "login_btn"},
    )
    element_id = el.json()["id"]
    resp = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={"name": "引用已删元素", "steps": [{"key": str(uuid.uuid4()), "order": 1, "action": "click", "element_id": element_id, "params": {"wait_timeout": 5}}]},
    )
    assert resp.status_code == 201
    case_id = resp.json()["id"]
    # 软删除元素
    deleted = await client.delete(f"/api/elements/{element_id}", headers=base["headers"])
    assert deleted.status_code == 204
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
                target_type="case", target_ids=[case_id],
                expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
            ),
            db,
        )
    assert result.cases[0].steps_snapshot[0]["element_id"] == element_id
    snap = result.cases[0].elements_snapshot[str(element_id)]
    assert snap["locator_type"] == "resource_id"
    assert snap["locator_value"] == "login_btn"


async def test_element_override(client):
    """元素覆盖：快照定位器使用档案覆盖值。"""
    base = await _base(client)
    el = await client.post(
        f"/api/projects/{base['project_id']}/elements",
        headers=base["headers"],
        json={"name": "按钮", "locator_type": "id", "locator_value": "common_id"},
    )
    element_id = el.json()["id"]
    resp = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={"name": "元素覆盖用例", "steps": [{"key": str(uuid.uuid4()), "order": 1, "action": "click", "element_id": element_id, "params": {"wait_timeout": 5}}]},
    )
    case_id = resp.json()["id"]
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        db.add(AppProfileElementOverride(profile_id=profile_id, element_id=element_id, locator_type="resource_id", locator_value="dvr_id"))
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
                target_type="case", target_ids=[case_id],
                expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
            ),
            db,
        )
    elements = result.cases[0].elements_snapshot
    assert elements[str(element_id)]["locator_type"] == "resource_id"
    assert elements[str(element_id)]["locator_value"] == "dvr_id"


async def test_element_override_to_smart_keeps_config_raw(client):
    """普通元素被档案覆盖为 smart：快照写 locator_type='smart'+locator_config，
    ${device_name} 保持未渲染（raw 透传，不在后端求值）。"""
    base = await _base(client)
    el = await client.post(
        f"/api/projects/{base['project_id']}/elements",
        headers=base["headers"],
        json={"name": "按钮", "locator_type": "id", "locator_value": "common_id"},
    )
    element_id = el.json()["id"]
    resp = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={"name": "覆盖为智能用例", "steps": [{"key": str(uuid.uuid4()), "order": 1, "action": "click", "element_id": element_id, "params": {"wait_timeout": 5}}]},
    )
    case_id = resp.json()["id"]
    smart_config = {
        "version": 1,
        "alternatives": [
            {"target": [{"attribute": "text", "operator": "equals", "value": "${device_name}"}]}
        ],
    }
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        db.add(
            AppProfileElementOverride(
                profile_id=profile_id,
                element_id=element_id,
                locator_type="smart",
                locator_value=None,
                locator_config=smart_config,
            )
        )
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
                target_type="case", target_ids=[case_id],
                expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
            ),
            db,
        )
    snap = result.cases[0].elements_snapshot[str(element_id)]
    assert snap["locator_type"] == "smart"
    assert snap["locator_value"] is None
    assert snap["locator_config"] == smart_config
    assert snap["locator_config"]["alternatives"][0]["target"][0]["value"] == "${device_name}"

    # 全链：materialize_snapshot 固化 → _build_suites_payload 下发，config 保持未渲染
    from app.services import worker_service
    from app.services.execution_snapshot import materialize_snapshot

    async with SessionLocal() as db:
        execution = Execution(
            project_id=base["project_id"],
            type="case",
            case_id=case_id,
            status="queued",
            parameters={},
            app_profile_id=profile_id,
            profile_revision=result.profile_revision,
            test_asset_revision=result.test_asset_revision,
        )
        db.add(execution)
        await db.flush()
        await materialize_snapshot(db, execution, result)
        await db.commit()
        await db.refresh(execution)
        payload = await worker_service._build_suites_payload(db, execution)

    assert len(payload) == 1
    case_payload = payload[0]["cases"][0]
    payload_snap = case_payload["elements_snapshot"][str(element_id)]
    assert payload_snap["locator_type"] == "smart"
    assert payload_snap["locator_value"] is None
    assert payload_snap["platform"] == "both"
    # 固化后的 payload 仍保持 ${device_name} 未渲染
    assert payload_snap["locator_config"]["alternatives"][0]["target"][0]["value"] == "${device_name}"


async def test_smart_element_passthrough_without_override(client):
    """元素本身 smart、无覆盖：快照同样原样透传 locator_config，不渲染变量。"""
    base = await _base(client)
    smart_config = {
        "version": 1,
        "alternatives": [
            {"target": [{"attribute": "text", "operator": "equals", "value": "${device_name}"}]}
        ],
    }
    el = await client.post(
        f"/api/projects/{base['project_id']}/elements",
        headers=base["headers"],
        json={"name": "智能按钮", "locator_type": "smart", "locator_config": smart_config},
    )
    assert el.status_code == 201, el.text
    element_id = el.json()["id"]
    resp = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={"name": "智能元素用例", "steps": [{"key": str(uuid.uuid4()), "order": 1, "action": "click", "element_id": element_id, "params": {"wait_timeout": 5}}]},
    )
    case_id = resp.json()["id"]
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
                target_type="case", target_ids=[case_id],
                expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
            ),
            db,
        )
    snap = result.cases[0].elements_snapshot[str(element_id)]
    assert snap["locator_type"] == "smart"
    assert snap["locator_value"] is None
    assert snap["locator_config"]["alternatives"][0]["target"][0]["value"] == "${device_name}"


# ---- 兼容封装：让 resolve_compat 指向模块内 resolve ----

from app.services import profile_resolver as _pr  # noqa: E402


async def resolve_compat(request: ResolutionRequest, db):
    return await _pr.resolve(request, db, _pr.get_resolver().cache)


# ---- Step B13：阶段过滤兜底（纯 setup 用例关闭 pre/post 不应被判空） ----


def _mk(order: int, phase: str, action: str = "sleep") -> dict:
    return {"key": f"k{order}", "order": order, "phase": phase, "action": action, "params": {}}


def test_select_steps_all_setup_closed_runtime_falls_back_to_all():
    nodes = [_mk(1, "setup"), _mk(2, "setup"), _mk(3, "setup")]
    # 关闭 pre/post（默认只跑 main）时，纯 setup 用例应兜底纳入全部阶段
    selected = _select_steps_for_run(nodes, {"use_pre_steps": False, "use_post_steps": False})
    assert len(selected) == 3
    assert [s["order"] for s in selected] == [1, 2, 3]


def test_select_steps_main_only_stays_main():
    nodes = [_mk(1, "setup"), _mk(2, "main"), _mk(3, "teardown")]
    selected = _select_steps_for_run(nodes, {"use_pre_steps": False, "use_post_steps": False})
    assert [s["order"] for s in selected] == [2]


def test_select_steps_pre_enabled_includes_setup_sort():
    nodes = [_mk(1, "main"), _mk(2, "setup"), _mk(3, "teardown")]
    selected = _select_steps_for_run(nodes, {"use_pre_steps": True, "use_post_steps": False})
    assert [s["phase"] for s in selected] == ["setup", "main"]


def test_select_steps_empty_nodes_is_empty():
    assert _select_steps_for_run([], {"use_pre_steps": False, "use_post_steps": False}) == []
