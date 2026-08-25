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
    resp = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={
            "name": name,
            "steps": [
                {"key": K1, "order": 1, "phase": "setup", "action": "launch_app", "params": {"package": "${pkg}"}},
                {"key": K2, "order": 2, "phase": "main", "action": "click", "element_id": None, "params": {"wait_timeout": 5}},
            ],
            "assertions": [
                {"key": K3, "order": 1, "type": "element_exists", "element_id": None, "params": {}}
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
        db.add(AppProfileSkipRule(profile_id=profile_id, target_type="case", case_id=case_id, reason_code="unsupported"))
        await db.commit()
        request = ResolutionRequest(
            project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
            target_type="case", target_ids=[case_id],
            expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
        )
        with pytest.raises(ProfileEmpty):
            await resolve_compat(request, db)


async def test_step_skip_and_override(client):
    """步骤跳过 + 节点参数覆盖：被跳步骤移除，覆盖节点 params 生效。"""
    base = await _base(client)
    case_id = await _setup_case_with_steps(client, base, "步骤跳过用例")
    async with SessionLocal() as db:
        profile_id = await _make_profile(db, base)
        db.add(AppProfileSkipRule(profile_id=profile_id, target_type="step", case_id=case_id, node_key=K2, reason_code="unsupported", reason_note="n"))
        # 覆盖 K1(setup launch_app) 的 params.package
        db.add(AppProfileNodeOverride(profile_id=profile_id, target_type="step", case_id=case_id, node_key=K1, patch={"params": {"package": "patched_pkg"}}))
        await db.commit()
        result = await resolve_compat(
            ResolutionRequest(
                project_id=base["project_id"], profile_id=profile_id, release_id=await _release_id(db, profile_id),
                target_type="case", target_ids=[case_id],
                expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(db, base["project_id"]),
                run_options={"use_pre_steps": True},
            ),
            db,
        )
    assert len(result.cases) == 1
    steps = result.cases[0].steps_snapshot
    assert len(steps) == 1  # K2 被跳过
    assert steps[0]["source_key"] == K1
    assert steps[0]["params"]["package"] == "patched_pkg"


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


# ---- 兼容封装：让 resolve_compat 指向模块内 resolve ----

from app.services import profile_resolver as _pr  # noqa: E402


async def resolve_compat(request: ResolutionRequest, db):
    return await _pr.resolve(request, db, _pr.get_resolver().cache)
