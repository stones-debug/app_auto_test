import shutil
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select as sa_select

from app.core.config import reports_dir
from app.core.database import SessionLocal
from app.main import app
from app.models import (
    Device,
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionExclusion,
    ExecutionStep,
    ExecutionSuite,
    Report,
)
from app.services import report_service, worker_service
from app.services.screenshot_store import resolve_screenshot_path, validate_object_key
from app.ws import handlers
from tests.helpers import create_bound_agent_device

REG = {"username": "pytest_report_user", "email": "rp@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup(client: AsyncClient) -> tuple[str, int]:
    """返回 (token, execution_id)，并造好 steps/assertions/报告数据。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post("/api/auth/login", json={"username": REG["username"], "password": REG["password"]})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post("/api/projects", headers=headers, json={"name": "报告测试项目", "visibility": "private"})
    project_id = project.json()["id"]
    profile_id = (
        await client.post(
            f"/api/projects/{project_id}/app-profiles",
            headers=headers,
            json={"name": "报告档案", "code": f"report-{uuid.uuid4().hex[:6]}"},
        )
    ).json()["id"]
    release_id = (
        await client.post(
            f"/api/app-profiles/{profile_id}/releases",
            headers=headers,
            json={"version": "1.0"},
        )
    ).json()["id"]
    element = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "用户名", "locator_type": "id", "locator_value": "username"},
    )
    element_id = element.json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        headers=headers,
        json={
            "name": "报告用例",
            "steps": [
                {"order": 1, "action": "input", "element_id": element_id, "params": {"value": "admin"}},
                {"order": 2, "action": "click", "element_id": element_id, "params": {}},
            ],
            "assertions": [
                {"order": 1, "type": "text_equals", "element_id": element_id, "params": {"expected": "admin"}}
            ],
        },
    )
    case_id = case.json()["id"]
    # Windows 方案 §3.3：执行创建必须指定已授权设备
    agent_id, device_id = await create_bound_agent_device(REG["username"])
    execution = await client.post(
        f"/api/executions/cases/{case_id}", headers=headers,
        json={"device_id": device_id, "parameters": {"variables": {"account": "admin"}}},
    )
    assert execution.status_code == 201, execution.text
    execution_id = execution.json()["id"]
    _ = agent_id

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        await worker_service.create_execution_cases_from_execution(db, execution)
        device = Device(agent_id=agent_id, name="rp-device", platform="android", udid=f"u-{uuid.uuid4().hex[:8]}", status="busy")
        db.add(device)
        await db.flush()
        execution.device_id = device.id
        execution.app_profile_id = profile_id
        execution.app_profile_name_snapshot = "报告档案"
        execution.app_release_id = release_id
        execution.app_release_version_snapshot = "1.0"
        execution.profile_revision = 1
        execution.test_asset_revision = 3
        db.add(
            ExecutionExclusion(
                execution_id=execution_id,
                app_profile_id=profile_id,
                target_type="step",
                case_id_snapshot=case_id,
                case_name_snapshot="报告用例",
                node_name_snapshot="不支持步骤",
                source_type="direct",
                reason_code="unsupported",
            )
        )
        execution.status = "running"
        execution.session_token = "rp-token"
        execution.started_at = datetime.now(UTC)
        await db.commit()
        await handlers.handle_log(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "rp-token",
                "level": "INFO",
                "message": "步骤 1 input 执行通过",
                "step_order": 1,
            },
        )
        # 协议 V2：只按 execution_*_id 精确定位（旧协议 case_id/step_order 已拒绝）
        ec = (await db.execute(
            sa_select(ExecutionCase).where(ExecutionCase.execution_id == execution_id)
        )).scalar_one()
        step_rows = (
            await db.execute(
                sa_select(ExecutionStep)
                .where(ExecutionStep.execution_case_id == ec.id)
                .order_by(ExecutionStep.step_order)
            )
        ).scalars().all()
        await handlers.handle_step_result(
            db,
            agent_id,
            {"execution_id": execution_id, "session_token": "rp-token", "execution_step_id": step_rows[0].id, "action": "input", "status": "passed", "actual_value": "admin"},
        )
        await handlers.handle_step_result(
            db,
            agent_id,
            {"execution_id": execution_id, "session_token": "rp-token", "execution_step_id": step_rows[1].id, "action": "click", "status": "passed"},
        )
        assertion_row = (await db.execute(
            sa_select(ExecutionAssertion).where(ExecutionAssertion.execution_case_id == ec.id)
        )).scalar_one()
        await handlers.handle_assertion_result(
            db,
            agent_id,
            {
                "execution_id": execution_id,
                "session_token": "rp-token",
                "execution_case_id": ec.id,
                "assertions": [{"execution_assertion_id": assertion_row.id, "type": "text_equals", "expected": "admin", "actual": "admin", "status": "pass"}],
            },
        )
        execution = await db.get(Execution, execution_id)
        await worker_service._mark_terminal(db, execution, "passed")
    return token, execution_id


async def _report_id(execution_id: int) -> int:
    async with SessionLocal() as db:
        report = (await db.execute(sa_select(Report).where(Report.execution_id == execution_id))).scalar_one()
        return report.id


def _cleanup(execution_id: int) -> None:
    target = reports_dir() / f"execution_{execution_id}"
    if target.exists():
        shutil.rmtree(target)


async def _suite_with_setup_steps(client: AsyncClient) -> tuple[str, int, int, int]:
    """造一个含套件前置/后置步的执行，返回 (token, execution_id, project_id, case_id)。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post("/api/auth/login", json={"username": REG["username"], "password": REG["password"]})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = await client.post(
        "/api/projects", headers=headers, json={"name": "套件报告项目", "visibility": "private"}
    )
    project_id = project.json()["id"]
    profile_id = (
        await client.post(
            f"/api/projects/{project_id}/app-profiles",
            headers=headers,
            json={"name": "套件档案", "code": f"suite-{uuid.uuid4().hex[:6]}"},
        )
    ).json()["id"]
    release_id = (
        await client.post(f"/api/app-profiles/{profile_id}/releases", headers=headers, json={"version": "1.0"})
    ).json()["id"]
    element = await client.post(
        f"/api/projects/{project_id}/elements",
        headers=headers,
        json={"name": "元素", "locator_type": "id", "locator_value": "x"},
    )
    element_id = element.json()["id"]
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        headers=headers,
        json={
            "name": "套件内用例",
            "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}],
            "assertions": [],
        },
    )
    case_id = case.json()["id"]
    agent_id, device_id = await create_bound_agent_device(REG["username"])
    execution = await client.post(
        f"/api/executions/cases/{case_id}",
        headers=headers,
        json={"device_id": device_id, "parameters": {}},
    )
    assert execution.status_code == 201, execution.text
    execution_id = execution.json()["id"]
    _ = agent_id

    async with SessionLocal() as db:
        execution = await db.get(Execution, execution_id)
        suite = ExecutionSuite(
            execution_id=execution_id,
            suite_id=None,
            suite_name="登录套件",
            suite_order=1,
            is_virtual=True,
            status="pending",
            setup_steps_snapshot=[],
            teardown_steps_snapshot=[],
            elements_snapshot={},
        )
        db.add(suite)
        await db.flush()
        case_row = ExecutionCase(
            execution_id=execution_id,
            execution_suite_id=suite.id,
            case_id=case_id,
            case_name="套件内用例",
            case_order=1,
            status="pending",
            steps_snapshot=[{"order": 1, "action": "click", "phase": "case_main", "params": {}}],
            assertions_snapshot=[],
            elements_snapshot={},
        )
        db.add(case_row)
        await db.flush()
        db.add(
            ExecutionStep(
                execution_suite_id=suite.id, step_order=1, action="准备环境", phase="suite_setup", status="passed", actual_value="ok"
            )
        )
        db.add(
            ExecutionStep(
                execution_suite_id=suite.id, step_order=2, action="清理环境", phase="suite_teardown", status="passed", actual_value="ok"
            )
        )
        db.add(
            ExecutionStep(
                execution_case_id=case_row.id, step_order=1, action="click", phase="case_main", status="passed", actual_value="done"
            )
        )
        device = await db.get(Device, device_id)
        execution.device_id = device_id
        execution.app_profile_id = profile_id
        execution.app_profile_name_snapshot = "套件档案"
        execution.app_release_id = release_id
        execution.app_release_version_snapshot = "1.0"
        execution.profile_revision = 1
        execution.test_asset_revision = 3
        execution.status = "running"
        execution.started_at = datetime.now(UTC)
        device.status = "busy"
        device.locked_by_execution = execution_id
        await db.commit()
        await worker_service._mark_terminal(db, execution, "passed")
    return token, execution_id, project_id, case_id


async def test_report_list_and_detail(client: AsyncClient):
    token, execution_id = await _setup(client)
    headers = {"Authorization": f"Bearer {token}"}
    report_id = await _report_id(execution_id)

    # 模拟历史执行：旧逻辑已把步骤参数落成空字典，详情应从不可变快照恢复。
    async with SessionLocal() as db:
        step = (
            await db.execute(
                sa_select(ExecutionStep)
                .join(ExecutionCase, ExecutionStep.execution_case_id == ExecutionCase.id)
                .where(
                    ExecutionCase.execution_id == execution_id,
                    ExecutionStep.step_order == 1,
                )
            )
        ).scalar_one()
        step.parameters = {}
        await db.commit()

    listed = await client.get("/api/reports", headers=headers)
    assert listed.status_code == 200
    item = next(i for i in listed.json()["items"] if i["id"] == report_id)
    assert item["execution_status"] == "passed"
    assert item["case_name"] == "报告用例"
    assert item["passed"] == 1
    assert item["total"] == 1
    async with SessionLocal() as db:
        profile_id = (await db.get(Execution, execution_id)).app_profile_id
    filtered = await client.get(f"/api/reports?app_profile_id={profile_id}", headers=headers)
    assert filtered.status_code == 200
    assert any(row["id"] == report_id for row in filtered.json()["items"])
    empty = await client.get("/api/reports?app_profile_id=2147483647", headers=headers)
    assert empty.json()["total"] == 0

    detail = await client.get(f"/api/reports/{report_id}/detail", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["execution"]["status"] == "passed"
    assert body["execution"]["app_profile_name"] == "报告档案"
    assert body["execution"]["app_release_version"] == "1.0"
    assert body["execution"]["parameters"] == {"variables": {"account": "admin"}}
    assert body["report"]["passed"] == 1
    # 套件执行的用例仅通过 suites 返回，避免重复发送整棵用例/步骤树。
    assert body["cases"] == []
    report_case = body["suites"][0]["cases"][0]
    assert report_case["case_name"] == "报告用例"
    assert [s["action"] for s in report_case["steps"]] == ["input", "click"]
    assert report_case["steps"][0]["parameters"]["value"] == "admin"
    assert report_case["steps"][0]["parameters"]["clear_first"] is True
    assert report_case["steps"][0]["actual_value"] == "admin"
    assert report_case["assertions"][0]["assertion_type"] == "text_equals"
    assert report_case["assertions"][0]["status"] == "pass"
    assert body["exclusions"][0]["path"] == "报告用例/不支持步骤"
    assert body["logs"][0]["message"] == "步骤 1 input 执行通过"

    _cleanup(execution_id)


async def test_report_download_generates_and_caches(client: AsyncClient):
    token, execution_id = await _setup(client)
    headers = {"Authorization": f"Bearer {token}"}
    report_id = await _report_id(execution_id)

    resp1 = await client.get(f"/api/reports/{report_id}/download", headers=headers)
    assert resp1.status_code == 200
    assert "text/html" in resp1.headers.get("content-type", "")
    assert "执行报告" in resp1.text
    assert "执行参数" in resp1.text
    assert "account" in resp1.text
    assert "报告档案" in resp1.text
    assert "报告用例/不支持步骤" in resp1.text
    assert "套件总数" in resp1.text
    assert "步骤总数" in resp1.text
    assert "虚拟套件" in resp1.text

    html_path = reports_dir() / f"execution_{execution_id}" / "report.html"
    assert html_path.exists()

    async with SessionLocal() as db:
        report = await db.get(Report, report_id)
        assert report.report_path is not None
        assert str(html_path) == report.report_path

    resp2 = await client.get(f"/api/reports/{report_id}/download", headers=headers)
    assert resp2.status_code == 200
    assert resp2.text == resp1.text

    _cleanup(execution_id)


async def test_report_html_suite_stats_with_setup_steps(client: AsyncClient):
    """套件级报告：套件前后置步渲染 + 三层统计（套件总数/步骤总数）+ 套件名。"""
    token, execution_id, _project_id, _case_id = await _suite_with_setup_steps(client)

    async with SessionLocal() as db:
        html_path = await report_service.render_report_html(db, execution_id)
    html = html_path.read_text(encoding="utf-8")
    assert "套件总数" in html
    assert "步骤总数" in html
    assert "登录套件" in html
    assert "套件前置" in html
    assert "套件后置" in html
    assert "套件内用例" in html

    headers = {"Authorization": f"Bearer {token}"}
    report_id = await _report_id(execution_id)
    download = await client.get(f"/api/reports/{report_id}/download", headers=headers)
    assert download.status_code == 200
    assert "套件总数" in download.text
    assert "步骤总数" in download.text
    assert "登录套件" in download.text
    assert "套件前置" in download.text

    _cleanup(execution_id)


async def test_report_file_traversal_blocked(client: AsyncClient):
    token, execution_id = await _setup(client)
    headers = {"Authorization": f"Bearer {token}"}
    report_id = await _report_id(execution_id)

    resp = await client.get(f"/api/reports/{report_id}/files/..%2f..%2fsecret.txt", headers=headers)
    assert resp.status_code == 404
    resp2 = await client.get(f"/api/reports/{report_id}/files/screenshots/nope.png", headers=headers)
    assert resp2.status_code == 404
    _cleanup(execution_id)


async def test_report_permission_denied(client: AsyncClient):
    _token, execution_id = await _setup(client)
    report_id = await _report_id(execution_id)

    await client.post("/api/auth/register", json={"username": "pytest_rp_other", "email": "rpo@t.com", "password": "x12345678"})
    other_login = await client.post("/api/auth/login", json={"username": "pytest_rp_other", "password": "x12345678"})
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    resp = await client.get(f"/api/reports/{report_id}/detail", headers=other_headers)
    assert resp.status_code == 403
    _cleanup(execution_id)


async def test_report_screenshot_resolution_is_contained():
    """CR-01：报告生成器只能读取 execution_{id}/screenshots/ 内文件。"""
    exec_id = 424242
    bad = [
        r"C:\Windows\win.ini",
        r"\\server\share\x",
        "../x.png",
        f"execution_{exec_id}/../../etc/passwd",
        f"execution_{exec_id}/screenshots/..\\..\\win.ini",
        f"execution_{exec_id}/win.ini",
        f"execution_{exec_id}/screenshots/a.exe",
        f"execution_{exec_id}/screenshots/sub/a.png",
    ]
    for key in bad:
        assert validate_object_key(exec_id, key) is False
        assert resolve_screenshot_path(exec_id, key) is None

    good = f"execution_{exec_id}/screenshots/ab12.png"
    assert validate_object_key(exec_id, good) is True
    path = resolve_screenshot_path(exec_id, good)
    assert path is not None
    assert path.is_relative_to((reports_dir() / f"execution_{exec_id}").resolve())

    # 报告详情聚合时，越界路径被置空，不产生 base64
    detail = {
        "execution": {"id": exec_id},
        "cases": [
            {"steps": [{"screenshot": "screenshots/../../win.ini"}, {"screenshot": "screenshots/a.png"}]}
        ],
    }
    report_service._embed_screenshots(detail)
    assert detail["cases"][0]["steps"][0]["screenshot_base64"] is None

    _cleanup(exec_id)


# ---------- CR-20 / CR-15：公开项目报告可发现 + 筛选契约 ----------


async def test_public_project_report_listable_by_viewer(client: AsyncClient):
    """CR-20：公开项目报告在列表可见（与详情权限一致）。"""
    token, execution_id = await _setup(client)
    headers = {"Authorization": f"Bearer {token}"}
    # 改为公开项目
    project_id = (await client.get(f"/api/executions/{execution_id}", headers=headers)).json()["project_id"]
    await client.put(f"/api/projects/{project_id}", headers=headers, json={"visibility": "public"})
    report_id = await _report_id(execution_id)

    await client.post("/api/auth/register", json={"username": "pytest_rp_viewer", "email": "rpv@t.com", "password": "x12345678"})
    viewer_login = await client.post("/api/auth/login", json={"username": "pytest_rp_viewer", "password": "x12345678"})
    viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}

    listed = await client.get("/api/reports", headers=viewer_headers)
    assert listed.status_code == 200
    assert any(r["id"] == report_id for r in listed.json()["items"])
    _cleanup(execution_id)


async def test_report_list_status_and_keyword_filters(client: AsyncClient):
    """CR-15：报告列表支持 status 过滤与按执行 ID 搜索，total 与 items 一致。"""
    token, execution_id = await _setup(client)
    headers = {"Authorization": f"Bearer {token}"}

    by_status = await client.get("/api/reports?status=passed", headers=headers)
    assert by_status.status_code == 200
    assert by_status.json()["total"] == 1

    no_match = await client.get("/api/reports?status=running", headers=headers)
    assert no_match.json()["total"] == 0

    by_keyword = await client.get(f"/api/reports?keyword={execution_id}", headers=headers)
    assert by_keyword.status_code == 200
    assert by_keyword.json()["total"] == 1
    assert by_keyword.json()["items"][0]["execution_id"] == execution_id

    bad_keyword = await client.get("/api/reports?keyword=abc", headers=headers)
    assert bad_keyword.json()["total"] == 0

    # B5：type 过滤 + project_name/device_name/finished_at 填充
    by_type = await client.get("/api/reports?type=case", headers=headers)
    assert by_type.status_code == 200
    assert by_type.json()["total"] == 1
    item = by_type.json()["items"][0]
    assert item["project_name"] == "报告测试项目"
    assert item["finished_at"] is not None

    _cleanup(execution_id)
