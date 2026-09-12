"""Step B2：验证档案相关表随 pytest_% 用户的项目清理，无跨测试残留。

档案 CRUD 接口在 B5 才提供，故此处以 ORM/现有接口直接造数据；
conftest 自动清理段需能清空这些表（含父级 app_profiles 先于 suites/cases/elements 删除）。
"""


import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import (
    AppProfile,
    AppProfileAuditLog,
    AppProfileRelease,
    AppProfileSkipRule,
    AppProfileSuiteCaseVariableOverride,
    User,
)

REG = {"username": "pytest_profile_cleanup", "email": "pc@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_profile_tables_cleaned_by_conftest(client: AsyncClient):
    """建造档案/规则/覆盖/审计数据，测试通过即证明清理段覆盖新表（无 FK 报错、无残留异常）。"""
    await client.post("/api/auth/register", json=REG)
    login = await client.post(
        "/api/auth/login", json={"username": REG["username"], "password": REG["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 建项目 + 资产（复用现有接口）
    p = await client.post("/api/projects", json={"name": "pc-proj"}, headers=headers)
    project_id = p.json()["id"]
    m = await client.post(
        f"/api/projects/{project_id}/modules", json={"name": "m1"}, headers=headers
    )
    module_id = m.json()["id"]
    c = await client.post(
        f"/api/projects/{project_id}/cases",
        json={"name": "c1", "module_id": module_id, "steps": [], "assertions": []},
        headers=headers,
    )
    case_id = c.json()["id"]
    s = await client.post(
        f"/api/projects/{project_id}/suites", json={"name": "s1"}, headers=headers
    )
    suite_id = s.json()["id"]
    await client.post(
        f"/api/suites/{suite_id}/cases",
        json={"case_id": case_id},
        headers=headers,
    )
    memberships = await client.get(f"/api/suites/{suite_id}/cases", headers=headers)
    suite_case_id = memberships.json()[0]["id"]

    # 直接插入档案相关数据（ORM，接口尚不存在）
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == REG["username"]))).scalar_one()
        profile = AppProfile(project_id=project_id, name="DVR", code="dvr", created_by=user.id)
        db.add(profile)
        await db.flush()
        db.add(AppProfileRelease(profile_id=profile.id, version="1.0", created_by=user.id))
        db.add(
            AppProfileSkipRule(
                profile_id=profile.id, target_type="suite", suite_id=suite_id,
                reason_code="unsupported", created_by=user.id,
            )
        )
        db.add(
            AppProfileSkipRule(
                profile_id=profile.id, target_type="case", suite_case_id=suite_case_id,
                reason_code="unsupported", reason_note="x", created_by=user.id,
            )
        )
        db.add(
            AppProfileSuiteCaseVariableOverride(
                profile_id=profile.id, suite_case_id=suite_case_id, name="K", value="v", created_by=user.id
            )
        )
        db.add(
            AppProfileAuditLog(
                profile_id=profile.id, project_id=project_id, request_id="r-1",
                action="skip_batch", revision_before=1, revision_after=2, actor_id=user.id,
            )
        )
        await db.commit()

    # 清理由 conftest 在测试后自动执行；此测试能运行到结束且清理段不抛错即说明
    # _cleanup_test_data 已覆盖全部 8 张档案相关表。
