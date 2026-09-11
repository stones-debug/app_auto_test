"""Step 66：当前用户 APP 档案私有变量。"""

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import (
    AppProfileAuditLog,
    ProjectMember,
    TestSuiteCase,
    User,
    UserAppProfileVariableOverride,
)
from app.services.profile_resolver import ProfileResolver, ResolutionRequest, get_resolver


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


async def _login(client: AsyncClient, prefix: str) -> tuple[str, int]:
    user = {"username": f"{prefix}_{uuid.uuid4().hex[:8]}", "email": f"{prefix}_{uuid.uuid4().hex[:8]}@example.com", "password": "test123"}
    registered = await client.post("/api/auth/register", json=user)
    assert registered.status_code == 201, registered.text
    token = (await client.post("/api/auth/login", json={"username": user["username"], "password": user["password"]})).json()["access_token"]
    async with SessionLocal() as db:
        result = await db.execute(select(User).where(User.username == user["username"]))
        return token, result.scalar_one().id


async def _asset_graph(client: AsyncClient, headers: dict[str, str], variable_name: str = "username") -> tuple[int, int, int, int, int]:
    project_id = (await client.post("/api/projects", json={"name": f"变量项目-{uuid.uuid4().hex[:6]}"}, headers=headers)).json()["id"]
    suite_id = (await client.post(f"/api/projects/{project_id}/suites", json={"name": "套件A"}, headers=headers)).json()["id"]
    case_response = await client.post(f"/api/projects/{project_id}/cases", json={"name": "用例A", "steps": [{"key": str(uuid.uuid4()), "order": 1, "action": "launch_app", "params": {"package": f"${{{variable_name}}}"}}], "assertions": []}, headers=headers)
    assert case_response.status_code == 201, case_response.text
    case_id = case_response.json()["id"]
    membership_id = (await client.post(f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=headers)).json()[0]["id"]
    variable_id = (await client.post("/api/variables", json={"scope": "suite", "suite_id": suite_id, "name": variable_name, "value": "public"}, headers=headers)).json()["id"]
    profile_id = (await client.post(f"/api/projects/{project_id}/app-profiles", json={"name": "设备A", "code": f"p{uuid.uuid4().hex[:7]}"}, headers=headers)).json()["id"]
    return project_id, suite_id, membership_id, variable_id, profile_id


@pytest.mark.asyncio
async def test_user_variable_isolation_restore_empty_and_sensitive(client: AsyncClient):
    owner_token, owner_id = await _login(client, "step66_owner")
    other_token, other_id = await _login(client, "step66_other")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}
    project_id, suite_id, _membership_id, variable_id, profile_id = await _asset_graph(client, owner_headers)
    async with SessionLocal() as db:
        db.add(ProjectMember(project_id=project_id, user_id=other_id, role="member"))
        await db.commit()

    listed = await client.get(f"/api/app-profiles/{profile_id}/my-variables", headers=owner_headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["variable_id"] == variable_id
    request_id = str(uuid.uuid4())
    saved = await client.patch(f"/api/app-profiles/{profile_id}/my-variables", json={"request_id": request_id, "updates": [{"variable_id": variable_id, "value": ""}]}, headers=owner_headers)
    assert saved.status_code == 200
    assert saved.json()["items"][0]["display_value"] == ""
    replay = await client.patch(f"/api/app-profiles/{profile_id}/my-variables", json={"request_id": request_id, "updates": [{"variable_id": variable_id, "value": "other"}]}, headers=owner_headers)
    assert replay.status_code == 200
    assert replay.json()["items"][0]["display_value"] == ""
    other_write = await client.patch(f"/api/app-profiles/{profile_id}/my-variables", json={"request_id": request_id, "updates": [{"variable_id": variable_id, "value": "other"}]}, headers=other_headers)
    assert other_write.status_code == 200
    other = await client.get(f"/api/app-profiles/{profile_id}/my-variables", headers=other_headers)
    assert other.status_code == 200
    assert other.json()["items"][0]["display_value"] == "other"
    restored = await client.patch(f"/api/app-profiles/{profile_id}/my-variables", json={"request_id": str(uuid.uuid4()), "updates": [{"variable_id": variable_id, "value": None}]}, headers=owner_headers)
    assert restored.status_code == 200
    assert restored.json()["items"][0]["overridden"] is False
    async with SessionLocal() as db:
        assert (await db.execute(select(UserAppProfileVariableOverride).where(UserAppProfileVariableOverride.user_id == owner_id))).scalars().all() == []
        audits = (await db.execute(select(AppProfileAuditLog).where(AppProfileAuditLog.profile_id == profile_id, AppProfileAuditLog.actor_id == owner_id))).scalars().all()
        assert len([row for row in audits if row.action == "user_variable_override_batch"]) == 2
        assert all("public" not in str(row.changes) for row in audits)


@pytest.mark.asyncio
async def test_sensitive_user_variable_never_returns_plaintext(client: AsyncClient):
    token, _user_id = await _login(client, "step66_sensitive")
    headers = {"Authorization": f"Bearer {token}"}
    _project_id, suite_id, _membership_id, secret_id, profile_id = await _asset_graph(client, headers, "secret")
    updated = await client.put(f"/api/variables/{secret_id}", json={"value": "public-secret", "is_sensitive": True}, headers=headers)
    assert updated.status_code == 200
    listed = await client.get(f"/api/app-profiles/{profile_id}/my-variables", headers=headers)
    assert listed.status_code == 200
    secret = next(row for row in listed.json()["items"] if row["variable_id"] == secret_id)
    assert secret["public_value"] is None and "public-secret" not in str(secret)
    saved = await client.patch(f"/api/app-profiles/{profile_id}/my-variables", json={"request_id": str(uuid.uuid4()), "updates": [{"variable_id": secret_id, "value": "private-secret"}]}, headers=headers)
    secret = next(row for row in saved.json()["items"] if row["variable_id"] == secret_id)
    assert secret["user_value"] is None and "private-secret" not in str(secret)
    public = await client.get(f"/api/variables?scope=suite&suite_id={suite_id}", headers=headers)
    assert public.status_code == 200
    assert next(row for row in public.json() if row["id"] == secret_id)["value"] == "********"


@pytest.mark.asyncio
async def test_occurrence_profile_value_beats_execution_and_user(client: AsyncClient):
    token, _user_id = await _login(client, "step66_priority")
    headers = {"Authorization": f"Bearer {token}"}
    project_id, suite_id, membership_id, variable_id, profile_id = await _asset_graph(client, headers)
    assert (await client.patch(f"/api/app-profiles/{profile_id}/my-variables", json={"request_id": str(uuid.uuid4()), "updates": [{"variable_id": variable_id, "value": "user"}]}, headers=headers)).status_code == 200
    occurrence = await client.patch(f"/api/app-profiles/{profile_id}/suite-cases/{membership_id}/variable-overrides", json={"expected_revision": 1, "updates": [{"name": "username", "value": "capability"}]}, headers=headers)
    assert occurrence.status_code == 200
    assert occurrence.json()["variables"][0]["references"][0]["override_value"] == "capability"
    release = await client.post(f"/api/app-profiles/{profile_id}/releases", json={"request_id": str(uuid.uuid4()), "version": "1.0"}, headers=headers)
    assert release.status_code == 201
    async with SessionLocal() as db:
        from app.models import AppProfile
        profile = await db.get(AppProfile, profile_id)
        assert profile is not None
        from app.models import Project
        project = await db.get(Project, project_id)
        assert project is not None
        request = ResolutionRequest(
            project_id=project_id, profile_id=profile_id, release_id=release.json()["id"],
            target_type="suite", target_ids=[suite_id], expected_profile_revision=profile.revision,
            expected_test_asset_revision=project.test_asset_revision,
            execution_variables={"username": "execution"}, user_id=_user_id,
        )
        result = await get_resolver().preview(request, db)
        assert result.suites[0].cases[0].flow_snapshot[0]["params"]["package"] == "capability"


@pytest.mark.asyncio
async def test_cached_shared_config_does_not_stale_user_override(client: AsyncClient):
    token, user_id = await _login(client, "step66_cache")
    headers = {"Authorization": f"Bearer {token}"}
    project_id, suite_id, _membership_id, variable_id, profile_id = await _asset_graph(client, headers)
    release = await client.post(f"/api/app-profiles/{profile_id}/releases", json={"request_id": str(uuid.uuid4()), "version": "1.0"}, headers=headers)
    assert release.status_code == 201
    async with SessionLocal() as db:
        from app.models import AppProfile, Project
        profile = await db.get(AppProfile, profile_id)
        project = await db.get(Project, project_id)
        assert profile is not None and project is not None
        request = ResolutionRequest(
            project_id=project_id, profile_id=profile_id, release_id=release.json()["id"],
            target_type="suite", target_ids=[suite_id], expected_profile_revision=profile.revision,
            expected_test_asset_revision=project.test_asset_revision, user_id=user_id,
        )
        resolver = ProfileResolver()
        first = await resolver.preview(request, db)
        assert first.suites[0].cases[0].flow_snapshot[0]["params"]["package"] == "public"
        saved = await client.patch(f"/api/app-profiles/{profile_id}/my-variables", json={"request_id": str(uuid.uuid4()), "updates": [{"variable_id": variable_id, "value": "fresh"}]}, headers=headers)
        assert saved.status_code == 200
        second = await resolver.preview(request, db)
        assert second.suites[0].cases[0].flow_snapshot[0]["params"]["package"] == "fresh"


@pytest.mark.asyncio
async def test_candidate_variables_keep_suite_identity_and_membership_mask(client: AsyncClient):
    token, _user_id = await _login(client, "step66_identity")
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (await client.post("/api/projects", json={"name": f"身份项目-{uuid.uuid4().hex[:6]}"}, headers=headers)).json()["id"]
    suite_a = (await client.post(f"/api/projects/{project_id}/suites", json={"name": "套件A"}, headers=headers)).json()["id"]
    suite_b = (await client.post(f"/api/projects/{project_id}/suites", json={"name": "套件B"}, headers=headers)).json()["id"]
    case = await client.post(f"/api/projects/{project_id}/cases", json={"name": "共享用例", "steps": [{"key": str(uuid.uuid4()), "order": 1, "action": "launch_app", "params": {"package": "${username}"}}], "assertions": []}, headers=headers)
    assert case.status_code == 201
    case_id = case.json()["id"]
    membership_a = (await client.post(f"/api/suites/{suite_a}/cases", json={"case_id": case_id}, headers=headers)).json()[0]["id"]
    membership_b = (await client.post(f"/api/suites/{suite_b}/cases", json={"case_id": case_id}, headers=headers)).json()[0]["id"]
    var_a = (await client.post("/api/variables", json={"scope": "suite", "suite_id": suite_a, "name": "username", "value": "a"}, headers=headers)).json()["id"]
    var_b = (await client.post("/api/variables", json={"scope": "suite", "suite_id": suite_b, "name": "username", "value": "b"}, headers=headers)).json()["id"]
    profile_id = (await client.post(f"/api/projects/{project_id}/app-profiles", json={"name": "设备身份", "code": f"p{uuid.uuid4().hex[:7]}"}, headers=headers)).json()["id"]
    listed = await client.get(f"/api/app-profiles/{profile_id}/my-variables?page=1&page_size=1", headers=headers)
    assert listed.status_code == 200 and listed.json()["total"] == 2
    first = listed.json()["items"][0]
    second = (await client.get(f"/api/app-profiles/{profile_id}/my-variables?page=2&page_size=1", headers=headers)).json()["items"][0]
    assert [first["variable_id"], second["variable_id"]] == sorted([var_a, var_b])
    async with SessionLocal() as db:
        membership = await db.get(TestSuiteCase, membership_a)
        assert membership is not None
        membership.variable_overrides = {"username": "occurrence"}
        await db.commit()
    masked = await client.get(f"/api/app-profiles/{profile_id}/my-variables", headers=headers)
    assert masked.json()["total"] == 1
    assert masked.json()["items"][0]["variable_id"] == var_b
    assert membership_b != membership_a


@pytest.mark.asyncio
async def test_case_scope_variable_has_stable_identity_and_private_override(client: AsyncClient):
    token, _user_id = await _login(client, "step67_case_identity")
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (await client.post("/api/projects", json={"name": f"内联项目-{uuid.uuid4().hex[:6]}"}, headers=headers)).json()["id"]
    project_var = (await client.post("/api/variables", json={"scope": "project", "project_id": project_id, "name": "username", "value": "project"}, headers=headers)).json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        json={"name": "正式用例变量", "steps": [{"key": str(uuid.uuid4()), "order": 1, "action": "launch_app", "params": {"package": "${username}"}}], "assertions": []},
        headers=headers,
    )
    assert case.status_code == 201
    case_id = case.json()["id"]
    case_var = await client.post(
        "/api/variables",
        json={"scope": "case", "case_id": case_id, "name": "username", "value": "case"},
        headers=headers,
    )
    assert case_var.status_code == 201
    case_variable_id = case_var.json()["id"]
    profile_id = (await client.post(f"/api/projects/{project_id}/app-profiles", json={"name": "内联设备", "code": f"p{uuid.uuid4().hex[:7]}"}, headers=headers)).json()["id"]
    listed = await client.get(f"/api/app-profiles/{profile_id}/my-variables", headers=headers)
    assert listed.status_code == 200
    listed_ids = {row["variable_id"] for row in listed.json()["items"]}
    assert case_variable_id in listed_ids
    assert project_var not in listed_ids
    saved = await client.patch(
        f"/api/app-profiles/{profile_id}/my-variables",
        json={"request_id": str(uuid.uuid4()), "updates": [{"variable_id": case_variable_id, "value": "private-case"}]},
        headers=headers,
    )
    assert saved.status_code == 200
    assert next(row for row in saved.json()["items"] if row["variable_id"] == case_variable_id)["display_value"] == "private-case"


@pytest.mark.asyncio
async def test_concurrent_same_request_is_serialized_and_replayed(client: AsyncClient):
    token, _user_id = await _login(client, "step66_concurrent")
    headers = {"Authorization": f"Bearer {token}"}
    _project_id, _suite_id, _membership_id, variable_id, profile_id = await _asset_graph(client, headers)
    request_id = str(uuid.uuid4())
    responses = await asyncio.gather(*(
        client.patch(
            f"/api/app-profiles/{profile_id}/my-variables",
            json={"request_id": request_id, "updates": [{"variable_id": variable_id, "value": value}]},
            headers=headers,
        )
        for value in ("first", "second")
    ))
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    async with SessionLocal() as db:
        audits = (await db.execute(select(AppProfileAuditLog).where(AppProfileAuditLog.profile_id == profile_id, AppProfileAuditLog.request_id == request_id))).scalars().all()
        assert len(audits) == 1
