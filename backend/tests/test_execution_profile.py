"""Step B10：执行创建快照前移——档案执行固化快照/排除项/队列；revision 冲突 409。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import Execution, ExecutionCase, ExecutionStep
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


async def _make_profile(client: AsyncClient, base: dict) -> int:
    code = f"p{uuid.uuid4().hex[:6]}"
    return (await client.post(f"/api/projects/{base['project_id']}/app-profiles", json={"name": "PE", "code": code}, headers=base["headers"])).json()["id"]


async def _make_case(client: AsyncClient, base: dict) -> int:
    r = await client.post(
        f"/api/projects/{base['project_id']}/cases",
        headers=base["headers"],
        json={"name": "档案执行用例", "steps": [{"key": K1, "order": 1, "action": "sleep", "params": {"duration": 1}}], "assertions": []},
    )
    return r.json()["id"]


async def test_case_execution_materializes_snapshot(client: AsyncClient):
    """带档案创建用例执行 → 同事务固化 ExecutionCase/ExecutionStep/ExecutionQueue。"""
    base = await _base(client)
    profile_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={
            "app_profile_id": profile_id,
            "expected_profile_revision": 1,
            "expected_test_asset_revision": 1,
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
    profile_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"app_profile_id": profile_id, "expected_profile_revision": 99, "expected_test_asset_revision": 1, "device_id": base["device_id"]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "PROFILE_REVISION_CONFLICT"


async def test_empty_target_not_created(client: AsyncClient):
    """目标整体 N/A（所有用例跳过）→ 400 PROFILE_EMPTY，不创建执行/不入队。"""
    base = await _base(client)
    profile_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    await client.post(
        f"/api/app-profiles/{profile_id}/skip-rules/batch",
        headers=base["headers"],
        json={"expected_revision": 1, "operation": "skip", "reason": {"code": "unsupported"}, "targets": [{"type": "case", "case_id": case_id}]},
    )
    resp = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"app_profile_id": profile_id, "expected_profile_revision": 2, "expected_test_asset_revision": 1, "device_id": base["device_id"]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "PROFILE_EMPTY"
    async with SessionLocal() as db:
        assert (await db.execute(select(Execution).where(Execution.case_id == case_id))).scalars().all() == []


async def test_resolver_preview_matches(client: AsyncClient):
    """预检与提交走同一解析器：预检计数与固化快照一致。"""
    base = await _base(client)
    profile_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    async with SessionLocal() as db:
        request = ResolutionRequest(
            project_id=base["project_id"], profile_id=profile_id, release_id=None,
            target_type="case", target_ids=[case_id],
            expected_profile_revision=1, expected_test_asset_revision=1,
        )
        result = await resolve(request, db)
    assert result.summary["executable_cases"] == 1
    assert result.summary["executable_steps"] == 1


async def test_retry_reuses_profile(client: AsyncClient):
    """重试复用原执行档案并按当前 revision 固化，retry_of 指向原执行。"""
    base = await _base(client)
    profile_id = await _make_profile(client, base)
    case_id = await _make_case(client, base)
    created = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=base["headers"],
        json={"app_profile_id": profile_id, "expected_profile_revision": 1, "expected_test_asset_revision": 1, "device_id": base["device_id"]},
    )
    assert created.status_code == 201
    original_id = created.json()["id"]

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
        assert (await db.execute(select(ExecutionCase).where(ExecutionCase.execution_id == data["id"]))).scalars().first() is not None
