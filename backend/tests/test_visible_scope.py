"""Step 9：owner/member/viewer/public outsider/private outsider 五类用户
在 reports/executions 列表与详情使用同一资源可见规则。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import SessionLocal
from app.main import app
from app.models import Execution, Report

ROLES = {}


def _reg(name: str, email: str) -> dict:
    return {"username": name, "email": email, "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_token(client: AsyncClient, reg: dict) -> str:
    resp = await client.post("/api/auth/register", json=reg)
    assert resp.status_code == 201, resp.text
    login = await client.post("/api/auth/login", json={"username": reg["username"], "password": reg["password"]})
    return login.json()["access_token"]


async def _add_member(client: AsyncClient, token: str, project_id: int, username: str, role: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    candidates = (
        await client.get(f"/api/projects/{project_id}/member-candidates?keyword={username}", headers=headers)
    ).json()
    target_user_id = next(u["id"] for u in candidates if u["username"] == username)
    resp = await client.post(
        f"/api/projects/{project_id}/members",
        headers=headers,
        json={"user_id": target_user_id, "role": role},
    )
    assert resp.status_code in (200, 201), resp.text


async def _seed_report_and_execution(project_id: int, status: str = "passed") -> int:
    """直插一条执行+报告（报告详情可直接读取）。返回 execution_id。"""
    async with SessionLocal() as db:
        execution = Execution(project_id=project_id, type="case", status=status)
        db.add(execution)
        await db.flush()
        db.add(Report(execution_id=execution.id, total=1, passed=1, failed=0, error_count=0, skipped=0, success_rate=100))
        await db.commit()
        return execution.id


async def test_visible_scope_matrix(client: AsyncClient):
    # ---- 五类用户 ----
    owner_t = await _register_token(client, _reg("pytest_vs_owner", "vs-o@t.com"))
    member_t = await _register_token(client, _reg("pytest_vs_member", "vs-m@t.com"))
    viewer_t = await _register_token(client, _reg("pytest_vs_viewer", "vs-v@t.com"))
    public_out_t = await _register_token(client, _reg("pytest_vs_pubout", "vs-p@t.com"))  # 公开项目 outsider
    private_out_t = await _register_token(client, _reg("pytest_vs_privout", "vs-pr@t.com"))  # 私有项目 outsider

    # ---- 项目：A 私有（owner）+ B 公开（另一 owner）----
    owner_headers = {"Authorization": f"Bearer {owner_t}"}
    proj_a = (await client.post("/api/projects", headers=owner_headers, json={"name": "私有项目A", "visibility": "private"})).json()["id"]
    await _add_member(client, owner_t, proj_a, "pytest_vs_member", "member")
    await _add_member(client, owner_t, proj_a, "pytest_vs_viewer", "viewer")

    pub_owner_t = await _register_token(client, _reg("pytest_vs_pubowner", "vs-po@t.com"))
    proj_b = (
        await client.post(
            "/api/projects",
            headers={"Authorization": f"Bearer {pub_owner_t}"},
            json={"name": "公开项目B", "visibility": "public"},
        )
    ).json()["id"]

    exec_a = await _seed_report_and_execution(proj_a)
    exec_b = await _seed_report_and_execution(proj_b)

    async with SessionLocal() as db:
        report_a = (await db.execute(
            __import__("sqlalchemy").select(Report).where(Report.execution_id == exec_a)
        )).scalar_one().id
        report_b = (await db.execute(
            __import__("sqlalchemy").select(Report).where(Report.execution_id == exec_b)
        )).scalar_one().id

    cases = [
        ("owner", owner_t, {exec_a, exec_b}, {report_a, report_b}),
        ("member", member_t, {exec_a, exec_b}, {report_a, report_b}),
        ("viewer", viewer_t, {exec_a, exec_b}, {report_a, report_b}),
        ("public outsider", public_out_t, {exec_b}, {report_b}),
        ("private outsider", private_out_t, {exec_b}, {report_b}),
    ]

    for label, token, visible_execs, visible_reports in cases:
        headers = {"Authorization": f"Bearer {token}"}
        all_execs = {exec_a, exec_b}
        all_reports = {report_a, report_b}
        # 列表：total 与 items 使用完全相同的 visible 条件
        exec_list = (await client.get("/api/executions", headers=headers)).json()
        exec_ids = {e["id"] for e in exec_list["items"]}
        assert exec_ids == visible_execs, f"{label}: executions 列表 {exec_ids} != {visible_execs}"
        assert exec_list["total"] == len(visible_execs), f"{label}: total 与 items 条件不一致"

        report_list = (await client.get("/api/reports", headers=headers)).json()
        report_ids = {r["id"] for r in report_list["items"]}
        assert report_ids == visible_reports, f"{label}: reports 列表 {report_ids} != {visible_reports}"
        assert report_list["total"] == len(visible_reports), f"{label}: total 与 items 条件不一致"

        # 详情：可见范围内按 ID 可访问
        for eid in visible_execs:
            assert (await client.get(f"/api/executions/{eid}", headers=headers)).status_code == 200
        for rid in visible_reports:
            detail = await client.get(f"/api/reports/{rid}/detail", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["execution"]["id"] in visible_execs

        # 不可见资源：列表不可发现，也不能按 ID 访问
        for eid in all_execs - visible_execs:
            resp = await client.get(f"/api/executions/{eid}", headers=headers)
            assert resp.status_code == 403, f"{label}: 不应能访问 execution {eid}"
        for rid in all_reports - visible_reports:
            resp = await client.get(f"/api/reports/{rid}/detail", headers=headers)
            assert resp.status_code == 403, f"{label}: 不应能访问 report {rid}"

    # 私有项目 outsider：既不能列表发现，也不能按 ID 访问（明确断言）
    priv_headers = {"Authorization": f"Bearer {private_out_t}"}
    priv_exec_list = (await client.get("/api/executions", headers=priv_headers)).json()
    assert not any(e["id"] == exec_a for e in priv_exec_list["items"])
    assert (await client.get(f"/api/executions/{exec_a}", headers=priv_headers)).status_code == 403
    priv_report_list = (await client.get("/api/reports", headers=priv_headers)).json()
    assert not any(r["id"] == report_a for r in priv_report_list["items"])
    assert (await client.get(f"/api/reports/{report_a}/detail", headers=priv_headers)).status_code == 403


async def test_public_project_execution_discoverable(client: AsyncClient):
    """公开项目执行可从「全部执行」列表发现（Step 9，执行列表与报告同口径）。"""
    owner_t = await _register_token(client, _reg("pytest_vs_pub2_owner", "vs-p2@t.com"))
    outsider_t = await _register_token(client, _reg("pytest_vs_pub2_out", "vs-p2o@t.com"))
    project_id = (
        await client.post(
            "/api/projects",
            headers={"Authorization": f"Bearer {owner_t}"},
            json={"name": "公开B2", "visibility": "public"},
        )
    ).json()["id"]
    exec_id = await _seed_report_and_execution(project_id)

    outsider_list = (await client.get("/api/executions", headers={"Authorization": f"Bearer {outsider_t}"})).json()
    assert any(e["id"] == exec_id for e in outsider_list["items"])
