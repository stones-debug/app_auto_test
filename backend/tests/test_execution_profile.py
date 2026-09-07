"""Step B10：执行创建快照前移——档案执行固化快照/排除项/队列；revision 冲突 409。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import Execution, ExecutionCase, ExecutionExclusion, ExecutionStep, Project, Report
from app.services import worker_service
from app.services.profile_resolver import ResolutionRequest, resolve
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


async def test_batch_profile_execution_uses_all_suite_ids_and_excludes_skipped_suite(
    client: AsyncClient,
):
    """批量入口把顶层 suite_ids 传入解析器，档案整套跳过后只固化可执行用例。"""
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
    assert execution.parameters["suite_ids"] == suite_ids
    assert [row.case_id for row in rows] == [case_ids[1]]
    assert any(item.suite_id_snapshot == suite_ids[0] for item in exclusions)


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
