import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

OWNER = {"username": "pytest_suiteowner", "email": "suiteowner@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup(client: AsyncClient) -> tuple[dict, int, list[int]]:
    """注册 + 建项目 + 建 2 个用例，返回 (headers, project_id, [case_id1, case_id2])。"""
    reg = await client.post("/api/auth/register", json=OWNER)
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (await client.post("/api/projects", json={"name": "套件项目"}, headers=headers)).json()["id"]
    case_ids = []
    for name in ("用例A", "用例B"):
        c = await client.post(
            f"/api/projects/{project_id}/cases",
            json={"name": name, "steps": [{"order": 1, "action": "sleep", "params": {"duration": 1}}]},
            headers=headers,
        )
        case_ids.append(c.json()["id"])
    return headers, project_id, case_ids


async def test_suite_crud(client: AsyncClient):
    headers, project_id, case_ids = await _setup(client)

    created = await client.post(
        f"/api/projects/{project_id}/suites", json={"name": "冒烟套件"}, headers=headers
    )
    assert created.status_code == 201
    suite_id = created.json()["id"]

    listing = await client.get(f"/api/projects/{project_id}/suites", headers=headers)
    assert listing.status_code == 200
    assert any(s["id"] == suite_id for s in listing.json()["items"])

    # 添加用例
    for case_id in case_ids:
        add = await client.post(
            f"/api/suites/{suite_id}/cases", json={"case_id": case_id}, headers=headers
        )
        assert add.status_code == 201

    # 重复添加 409
    dup = await client.post(
        f"/api/suites/{suite_id}/cases", json={"case_id": case_ids[0]}, headers=headers
    )
    assert dup.status_code == 409

    # 列表
    cases = await client.get(f"/api/suites/{suite_id}/cases", headers=headers)
    assert cases.status_code == 200
    assert len(cases.json()) == 2
    assert cases.json()[0]["sort_order"] == 1

    # 重排序
    reorder = await client.put(
        f"/api/suites/{suite_id}/cases/order",
        json={"order": [case_ids[1], case_ids[0]]},
        headers=headers,
    )
    assert reorder.status_code == 204
    cases = await client.get(f"/api/suites/{suite_id}/cases", headers=headers)
    assert cases.json()[0]["case_id"] == case_ids[1]

    # 移除用例
    removed = await client.delete(
        f"/api/suites/{suite_id}/cases/{case_ids[0]}", headers=headers
    )
    assert removed.status_code == 204
    cases = await client.get(f"/api/suites/{suite_id}/cases", headers=headers)
    assert len(cases.json()) == 1

    # 套件详情含 case_count
    detail = await client.get(f"/api/suites/{suite_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["case_count"] == 1

    # 删除
    deleted = await client.delete(f"/api/suites/{suite_id}", headers=headers)
    assert deleted.status_code == 204


async def test_variable_crud(client: AsyncClient):
    headers, project_id, _ = await _setup(client)

    created = await client.post(
        "/api/variables",
        json={"scope": "project", "project_id": project_id, "name": "username", "value": "u1"},
        headers=headers,
    )
    assert created.status_code == 201
    var_id = created.json()["id"]

    # 重复 409
    dup = await client.post(
        "/api/variables",
        json={"scope": "project", "project_id": project_id, "name": "username", "value": "u2"},
        headers=headers,
    )
    assert dup.status_code == 409

    listing = await client.get(
        f"/api/variables?scope=project&project_id={project_id}", headers=headers
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["value"] == "u1"

    updated = await client.put(
        f"/api/variables/{var_id}", json={"value": "u1_new"}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["value"] == "u1_new"

    deleted = await client.delete(f"/api/variables/{var_id}", headers=headers)
    assert deleted.status_code == 204


async def test_suite_steps_with_find_text_click(client: AsyncClient):
    """Step 12：套件前后置步骤可保存新动作，缺少列表控件时拒绝。"""
    headers, project_id, _ = await _setup(client)
    el = await client.post(
        f"/api/projects/{project_id}/elements",
        json={"name": "列表", "locator_type": "id", "locator_value": "date_list"},
        headers=headers,
    )
    assert el.status_code == 201
    element_id = el.json()["id"]

    created = await client.post(
        f"/api/projects/{project_id}/suites",
        json={
            "name": "含列表查找套件",
            "setup_steps": [
                {
                    "order": 1,
                    "action": "swipe_in_element_find_text_click",
                    "element_id": element_id,
                    "params": {"target_text": "系统时间"},
                },
            ],
        },
        headers=headers,
    )
    assert created.status_code == 201
    suite_id = created.json()["id"]
    assert created.json()["setup_steps"][0]["params"]["target_text"] == "系统时间"

    # 缺少 element_id 的新动作被拒绝
    bad = await client.post(
        f"/api/projects/{project_id}/suites",
        json={
            "name": "缺列表套件",
            "setup_steps": [
                {"order": 1, "action": "swipe_in_element_find_text_click", "params": {"target_text": "x"}},
            ],
        },
        headers=headers,
    )
    assert bad.status_code == 422

    # 更新套件前后置步骤
    updated = await client.put(
        f"/api/suites/{suite_id}",
        json={
            "teardown_steps": [
                {
                    "order": 1,
                    "action": "swipe_in_element_find_text_click",
                    "element_id": element_id,
                    "params": {"target_text": "确定", "match_mode": "contains"},
                    "assertions": [],
                },
            ],
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["teardown_steps"][0]["params"]["match_mode"] == "contains"
    assert "assertions" not in updated.json()["teardown_steps"][0]


async def test_suite_steps_reject_non_empty_assertions(client: AsyncClient):
    """套件前后置仅支持动作，不能借兼容字段写入断言。"""
    headers, project_id, _ = await _setup(client)
    response = await client.post(
        f"/api/projects/{project_id}/suites",
        json={
            "name": "禁止套件断言",
            "setup_steps": [
                {
                    "order": 1,
                    "action": "sleep",
                    "params": {"duration": 1},
                    "assertions": [{"type": "element_exists"}],
                },
            ],
        },
        headers=headers,
    )
    assert response.status_code == 422
