"""Step B4：ProfileResolver 解析引擎行为验证（方案 §11.1）。

基础设施：直接以 ORM 造档案/规则/覆盖 + 公共用例，调用 ProfileResolver.preview。
"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import (
    AppProfile,    AppProfileRelease,
    AppProfileSkipRule,    Execution,
    ExecutionCase,
    Project,
    TestCase,
    TestElement,
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
    variable_references,
)
from app.utils.element_refs import collect_element_ids

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
        },
    )
    assert resp.status_code == 201
    case_id = resp.json()["id"]
    variable = await client.post(
        "/api/variables",
        headers=base["headers"],
        json={"scope": "case", "case_id": case_id, "name": "pkg", "value": "com.v"},
    )
    assert variable.status_code == 201
    return case_id


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


async def _membership_id(db, suite_id: int, case_id: int) -> int:
    """用例节点覆盖以编排项 suite_case_id 为身份。"""
    return (
        await db.execute(
            select(SuiteCaseModel.id)
            .where(SuiteCaseModel.suite_id == suite_id, SuiteCaseModel.case_id == case_id)
            .order_by(SuiteCaseModel.sort_order, SuiteCaseModel.id)
            .limit(1)
        )
    ).scalar_one()


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
        suite_case_id = await _membership_id(db, suite_id, case_id)
        db.add(AppProfileSkipRule(profile_id=profile_id, target_type="case", suite_case_id=suite_case_id, reason_code="unsupported"))
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
        suite_case_id = await _membership_id(db, suite_id, case_id)
        db.add(AppProfileSkipRule(profile_id=profile_id, target_type="step", suite_case_id=suite_case_id, node_key=K2, reason_code="unsupported", reason_note="n"))
        # 覆盖 K1(setup launch_app) 的 params.package
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
    assert steps[0]["params"]["package"] == "com.v"
    other_steps = result.suites[1].cases[0].steps_snapshot
    assert [step["source_key"] for step in other_steps] == [K1, K2]
    assert other_steps[0]["params"]["package"] == "com.v"


async def test_variable_scope_priority_is_consistent_for_suite_setup_and_case(client):
    """global < project < case < suite < profile < execution，前后置与用例一致。"""
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "变量作用域优先级")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        case = await db.get(TestCase, case_id)
        assert case is not None
        suite = SuiteModel(
            project_id=base["project_id"],
            name="变量作用域套件",
            setup_steps=[
                {"key": str(uuid.uuid4()), "order": 1, "action": "launch_app", "params": {"package": "${pkg}"}}
            ],
            teardown_steps=[],
        )
        db.add(suite)
        await db.flush()
        db.add(SuiteCaseModel(suite_id=suite.id, case_id=case_id, sort_order=1))
        case_variable = await db.scalar(
            select(Variable).where(
                Variable.scope == "case",
                Variable.case_id == case_id,
                Variable.name == "pkg",
            )
        )
        assert case_variable is not None
        case_variable.value = "from_case"
        db.add_all(
            [
                Variable(scope="global", name="pkg", value="from_global"),
                Variable(scope="project", project_id=base["project_id"], name="pkg", value="from_project"),
            ]
        )
        await db.commit()

        async def resolve_with(**kwargs):
            return await resolve_compat(
                ResolutionRequest(
                    project_id=base["project_id"],
                    profile_id=profile_id,
                    release_id=await _release_id(db, profile_id),
                    target_type="suite",
                    target_ids=[suite.id],
                    expected_profile_revision=kwargs.pop("expected_profile_revision", 1),
                    expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                    run_options={"use_pre_steps": True},
                    **kwargs,
                ),
                db,
            )

        result = await resolve_with()
        assert result.suites[0].setup_steps_snapshot[0]["params"]["package"] == "from_project"
        assert result.suites[0].cases[0].steps_snapshot[0]["params"]["package"] == "from_case"

        db.add(Variable(scope="suite", project_id=base["project_id"], suite_id=suite.id, name="pkg", value="from_suite"))
        await db.commit()
        result = await resolve_with()
        assert result.suites[0].setup_steps_snapshot[0]["params"]["package"] == "from_suite"
        assert result.suites[0].cases[0].steps_snapshot[0]["params"]["package"] == "from_suite"

        result = await resolve_with(execution_variables={"pkg": "from_execution"})
        assert result.suites[0].setup_steps_snapshot[0]["params"]["package"] == "from_execution"
        assert result.suites[0].cases[0].steps_snapshot[0]["params"]["package"] == "from_execution"


def test_variable_override_references_are_recursive_and_ordered():
    assert variable_references({"a": "${first}/${second}", "nested": ["${first}", {"x": "${third}"}]}) == [
        "first", "second", "third"
    ]


def test_element_preload_reference_scan_covers_nested_assertions_and_parameter_patches():
    assert collect_element_ids(
        {
            "element_id": 11,
            "assertions": [{"element_id": 12, "params": {"value_element_id": 13}}],
        },
        {"params": {"value_element_id": 14}},
    ) == {11, 12, 13, 14}


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


async def test_duplicate_suite_memberships_resolve_and_materialize_independently(client):
    """同一套件中的重复 occurrence 按 suite_case_id 隔离规则。"""
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "重复资产用例")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        suite = SuiteModel(project_id=base["project_id"], name="重复 occurrence 套件")
        db.add(suite)
        await db.flush()
        db.add_all([
            SuiteCaseModel(suite_id=suite.id, case_id=case_id, sort_order=1),
            SuiteCaseModel(suite_id=suite.id, case_id=case_id, sort_order=2),
        ])
        await db.flush()
        membership_ids = [
            row.id for row in (await db.execute(
                select(SuiteCaseModel).where(SuiteCaseModel.suite_id == suite.id).order_by(SuiteCaseModel.sort_order)
            )).scalars().all()
        ]
        db.add(
            AppProfileSkipRule(
                profile_id=profile_id, target_type="step", suite_case_id=membership_ids[0],
                node_key=uuid.UUID(K1), reason_code="unsupported",
            ),
        )
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id,
                release_id=await _release_id(db, profile_id), target_type="suite", target_ids=[suite.id],
                expected_profile_revision=1,
                expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                run_options={"use_pre_steps": True},
            ),
            db,
        )
        assert [item.case_id for item in result.suites[0].cases] == [case_id, case_id]
        assert [item.case_order for item in result.suites[0].cases] == [1, 2]
        assert [step["source_key"] for step in result.suites[0].cases[0].steps_snapshot] == [K2]
        assert [step["source_key"] for step in result.suites[0].cases[1].steps_snapshot] == [K1, K2]

        execution = Execution(
            project_id=base["project_id"], type="suite", suite_id=suite.id,
            status="queued", parameters={}, app_profile_id=profile_id,
            profile_revision=result.profile_revision, test_asset_revision=result.test_asset_revision,
        )
        db.add(execution)
        await db.flush()
        from app.services.execution_snapshot import materialize_snapshot
        await materialize_snapshot(db, execution, result)
        await db.commit()
        rows = list((await db.execute(
            select(ExecutionCase).where(ExecutionCase.execution_id == execution.id).order_by(ExecutionCase.case_order)
        )).scalars().all())
    assert [row.case_id for row in rows] == [case_id, case_id]
    assert [row.case_order for row in rows] == [1, 2]
    assert len({row.id for row in rows}) == 2

    workspace = await client.get(
        f"/api/app-profiles/{profile_id}/workspace/nodes?parent_type=suite&parent_id={suite.id}",
        headers=base["headers"],
    )
    assert workspace.status_code == 200
    items = workspace.json()["items"]
    # occurrence-aware：重复编排各占一行，suid 用 suite_case_id 区分
    assert [item["id"] for item in items] == [case_id, case_id]
    membership_ids = [item["suite_case_id"] for item in items]
    assert len(set(membership_ids)) == 2
    assert all(mid is not None for mid in membership_ids)


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


async def test_preview_allows_directly_soft_deleted_element_referenced_by_case(client):
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
    # 删除 API 会拒绝活动用例引用；这里直接固化历史数据状态，验证解析器仍能读取快照来源。
    async with SessionLocal() as db:
        element = await db.get(TestElement, element_id)
        assert element is not None
        element.deleted_at = datetime.now(UTC)
        await db.commit()
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
