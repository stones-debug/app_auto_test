"""Step B3：用例步骤/断言稳定 key（UUID）行为验证。

覆盖：创建兜底生成、重复 key 拒绝、克隆重新生成、加载返回 key。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

OWNER = {"username": "pytest_casekey", "email": "casekey@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup(client: AsyncClient) -> tuple[dict, int, int]:
    reg = await client.post("/api/auth/register", json=OWNER)
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (await client.post("/api/projects", json={"name": "key项目"}, headers=headers)).json()["id"]
    element_id = (
        await client.post(
            f"/api/projects/{project_id}/elements",
            json={"name": "按钮", "locator_type": "id", "locator_value": "btn"},
            headers=headers,
        )
    ).json()["id"]
    return headers, project_id, element_id


async def test_create_backfills_key(client: AsyncClient):
    """不传 key 时后端兜底生成 UUID，并在响应返回。"""
    import uuid

    headers, project_id, element_id = await _setup(client)
    resp = await client.post(
        f"/api/projects/{project_id}/cases",
        # 断言已下沉到步骤内（StepCreate.assertions），不再是用例级字段
        json={
            "name": "兜底key",
            "steps": [
                {
                    "order": 1,
                    "action": "click",
                    "element_id": element_id,
                    "params": {},
                    "assertions": [
                        {"order": 1, "type": "element_exists", "element_id": element_id, "params": {}}
                    ],
                }
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    step_key = data["steps"][0]["key"]
    assertion_key = data["steps"][0]["assertions"][0]["key"]
    uuid.UUID(step_key)
    uuid.UUID(assertion_key)
    assert step_key != assertion_key


async def test_duplicate_key_rejected(client: AsyncClient):
    """用例内合法 UUID key 重复返回 400；非法 key 返回 422。"""
    import uuid

    headers, project_id, element_id = await _setup(client)
    dup = str(uuid.uuid4())
    resp = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "重复key",
            "steps": [
                {"order": 1, "action": "sleep", "key": dup, "params": {"duration": 1}},
                {"order": 2, "action": "sleep", "key": dup, "params": {"duration": 2}},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "key" in resp.json()["detail"]

    # 非法 key（非 UUID）直接拒绝
    invalid = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "非法key",
            "steps": [{"order": 1, "action": "sleep", "key": "k1", "params": {"duration": 1}}],
        },
        headers=headers,
    )
    assert invalid.status_code == 422


async def test_clone_regenerates_keys(client: AsyncClient):
    """克隆用例为所有节点重新生成 key。"""
    headers, project_id, element_id = await _setup(client)
    created = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "原用例",
            "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}],
        },
        headers=headers,
    )
    case_id = created.json()["id"]
    original_key = created.json()["steps"][0]["key"]

    cloned = await client.post(f"/api/cases/{case_id}/clone", headers=headers)
    assert cloned.status_code == 201
    assert cloned.json()["steps"][0]["key"] != original_key


async def test_get_returns_keys(client: AsyncClient):
    """加载用例返回带 key 的步骤/断言。"""
    headers, project_id, element_id = await _setup(client)
    created = await client.post(
        f"/api/projects/{project_id}/cases",
        json={
            "name": "取回key",
            "steps": [{"order": 1, "action": "click", "element_id": element_id, "params": {}}],
        },
        headers=headers,
    )
    case_id = created.json()["id"]
    got = await client.get(f"/api/cases/{case_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["steps"][0]["key"]
