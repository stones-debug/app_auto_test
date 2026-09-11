"""Step B10：执行创建快照前移——档案执行固化快照/排除项/队列；revision 冲突 409。"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models import (
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionExclusion,
    ExecutionNode,
    ExecutionPrepare,
    ExecutionStep,
    ExecutionSuite,
    Project,
    Report,
    TestSuite,
)
from app.services import execution_prepare, worker_service
from app.services.cleanup_service import cleanup_expired_prepares
from app.services.profile_resolver import ProfileResolver, ResolutionRequest, resolve
from tests.helpers import create_bound_agent_device

OWNER = {"username": "pytest_execline", "email": "execline@tl-tek.com", "password": "test123"}
K1 = str(uuid.uuid4())


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _base(client: AsyncClient) -> dict:
    await client.post("/api/auth/register", json=OWNER)
    token = (await client.post("/api/auth/login", json={"username": OWNER["username"], "password": "test123"})).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (await client.post("/api/projects", json={"name": "档案执行项目"}, headers=headers)).json()["id"]
    _agent_id, device_id = await create_bound_agent_device(OWNER["username"])
    return {"headers": headers, "project_id": project_id, "device_id": device_id}


async def _make_profile(client: AsyncClient, base: dict) -> tuple[int, int]:
    code = f"p{uuid.uuid4().hex[:6]}"
    profile_id = (await client.post(f"/api/projects/{base['project_id']}/app-profiles", json={"name": "PE", "code": code}, headers=base["headers"])).json()["id"]
    release_id = (
        await client.post(
            f"/api/app-profiles/{profile_id}/releases",
            json={"version": "1.0"},
            headers=base["headers"],
        )
    ).json()["id"]
    return profile_id, release_id


async def _make_case(client: AsyncClient, base: dict) -> int:
    r = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={"name": "档案执行用例", "steps": [{"key": K1, "order": 1, "action": "sleep", "params": {"duration": 1}}], "assertions": []},
    )
    return r.json()["id"]


async def _asset_revision(project_id: int) -> int:
    async with SessionLocal() as db:
        revision = await db.scalar(
            select(Project.test_asset_revision).where(Project.id == project_id)
        )
        assert revision is not None
        return int(revision)


async def test_case_execution_materializes_snapshot(client: AsyncClient):
    """带档案创建用例执行 → 同事务固化 ExecutionCase/ExecutionStep/ExecutionQueue。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "expected_profile_revision": 1,
            "expected_test_asset_revision": await _asset_revision(base["project_id"]),
            "device_id": base["device_id"],
        },
    )
    assert resp.status_code == 201, resp.text
    execution_id = resp.json()["id"]
    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.app_profile_id == profile_id
        cases = (await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == execution_id))).scalars().all()
        assert len(cases) == 1
        assert cases[0].steps_snapshot[0]["action"] == "sleep"
        steps = (await db.execute(select(ExecutionStep).where(ExecutionStep.execution_case_id == cases[0].id))).scalars().all()
        assert len(steps) == 1


async def test_preview_prepare_token_reuses_resolution_and_is_single_use(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """带设备预检会固化 token；创建直接复用快照且 token 只能消费一次。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    revision = await _asset_revision(base["project_id"])
    preview = await client.post(
        "/api/executions/preview",
        headers=base["headers"],
        json={
            "project_id": base["project_id"],
            "target": {"type": "case", "ids": [case_id]},
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "device_id": base["device_id"],
            "parameters": {"use_pre_steps": False, "use_post_steps": False},
        },
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["prepare_token"]
    assert isinstance(token, str) and len(token) >= 40

    async def fail_resolve(*args, **kwargs):
        raise AssertionError("带 prepare_token 的创建不应再次解析")

    monkeypatch.setattr("app.services.execution_service.get_resolver", lambda: SimpleNamespace(preview=fail_resolve))
    created = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={
            "prepare_token": token,
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "expected_profile_revision": 1,
            "expected_test_asset_revision": revision,
            "device_id": base["device_id"],
            "parameters": {"use_pre_steps": False, "use_post_steps": False},
        },
    )
    assert created.status_code == 201, created.text
    repeated = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"prepare_token": token, "device_id": base["device_id"], "parameters": {"use_pre_steps": False, "use_post_steps": False}},
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "EXECUTION_PREPARE_INVALID"


async def test_profile_all_prepare_excludes_direct_skipped_suites_and_reuses_tree(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """profile_all token 固化最终 source，直接跳过套件不产生 N/A。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    suite_ids: list[int] = []
    for name in ("保留套件", "跳过套件"):
        suite_id = (
            await client.post(
                f"/api/projects/{base['project_id']}/suites",
                headers=base["headers"],
                json={"name": name},
            )
        ).json()["id"]
        relation = await client.post(
            f"/api/suites/{suite_id}/cases",
            headers=base["headers"],
            json={"case_id": case_id},
        )
        assert relation.status_code in {200, 201}
        suite_ids.append(suite_id)
    skipped = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        headers=base["headers"],
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "suite", "suite_id": suite_ids[1]}],
        },
    )
    assert skipped.status_code == 200, skipped.text
    preview = await client.post(
        "/api/executions/preview",
        headers=base["headers"],
        json={
            "project_id": base["project_id"],
            "target": {"type": "batch", "ids": [], "target_scope": "profile_all"},
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "device_id": base["device_id"],
        },
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["prepare_token"]

    async def fail_resolve(*args, **kwargs):
        raise AssertionError("profile_all prepare 不应重新解析")

    monkeypatch.setattr("app.services.execution_service.get_resolver", lambda: SimpleNamespace(preview=fail_resolve))
    created = await client.post(
        "/api/executions/suites/batch",
        headers=base["headers"],
        json={"target_scope": "profile_all", "suite_ids": [], "prepare_token": token, "device_id": base["device_id"]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["parameters"]["suite_ids"] == [suite_ids[0]]
    assert created.json()["parameters"]["target_scope"] == "profile_all"
    assert created.json()["parameters"]["excluded_suite_ids"] == []
    async with SessionLocal() as db:
        rows = (
            await db.execute(select(ExecutionExclusion).where(ExecutionExclusion.execution_id == created.json()["id"]))
        ).scalars().all()
        assert all(row.suite_id_snapshot != suite_ids[1] for row in rows)


async def test_profile_all_excluded_suites_are_not_in_snapshot_or_exclusions(
    client: AsyncClient,
):
    """工作台取消项走补集协议，预检/创建都固化规范化集合与最终 source。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    suite_ids: list[int] = []
    for name in ("选中套件", "用户取消套件", "直接跳过套件", "已删除套件"):
        suite = await client.post(
            f"/api/projects/{base['project_id']}/suites",
            headers=base["headers"],
            json={"name": name},
        )
        assert suite.status_code == 201, suite.text
        suite_id = suite.json()["id"]
        relation = await client.post(
            f"/api/suites/{suite_id}/cases",
            headers=base["headers"],
            json={"case_id": case_id},
        )
        assert relation.status_code in {200, 201}, relation.text
        suite_ids.append(suite_id)

    cancelled = suite_ids[1]
    directly_skipped = suite_ids[2]
    deleted = suite_ids[3]
    skipped = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        headers=base["headers"],
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "suite", "suite_id": directly_skipped}],
        },
    )
    assert skipped.status_code == 200, skipped.text
    deleted_response = await client.delete(f"/api/suites/{deleted}", headers=base["headers"])
    assert deleted_response.status_code == 204, deleted_response.text
    preview = await client.post(
        "/api/executions/preview",
        headers=base["headers"],
        json={
            "project_id": base["project_id"],
            "target": {
                "type": "batch",
                "ids": [],
                "target_scope": "profile_all",
                "excluded_suite_ids": [deleted, directly_skipped, cancelled, cancelled],
            },
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "device_id": base["device_id"],
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["counts"]["source_suites"] == 1

    all_cancelled = await client.post(
        "/api/executions/preview",
        headers=base["headers"],
        json={
            "project_id": base["project_id"],
            "target": {
                "type": "batch",
                "ids": [],
                "target_scope": "profile_all",
                "excluded_suite_ids": [suite_ids[0], cancelled],
            },
            "app_profile_id": profile_id,
            "app_release_id": release_id,
        },
    )
    assert all_cancelled.status_code == 400, all_cancelled.text
    assert all_cancelled.json()["detail"]["code"] == "PROFILE_EMPTY"

    tampered_exclusions = await client.post(
        "/api/executions/suites/batch",
        headers=base["headers"],
        json={
            "target_scope": "profile_all",
            "suite_ids": [],
            "excluded_suite_ids": [],
            "prepare_token": preview.json()["prepare_token"],
            "device_id": base["device_id"],
        },
    )
    assert tampered_exclusions.status_code == 409, tampered_exclusions.text
    assert tampered_exclusions.json()["detail"]["code"] == "EXECUTION_PREPARE_INVALID"

    tampered_target = await client.post(
        "/api/executions/suites/batch",
        headers=base["headers"],
        json={
            "target_scope": "profile_all",
            "suite_ids": [suite_ids[0]],
            "excluded_suite_ids": [cancelled],
            "prepare_token": preview.json()["prepare_token"],
            "device_id": base["device_id"],
        },
    )
    assert tampered_target.status_code == 409, tampered_target.text
    assert tampered_target.json()["detail"]["code"] == "EXECUTION_PREPARE_INVALID"

    created = await client.post(
        "/api/executions/suites/batch",
        headers=base["headers"],
        json={
            "target_scope": "profile_all",
            "suite_ids": [],
            "excluded_suite_ids": [deleted, directly_skipped, cancelled, cancelled],
            "prepare_token": preview.json()["prepare_token"],
            "device_id": base["device_id"],
        },
    )
    assert created.status_code == 201, created.text
    execution_id = created.json()["id"]
    assert created.json()["parameters"]["suite_ids"] == [suite_ids[0]]
    assert created.json()["parameters"]["target_scope"] == "profile_all"
    assert created.json()["parameters"]["excluded_suite_ids"] == [cancelled]
    async with SessionLocal() as db:
        assert not (
            await db.execute(
                select(ExecutionExclusion).where(ExecutionExclusion.execution_id == execution_id)
            )
        ).scalars().all()
        execution_suites = (
            await db.execute(select(ExecutionSuite).where(ExecutionSuite.execution_id == execution_id))
        ).scalars().all()
    assert [row.suite_id for row in execution_suites] == [suite_ids[0]]

    missing = await client.post(
        "/api/executions/preview",
        headers=base["headers"],
        json={
            "project_id": base["project_id"],
            "target": {
                "type": "batch",
                "ids": [],
                "target_scope": "profile_all",
                "excluded_suite_ids": [999999999],
            },
            "app_profile_id": profile_id,
            "app_release_id": release_id,
        },
    )
    assert missing.status_code == 422, missing.text


async def test_prepare_rejects_parameter_mismatch_and_expiry_without_consuming(
    client: AsyncClient,
):
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)

    async def make_preview() -> str:
        response = await client.post(
            "/api/executions/preview",
            headers=base["headers"],
            json={
                "project_id": base["project_id"],
                "target": {"type": "case", "ids": [case_id]},
                "app_profile_id": profile_id,
                "app_release_id": release_id,
                "device_id": base["device_id"],
                "parameters": {"use_pre_steps": False},
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["prepare_token"]

    token = await make_preview()
    mismatch = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"prepare_token": token, "device_id": base["device_id"], "parameters": {"use_pre_steps": True}},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "EXECUTION_PREPARE_INVALID"
    async with SessionLocal() as db:
        row = await db.scalar(select(ExecutionPrepare).where(ExecutionPrepare.token_hash == execution_prepare.token_hash(token)))
        assert row is not None and row.consumed_at is None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
        assert await cleanup_expired_prepares(db) == {"prepares_deleted": 1}
    expired = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"prepare_token": token, "device_id": base["device_id"], "parameters": {"use_pre_steps": False}},
    )
    assert expired.status_code == 409
    assert expired.json()["detail"]["code"] == "EXECUTION_PREPARE_INVALID"
    unicode_invalid = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"prepare_token": "无效令牌" * 8, "device_id": base["device_id"], "parameters": {}},
    )
    assert unicode_invalid.status_code == 409
    assert unicode_invalid.json()["detail"]["code"] == "EXECUTION_PREPARE_INVALID"
    assert await make_preview()


async def test_materialize_snapshot_batches_dependency_layers_and_backfills_nodes(
    client: AsyncClient,
):
    """多套件共享用例的物化保持独立树，flush 次数不随节点数增长。"""
    base = await _base(client)
    from app.services.execution_snapshot import materialize_snapshot

    async with SessionLocal() as db:
        suites = [
            TestSuite(project_id=base["project_id"], name=f"批量套件{index}")
            for index in (1, 2)
        ]
        db.add_all(suites)
        await db.flush()
        execution = Execution(
            project_id=base["project_id"], type="batch", status="queued", parameters={}
        )
        db.add(execution)
        await db.flush()
        flow = [
            {"kind": "action", "order": 1, "phase": "case_main", "action": "sleep", "source_key": "action-1", "params": {"duration": 1}},
            {"kind": "assertion", "order": 2, "phase": "case_main", "type": "text_equals", "source_key": "assert-1", "params": {"expected": "ok"}},
        ]
        steps = [
            {"order": 1, "phase": "case_main", "action": "sleep", "source_key": "step-1", "params": {"duration": 1}, "assertions": [{"order": 1, "type": "text_equals", "expected": "ok"}]}
        ]
        result = SimpleNamespace(
            suites=[
                SimpleNamespace(
                    suite_id=suite.id,
                    suite_name=suite.name,
                    suite_order=order,
                    is_virtual=False,
                    is_na=False,
                    setup_steps_snapshot=[{"phase": "suite_setup", "action": "sleep", "source_key": "setup", "params": {"duration": 1}}],
                    teardown_steps_snapshot=[{"phase": "suite_teardown", "action": "sleep", "source_key": "teardown", "params": {"duration": 1}}],
                    elements_snapshot={},
                    cases=[SimpleNamespace(
                        case_id=77,
                        case_name="共享用例",
                        module_name=None,
                        case_order=1,
                        steps_snapshot=steps,
                        elements_snapshot={},
                        flow_snapshot=flow,
                    )],
                )
                for order, suite in enumerate(suites, start=1)
            ]
        )
        flushes = 0

        def before_flush(session, flush_context, instances):
            nonlocal flushes
            if session is db.sync_session:
                flushes += 1

        event.listen(Session, "before_flush", before_flush)
        try:
            await materialize_snapshot(db, execution, result)
            assert flushes == 4
            await db.commit()
        finally:
            event.remove(Session, "before_flush", before_flush)

        execution_suites = (
            await db.execute(select(ExecutionSuite).where(ExecutionSuite.execution_id == execution.id).order_by(ExecutionSuite.suite_order))
        ).scalars().all()
        execution_cases = (
            await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == execution.id).order_by(ExecutionCase.execution_suite_id))
        ).scalars().all()
        case_nodes = (
            await db.execute(select(ExecutionNode).where(ExecutionNode.execution_case_id.in_([case.id for case in execution_cases])))
        ).scalars().all()
        suite_nodes = (
            await db.execute(select(ExecutionNode).where(ExecutionNode.execution_suite_id.in_([suite.id for suite in execution_suites])))
        ).scalars().all()
        steps_rows = (
            await db.execute(select(ExecutionStep).where(ExecutionStep.execution_case_id.in_([case.id for case in execution_cases])))
        ).scalars().all()
        assertions = (
            await db.execute(select(ExecutionAssertion).where(ExecutionAssertion.execution_step_id.in_([step.id for step in steps_rows])))
        ).scalars().all()

    assert len(execution_suites) == 2
    assert len(execution_cases) == 2
    assert {case.execution_suite_id for case in execution_cases} == {suite.id for suite in execution_suites}
    assert len(case_nodes) == 4
    assert len({node.id for node in case_nodes}) == 4
    assert all(node["execution_node_id"] for case in execution_cases for node in case.flow_snapshot)
    assert len(suite_nodes) == 4
    assert len(steps_rows) == 2
    assert len(assertions) == 2


async def test_case_execution_revision_conflict(client: AsyncClient):
    """expected revision 不匹配 → 409，不创建执行。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"app_profile_id": profile_id, "app_release_id": release_id, "expected_profile_revision": 99, "expected_test_asset_revision": await _asset_revision(base["project_id"]), "device_id": base["device_id"]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "PROFILE_REVISION_CONFLICT"


async def test_required_feature_mode_rejects_legacy_execution(client: AsyncClient, monkeypatch):
    base = await _base(client)
    case_id = await _make_case(client, base)
    monkeypatch.setattr(settings, "app_profile_feature_mode", "required")

    response = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"device_id": base["device_id"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "APP_PROFILE_REQUIRED"


async def test_compat_feature_mode_injects_default_profile(client: AsyncClient, monkeypatch):
    base = await _base(client)
    case_id = await _make_case(client, base)
    profile_id = (
        await client.post(
            f"/api/projects/{base['project_id']}/app-profiles",
            headers=base["headers"],
            json={"name": "通用配置（待调整）", "code": f"compat-{uuid.uuid4().hex[:12]}"},
        )
    ).json()["id"]
    await client.post(
        f"/api/app-profiles/{profile_id}/releases",
        headers=base["headers"],
        json={"version": "未标注历史版本"},
    )
    monkeypatch.setattr(settings, "app_profile_feature_mode", "compat")
    monkeypatch.setattr(settings, "app_profile_enabled_project_ids", str(base["project_id"]))

    response = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"device_id": base["device_id"]},
    )

    assert response.status_code == 201, response.text
    async with SessionLocal() as db:
        execution = await db.get(Execution, response.json()["id"])
        assert execution.app_profile_id == profile_id


async def test_profile_execution_requires_active_release(client: AsyncClient):
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    missing = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={
            "app_profile_id": profile_id,
            "expected_profile_revision": 1,
            "expected_test_asset_revision": await _asset_revision(base["project_id"]),
            "device_id": base["device_id"],
        },
    )
    assert missing.status_code == 422
    disabled = await client.patch(
        f"/api/app-profile-releases/{release_id}",
        headers=base["headers"],
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    preview = await client.post(
        "/api/executions/preview",
        headers=base["headers"],
        json={
            "project_id": base["project_id"],
            "target": {"type": "case", "ids": [case_id]},
            "app_profile_id": profile_id,
            "app_release_id": release_id,
        },
    )
    assert preview.status_code == 422
    assert preview.json()["detail"]["code"] == "APP_RELEASE_NOT_FOUND"


async def test_public_asset_change_invalidates_execution_revision(client: AsyncClient):
    """公共用例修改必须推进资产 revision，并拒绝旧预检结果提交。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    old_revision = await _asset_revision(base["project_id"])
    updated = await client.put(
        f"/api/cases/{case_id}",
        headers=base["headers"],
        json={"description": "资产已变化"},
    )
    assert updated.status_code == 200
    assert await _asset_revision(base["project_id"]) == old_revision + 1
    response = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "expected_profile_revision": 1,
            "expected_test_asset_revision": old_revision,
            "device_id": base["device_id"],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TEST_ASSET_REVISION_CONFLICT"


async def test_empty_target_not_created(client: AsyncClient):
    """目标整体 N/A（所有用例跳过）→ 400 PROFILE_EMPTY，不创建执行/不入队。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    suite_id = (
        await client.post(
            f"/api/projects/{base['project_id']}/suites",
            headers=base["headers"],
            json={"name": "全部跳过套件"},
        )
    ).json()["id"]
    await client.post(
        f"/api/suites/{suite_id}/cases",
        headers=base["headers"],
        json={"case_id": case_id},
    )
    await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        headers=base["headers"],
        json={"expected_revision": 1, "operation": "skip", "reason": {"code": "unsupported"}, "targets": [{"type": "case", "suite_id": suite_id, "case_id": case_id}]},
    )
    resp = await client.post(
        f"/api/executions/suites/{suite_id}",
        headers=base["headers"],
        json={"app_profile_id": profile_id, "app_release_id": release_id, "expected_profile_revision": 2, "expected_test_asset_revision": await _asset_revision(base["project_id"]), "device_id": base["device_id"]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "PROFILE_EMPTY"
    async with SessionLocal() as db:
        assert (await db.execute(select(Execution).where(Execution.case_id == case_id))).scalars().all() == []


async def test_resolver_preview_matches(client: AsyncClient):
    """预检与提交走同一解析器：预检计数与固化快照一致。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    async with SessionLocal() as db:
        request = ResolutionRequest(
            project_id=base["project_id"], profile_id=profile_id, release_id=release_id,
            target_type="case", target_ids=[case_id],
            expected_profile_revision=1, expected_test_asset_revision=await _asset_revision(base["project_id"]),
        )
        result = await resolve(request, db)
    assert result.summary["executable_cases"] == 1
    assert result.summary["executable_steps"] == 1


async def test_execution_exclusion_persists_display_names(client: AsyncClient):
    """执行排除项固化套件、用例和节点名称，历史报告无需回查公共资产。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    skipped_key = str(uuid.uuid4())
    kept_key = str(uuid.uuid4())
    case = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={
            "name": "排除项用例",
            "steps": [
                {"key": skipped_key, "order": 1, "action": "sleep", "description": "不支持步骤", "params": {"duration": 1}},
                {"key": kept_key, "order": 2, "action": "sleep", "description": "保留步骤", "params": {"duration": 1}},
            ],
        },
    )
    case_id = case.json()["id"]
    suite = await client.post(
        f"/api/projects/{base['project_id']}/suites",
        headers=base["headers"],
        json={"name": "排除项套件"},
    )
    suite_id = suite.json()["id"]
    await client.post(
        f"/api/suites/{suite_id}/cases",
        headers=base["headers"],
        json={"case_id": case_id},
    )
    skipped = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        headers=base["headers"],
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [
                {
                    "type": "step",
                    "suite_id": suite_id,
                    "case_id": case_id,
                    "node_key": skipped_key,
                }
            ],
        },
    )
    assert skipped.status_code == 200
    created = await client.post(
        f"/api/executions/suites/{suite_id}",
        headers=base["headers"],
        json={
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "expected_profile_revision": 2,
            "expected_test_asset_revision": await _asset_revision(base["project_id"]),
            "device_id": base["device_id"],
        },
    )
    assert created.status_code == 201, created.text
    async with SessionLocal() as db:
        exclusion = (
            await db.execute(
                select(ExecutionExclusion).where(
                    ExecutionExclusion.execution_id == created.json()["id"]
                )
            )
        ).scalar_one()
    assert exclusion.suite_name_snapshot == "排除项套件"
    assert exclusion.case_name_snapshot == "排除项用例"
    assert exclusion.node_name_snapshot == "不支持步骤"


async def test_batch_profile_execution_omits_direct_skipped_suite(
    client: AsyncClient,
):
    """批量入口保留显式目标协议，档案整套跳过后不固化 N/A 套件。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_ids = [await _make_case(client, base), await _make_case(client, base)]
    suite_ids: list[int] = []
    for index, case_id in enumerate(case_ids, start=1):
        suite_id = (
            await client.post(
                f"/api/projects/{base['project_id']}/suites",
                headers=base["headers"],
                json={"name": f"批量套件{index}"},
            )
        ).json()["id"]
        await client.post(
            f"/api/suites/{suite_id}/cases",
            headers=base["headers"],
            json={"case_id": case_id},
        )
        suite_ids.append(suite_id)
    skipped = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        headers=base["headers"],
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "suite", "suite_id": suite_ids[0]}],
        },
    )
    assert skipped.status_code == 200

    created = await client.post(
        "/api/executions/suites/batch",
        headers=base["headers"],
        json={
            "suite_ids": suite_ids,
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "expected_profile_revision": 2,
            "expected_test_asset_revision": await _asset_revision(base["project_id"]),
            "device_id": base["device_id"],
        },
    )

    assert created.status_code == 201, created.text
    async with SessionLocal() as db:
        execution = await db.get(Execution, created.json()["id"])
        rows = (
            await db.execute(
                select(ExecutionCase).where(ExecutionCase.execution_id == execution.id)
            )
        ).scalars().all()
        exclusions = (
            await db.execute(
                select(ExecutionExclusion).where(
                    ExecutionExclusion.execution_id == execution.id
                )
            )
        ).scalars().all()
    assert execution.parameters["suite_ids"] == [suite_ids[1]]
    assert execution.parameters["excluded_suite_ids"] == []
    assert [row.case_id for row in rows] == [case_ids[1]]
    assert all(item.suite_id_snapshot != suite_ids[0] for item in exclusions)


async def test_profile_all_batch_matches_explicit_batch_without_client_suite_paging(
    client: AsyncClient,
):
    """profile_all 由服务端确定项目套件，解析结果与显式 suite_ids 等价。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_ids = [await _make_case(client, base), await _make_case(client, base)]
    suite_ids: list[int] = []
    for index, case_id in enumerate(case_ids, start=1):
        suite_id = (
            await client.post(
                f"/api/projects/{base['project_id']}/suites",
                headers=base["headers"],
                json={"name": f"全量语义套件{index}"},
            )
        ).json()["id"]
        await client.post(
            f"/api/suites/{suite_id}/cases",
            headers=base["headers"],
            json={"case_id": case_id},
        )
        suite_ids.append(suite_id)

    skipped = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        headers=base["headers"],
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [{"type": "suite", "suite_id": suite_ids[0]}],
        },
    )
    assert skipped.status_code == 200, skipped.text

    preview_payload = {
        "project_id": base["project_id"],
        "app_profile_id": profile_id,
        "app_release_id": release_id,
    }
    explicit = await client.post(
        "/api/executions/preview",
        headers=base["headers"],
        json={
            **preview_payload,
            "target": {"type": "batch", "ids": suite_ids},
        },
    )
    profile_all = await client.post(
        "/api/executions/preview",
        headers=base["headers"],
        json={
            **preview_payload,
            "target": {"type": "batch", "ids": [], "target_scope": "profile_all"},
        },
    )
    assert explicit.status_code == 200, explicit.text
    assert profile_all.status_code == 200, profile_all.text
    assert profile_all.json()["counts"] == explicit.json()["counts"]

    conflict = await client.post(
        "/api/executions/suites/batch",
        headers=base["headers"],
        json={
            "target_scope": "profile_all",
            "suite_ids": [],
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "expected_profile_revision": 99,
            "expected_test_asset_revision": await _asset_revision(base["project_id"]),
            "device_id": base["device_id"],
        },
    )
    assert conflict.status_code == 409

    unauthorized = await client.post(
        "/api/executions/suites/batch",
        json={
            "target_scope": "profile_all",
            "suite_ids": [],
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "expected_profile_revision": 2,
            "expected_test_asset_revision": await _asset_revision(base["project_id"]),
            "device_id": base["device_id"],
        },
    )
    assert unauthorized.status_code == 401

    created = await client.post(
        "/api/executions/suites/batch",
        headers=base["headers"],
        json={
            "target_scope": "profile_all",
            "suite_ids": [],
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "expected_profile_revision": 2,
            "expected_test_asset_revision": await _asset_revision(base["project_id"]),
            "device_id": base["device_id"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["parameters"]["target_scope"] == "profile_all"
    assert created.json()["parameters"]["suite_ids"] == [suite_ids[1]]
    assert created.json()["parameters"]["excluded_suite_ids"] == []
    async with SessionLocal() as db:
        execution_cases = (
            await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == created.json()["id"]))
        ).scalars().all()
    assert [row.case_id for row in execution_cases] == [case_ids[1]]


async def test_profile_resolution_query_count_is_bounded_for_multiple_suites(
    client: AsyncClient,
):
    """批量解析的加载查询数不应随套件/用例数量线性增长。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_ids = [await _make_case(client, base), await _make_case(client, base)]
    suite_ids: list[int] = []
    for index, case_id in enumerate(case_ids, start=1):
        suite_id = (
            await client.post(
                f"/api/projects/{base['project_id']}/suites",
                headers=base["headers"],
                json={"name": f"查询趋势套件{index}"},
            )
        ).json()["id"]
        await client.post(
            f"/api/suites/{suite_id}/cases",
            headers=base["headers"],
            json={"case_id": case_id},
        )
        suite_ids.append(suite_id)

    expected_asset_revision = await _asset_revision(base["project_id"])

    async def counted_resolution(target_ids: list[int]) -> int:
        calls: list[str] = []

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            calls.append(statement)

        request = ResolutionRequest(
            project_id=base["project_id"],
            profile_id=profile_id,
            release_id=release_id,
            target_type="batch",
            target_ids=target_ids,
            expected_profile_revision=1,
            expected_test_asset_revision=expected_asset_revision,
        )
        event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
        try:
            async with SessionLocal() as db:
                await ProfileResolver().preview(request, db)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
        return len(calls)

    one_suite_calls = await counted_resolution(suite_ids[:1])
    many_suite_calls = await counted_resolution(suite_ids)
    assert many_suite_calls <= one_suite_calls + 1, (
        f"批量解析查询数不应随目标数量线性增长: one={one_suite_calls}, many={many_suite_calls}"
    )


async def test_batch_shared_case_executes_from_unskipped_suite(client: AsyncClient):
    """回归：共享用例在套件 A 跳过后，批量执行仍从套件 B 执行一次。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    suite_ids: list[int] = []
    for index in (1, 2):
        suite_id = (
            await client.post(
                f"/api/projects/{base['project_id']}/suites",
                headers=base["headers"],
                json={"name": f"共享套件{index}"},
            )
        ).json()["id"]
        await client.post(
            f"/api/suites/{suite_id}/cases",
            headers=base["headers"],
            json={"case_id": case_id},
        )
        suite_ids.append(suite_id)

    skipped = await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        headers=base["headers"],
        json={
            "expected_revision": 1,
            "operation": "skip",
            "reason": {"code": "unsupported"},
            "targets": [
                {"type": "case", "suite_id": suite_ids[0], "case_id": case_id}
            ],
        },
    )
    assert skipped.status_code == 200, skipped.text

    created = await client.post(
        "/api/executions/suites/batch",
        headers=base["headers"],
        json={
            "suite_ids": suite_ids,
            "app_profile_id": profile_id,
            "app_release_id": release_id,
            "expected_profile_revision": 2,
            "expected_test_asset_revision": await _asset_revision(base["project_id"]),
            "device_id": base["device_id"],
        },
    )
    assert created.status_code == 201, created.text

    async with SessionLocal() as db:
        execution_cases = (
            await db.execute(
                select(ExecutionCase).where(
                    ExecutionCase.execution_id == created.json()["id"]
                )
            )
        ).scalars().all()
        exclusions = (
            await db.execute(
                select(ExecutionExclusion).where(
                    ExecutionExclusion.execution_id == created.json()["id"]
                )
            )
        ).scalars().all()
        execution = await db.get(Execution, created.json()["id"])
        execution.status = "running"
        await db.commit()
        await worker_service._mark_terminal(db, execution, "passed")
        report = await db.scalar(
            select(Report).where(Report.execution_id == created.json()["id"])
        )

    assert [item.case_id for item in execution_cases] == [case_id]
    assert any(
        item.target_type == "case" and item.suite_id_snapshot == suite_ids[0]
        for item in exclusions
    )
    assert any(
        item.target_type == "suite" and item.suite_id_snapshot == suite_ids[0]
        for item in exclusions
    )
    assert report.not_applicable_suites == 1


async def test_retry_reuses_profile_snapshot(client: AsyncClient):
    """终态重试复用原执行档案快照，而不是重新解析当前档案。"""
    base = await _base(client)
    profile_id, release_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    created = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"app_profile_id": profile_id, "app_release_id": release_id, "expected_profile_revision": 1, "expected_test_asset_revision": await _asset_revision(base["project_id"]), "device_id": base["device_id"]},
    )
    assert created.status_code == 201
    original_id = created.json()["id"]
    async with SessionLocal() as db:
        original = await db.get(Execution, original_id)
        original.status = "failed"
        original_snapshot = {
            "profile_revision": original.profile_revision,
            "test_asset_revision": original.test_asset_revision,
            "profile_resolution_summary": original.profile_resolution_summary,
        }
        original_case = (
            await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == original_id))
        ).scalar_one()
        original_flow = [
            {key: value for key, value in item.items() if key != "execution_node_id"}
            for item in original_case.flow_snapshot
        ]
        original_case_snapshot = {
            "steps_snapshot": original_case.steps_snapshot,
            "elements_snapshot": original_case.elements_snapshot,
        }
        await db.commit()

    assert (await client.delete(f"/api/cases/{case_id}", headers=base["headers"])).status_code == 204

    retried = await client.post(
        f"/api/executions/{original_id}/retry",
        headers=base["headers"],
        json={"device_id": base["device_id"]},
    )
    assert retried.status_code == 201, retried.text
    data = retried.json()
    assert data["retry_of"] == original_id
    assert data["app_profile_id"] == profile_id
    async with SessionLocal() as db:
        exec2 = await db.get(Execution, data["id"])
        assert exec2.app_profile_id == profile_id
        assert {
            key: getattr(exec2, key) for key in original_snapshot
        } == original_snapshot
        cloned_case = (
            await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == data["id"]))
        ).scalar_one()
        assert {
            key: getattr(cloned_case, key) for key in original_case_snapshot
        } == original_case_snapshot
        cloned_flow = [
            {key: value for key, value in item.items() if key != "execution_node_id"}
            for item in cloned_case.flow_snapshot
        ]
        assert cloned_flow == original_flow
