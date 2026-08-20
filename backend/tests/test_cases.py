import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

OWNER = {"username": "pytest_caseowner", "email": "caseowner@tl-tek.com", "password": "test123"}

STEPS = [
    {"order": 1, "action": "launch_app", "params": {"package": "com.demo.app"}, "description": "启动应用"},
    {"order": 2, "action": "click", "element_id": 1001, "params": {"wait_timeout": 10}},
]
ASSERTIONS = [
    {"order": 1, "type": "element_exists", "element_id": 1003, "params": {"expected": True}},
]


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup(client: AsyncClient) -> tuple[dict, int]:
    reg = await client.post("/api/auth/register", json=OWNER)
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post("/api/projects", json={"name": "用例项目"}, headers=headers)
    return headers, created.json()["id"]


async def test_case_crud(client: AsyncClient):
    headers, project_id = await _setup(client)

    created = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "登录用例",
            "description": "测试登录",
            "status": "active",
            "steps": STEPS,
            "assertions": ASSERTIONS,
            "variables": {"username": "u1"},
        },
        headers=headers,
    )
    assert created.status_code == 201
    case_id = created.json()["id"]
    assert len(created.json()["steps"]) == 2

    # 列表 + 关键字筛选
    listing = await client.get(
        f"/api/projects/{project_id}/cases?keyword=登录", headers=headers
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    # 详情
    detail = await client.get(f"/api/cases/{case_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["variables"]["username"] == "u1"

    # 更新
    updated = await client.put(
        f"/api/cases/{case_id}",
        json={"name": "登录用例V2", "steps": STEPS + [{"order": 3, "action": "sleep", "params": {"duration": 1}}]},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "登录用例V2"
    assert len(updated.json()["steps"]) == 3

    # 克隆
    cloned = await client.post(f"/api/cases/{case_id}/clone", headers=headers)
    assert cloned.status_code == 201
    assert "副本" in cloned.json()["name"]
    assert cloned.json()["status"] == "draft"
    assert len(cloned.json()["steps"]) == 3

    # 删除
    deleted = await client.delete(f"/api/cases/{case_id}", headers=headers)
    assert deleted.status_code == 204
    gone = await client.get(f"/api/cases/{case_id}", headers=headers)
    assert gone.status_code == 404


async def test_case_jsonb_validation(client: AsyncClient):
    """用例可按 JSONB 结构校验 steps。"""
    headers, project_id = await _setup(client)
    created = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "断言用例",
            "assertions": [{"order": 1, "type": "text_equals", "params": {"expected": "成功"}}],
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["assertions"][0]["type"] == "text_equals"
