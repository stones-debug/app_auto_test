"""模块树 scope 隔离与拖拽移动（见 docs/测试套件模块分组设计方案.md）。

覆盖：
- 用例模块树 / 套件模块树互相隔离；
- 套件挂载套件模块（含父模块选中包含子孙）与未分组契约；
- PUT /modules/{id}/position 的合法移动、循环拒绝、幂等与权限；
- 删除模块后子模块提升、套件/用例解绑；
- 模块写操作推进资产 revision。
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import Project

OWNER = {"username": "pytest_modscope_owner", "email": "modscope-owner@tl-tek.com", "password": "test123"}
OTHER = {"username": "pytest_modscope_other", "email": "modscope-other@tl-tek.com", "password": "test123"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client: AsyncClient, payload: dict) -> tuple[dict, int]:
    reg = await client.post("/api/auth/register", json=payload)
    assert reg.status_code == 201, reg.text
    token = reg.json()["access_token"]
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}"}, me.json()["id"]


async def _setup(client: AsyncClient) -> tuple[dict, int]:
    headers, _user_id = await _register(client, OWNER)
    project_id = (
        await client.post("/api/projects", json={"name": "模块项目"}, headers=headers)
    ).json()["id"]
    return headers, project_id


async def _create_module(
    client: AsyncClient,
    headers: dict,
    project_id: int,
    name: str,
    *,
    parent_id: int | None = None,
    scope: str = "case",
) -> dict:
    resp = await client.post(
        f"/api/projects/{project_id}/modules",
        json={"name": name, "parent_id": parent_id, "scope": scope},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_case(
    client: AsyncClient, headers: dict, project_id: int, name: str, module_id: int | None = None
) -> dict:
    resp = await client.post(
        f"/api/projects/{project_id}/cases",
        json={"name": name, "module_id": module_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_suite(
    client: AsyncClient, headers: dict, project_id: int, name: str, module_id: int | None = None
) -> dict:
    resp = await client.post(
        f"/api/projects/{project_id}/suites",
        json={"name": name, "module_id": module_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _project_revision(project_id: int) -> int | None:
    async with SessionLocal() as db:
        return (
            await db.scalar(
                select(Project.test_asset_revision).where(Project.id == project_id)
            )
        )


# ---------- scope 隔离 ----------


async def test_module_scope_isolation(client: AsyncClient):
    headers, project_id = await _setup(client)
    case_mod = await _create_module(client, headers, project_id, "用例模块", scope="case")
    suite_mod = await _create_module(client, headers, project_id, "套件模块", scope="suite")

    default_list = (
        await client.get(f"/api/projects/{project_id}/modules", headers=headers)
    ).json()
    assert [m["name"] for m in default_list] == ["用例模块"]
    assert default_list[0]["scope"] == "case"

    suite_list = (
        await client.get(f"/api/projects/{project_id}/modules?scope=suite", headers=headers)
    ).json()
    assert [m["name"] for m in suite_list] == ["套件模块"]
    assert suite_list[0]["scope"] == "suite"

    # 非法 scope 直接 422（Literal 校验）
    bad = await client.get(
        f"/api/projects/{project_id}/modules?scope=element", headers=headers
    )
    assert bad.status_code == 422

    # 子模块不能跨树：套件模块不能挂在用例模块下
    cross = await client.post(
        f"/api/projects/{project_id}/modules",
        json={"name": "混树子模块", "parent_id": case_mod["id"], "scope": "suite"},
        headers=headers,
    )
    assert cross.status_code == 404

    # suite 模块存在，且未被上面的失败操作波及
    after = (
        await client.get(f"/api/projects/{project_id}/modules?scope=suite", headers=headers)
    ).json()
    assert [m["id"] for m in after] == [suite_mod["id"]]


# ---------- 套件挂载与过滤 ----------


async def test_suite_module_assignment_and_filter(client: AsyncClient):
    headers, project_id = await _setup(client)
    parent = await _create_module(client, headers, project_id, "回归", scope="suite")
    child = await _create_module(
        client, headers, project_id, "回归-夜间", parent_id=parent["id"], scope="suite"
    )
    suite_parent = await _create_suite(client, headers, project_id, "冒烟", parent["id"])
    suite_child = await _create_suite(client, headers, project_id, "夜间回归", child["id"])
    suite_free = await _create_suite(client, headers, project_id, "无模块套件")

    detail = (await client.get(f"/api/suites/{suite_parent['id']}", headers=headers)).json()
    assert detail["module_id"] == parent["id"]
    assert detail["module_name"] == "回归"

    # 决策 3：选中父模块时包含子孙模块的套件
    page = (
        await client.get(
            f"/api/projects/{project_id}/suites?module_id={parent['id']}", headers=headers
        )
    ).json()
    assert {s["id"] for s in page["items"]} == {suite_parent["id"], suite_child["id"]}

    page = (
        await client.get(
            f"/api/projects/{project_id}/suites?module_id={child['id']}", headers=headers
        )
    ).json()
    assert {s["id"] for s in page["items"]} == {suite_child["id"]}

    # 未分组契约：ungrouped=true 只取 module_id IS NULL
    page = (
        await client.get(f"/api/projects/{project_id}/suites?ungrouped=true", headers=headers)
    ).json()
    assert {s["id"] for s in page["items"]} == {suite_free["id"]}

    # 摘出模块 → 未分组
    updated = await client.put(
        f"/api/suites/{suite_parent['id']}", json={"module_id": None}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["module_id"] is None
    page = (
        await client.get(f"/api/projects/{project_id}/suites?ungrouped=true", headers=headers)
    ).json()
    assert {s["id"] for s in page["items"]} == {suite_free["id"], suite_parent["id"]}


async def test_scope_cross_assign_rejected(client: AsyncClient):
    headers, project_id = await _setup(client)
    case_mod = await _create_module(client, headers, project_id, "用例模块")
    suite_mod = await _create_module(client, headers, project_id, "套件模块", scope="suite")

    # 套件不能用用例模块：结构化错误码
    suite = await client.post(
        f"/api/projects/{project_id}/suites",
        json={"name": "S", "module_id": case_mod["id"]},
        headers=headers,
    )
    assert suite.status_code == 400
    assert suite.json()["detail"]["code"] == "MODULE_SCOPE_MISMATCH"

    # 用例不能用套件模块：沿用存量 404 语义
    case = await client.post(
        f"/api/projects/{project_id}/cases",
        json={"name": "C", "module_id": suite_mod["id"]},
        headers=headers,
    )
    assert case.status_code == 404


async def test_cross_project_module_rejected(client: AsyncClient):
    headers, project_id = await _setup(client)
    other_project_id = (
        await client.post("/api/projects", json={"name": "另一个项目"}, headers=headers)
    ).json()["id"]
    foreign = await _create_module(client, headers, other_project_id, "他项目模块", scope="suite")

    suite = await client.post(
        f"/api/projects/{project_id}/suites",
        json={"name": "S", "module_id": foreign["id"]},
        headers=headers,
    )
    assert suite.status_code == 404
    assert suite.json()["detail"]["code"] == "MODULE_NOT_FOUND"

    case = await client.post(
        f"/api/projects/{project_id}/cases",
        json={"name": "C", "module_id": foreign["id"]},
        headers=headers,
    )
    assert case.status_code == 404


# ---------- 拖拽移动 ----------


async def test_move_module_reparent_and_reorder(client: AsyncClient):
    headers, project_id = await _setup(client)
    m1 = await _create_module(client, headers, project_id, "M1")
    m2 = await _create_module(client, headers, project_id, "M2")
    m3 = await _create_module(client, headers, project_id, "M3")
    child = await _create_module(client, headers, project_id, "C1", parent_id=m1["id"])

    # 拖出为根层级、插到 M3 之前：根层级顺序变为 M1, M2, C1, M3
    resp = await client.put(
        f"/api/modules/{child['id']}/position",
        json={"parent_id": None, "before_id": m3["id"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    items = {m["id"]: m for m in resp.json()}
    assert items[child["id"]]["parent_id"] is None
    assert items[child["id"]]["sort_order"] == 2
    assert items[m3["id"]]["parent_id"] is None
    assert items[m3["id"]]["sort_order"] == 3

    # 拖进 M2（追加到末尾，before_id=None）
    resp = await client.put(
        f"/api/modules/{child['id']}/position",
        json={"parent_id": m2["id"], "before_id": None},
        headers=headers,
    )
    assert resp.status_code == 200
    items = {m["id"]: m for m in resp.json()}
    assert items[child["id"]]["parent_id"] == m2["id"]
    assert items[child["id"]]["sort_order"] == 0

    # 同级重排：把 M3 挪到 M1 之前
    resp = await client.put(
        f"/api/modules/{m3['id']}/position",
        json={"parent_id": None, "before_id": m1["id"]},
        headers=headers,
    )
    assert resp.status_code == 200
    roots = sorted(
        (m for m in resp.json() if m["parent_id"] is None), key=lambda m: m["sort_order"]
    )
    assert [m["id"] for m in roots] == [m3["id"], m1["id"], m2["id"]]


async def test_move_module_rejects_invalid_targets(client: AsyncClient):
    headers, project_id = await _setup(client)
    m1 = await _create_module(client, headers, project_id, "M1")
    m2 = await _create_module(client, headers, project_id, "M2")
    child = await _create_module(client, headers, project_id, "C1", parent_id=m1["id"])
    grandchild = await _create_module(
        client, headers, project_id, "G1", parent_id=child["id"]
    )

    # 拖到自己身上
    resp = await client.put(
        f"/api/modules/{m1['id']}/position",
        json={"parent_id": m1["id"], "before_id": None},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "MODULE_PARENT_INVALID"

    # 拖到自己的子孙里（child / grandchild 都在 M1 的子树内）
    for target in (child["id"], grandchild["id"]):
        resp = await client.put(
            f"/api/modules/{m1['id']}/position",
            json={"parent_id": target, "before_id": None},
            headers=headers,
        )
        assert resp.status_code == 400, f"target={target}"
        assert resp.json()["detail"]["code"] == "MODULE_PARENT_INVALID"

    # before_id 不属于目标父级（M2 是根层级，不在 M1 之下）
    resp = await client.put(
        f"/api/modules/{child['id']}/position",
        json={"parent_id": m1["id"], "before_id": m2["id"]},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "MODULE_POSITION_INVALID"

    # before_id 不存在
    resp = await client.put(
        f"/api/modules/{child['id']}/position",
        json={"parent_id": None, "before_id": 999999},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "MODULE_POSITION_INVALID"


async def test_move_module_scope_isolation(client: AsyncClient):
    headers, project_id = await _setup(client)
    case_mod = await _create_module(client, headers, project_id, "用例模块")
    suite_mod = await _create_module(client, headers, project_id, "套件模块", scope="suite")

    # 套件模块挂到用例模块下：父级不在同 scope 集合内 → MODULE_NOT_FOUND
    resp = await client.put(
        f"/api/modules/{suite_mod['id']}/position",
        json={"parent_id": case_mod["id"], "before_id": None},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "MODULE_NOT_FOUND"

    # 反向：用例模块挂到套件模块下
    resp = await client.put(
        f"/api/modules/{case_mod['id']}/position",
        json={"parent_id": suite_mod["id"], "before_id": None},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_move_module_is_idempotent(client: AsyncClient):
    headers, project_id = await _setup(client)
    m1 = await _create_module(client, headers, project_id, "M1")
    m2 = await _create_module(client, headers, project_id, "M2")

    payload = {"parent_id": None, "before_id": m1["id"]}
    first = await client.put(
        f"/api/modules/{m2['id']}/position", json=payload, headers=headers
    )
    assert first.status_code == 200
    second = await client.put(
        f"/api/modules/{m2['id']}/position", json=payload, headers=headers
    )
    assert second.status_code == 200

    def key(items):
        return {m["id"]: (m["parent_id"], m["sort_order"]) for m in items}

    assert key(first.json()) == key(second.json())
    assert [m["id"] for m in sorted(second.json(), key=lambda m: m["sort_order"])] == [
        m2["id"],
        m1["id"],
    ]


async def test_move_module_bumps_asset_revision(client: AsyncClient):
    headers, project_id = await _setup(client)
    m1 = await _create_module(client, headers, project_id, "M1")
    m2 = await _create_module(client, headers, project_id, "M2")
    before = await _project_revision(project_id)
    assert before is not None

    resp = await client.put(
        f"/api/modules/{m2['id']}/position",
        json={"parent_id": m1["id"], "before_id": None},
        headers=headers,
    )
    assert resp.status_code == 200
    after = await _project_revision(project_id)
    assert after is not None and after > before


async def test_move_module_requires_edit_permission(client: AsyncClient):
    headers, project_id = await _setup(client)
    m1 = await _create_module(client, headers, project_id, "M1")
    m2 = await _create_module(client, headers, project_id, "M2")

    other_headers, other_id = await _register(client, OTHER)
    move_payload = {"parent_id": m1["id"], "before_id": None}
    # 非成员 → 403
    resp = await client.put(
        f"/api/modules/{m2['id']}/position",
        json=move_payload,
        headers=other_headers,
    )
    assert resp.status_code == 403

    # viewer 成员同样不能移动
    added = await client.post(
        f"/api/projects/{project_id}/members",
        json={"user_id": other_id, "role": "viewer"},
        headers=headers,
    )
    assert added.status_code == 201, added.text
    resp = await client.put(
        f"/api/modules/{m2['id']}/position",
        json=move_payload,
        headers=other_headers,
    )
    assert resp.status_code == 403
    # 权限拦截不应改动数据
    roots = (
        await client.get(f"/api/projects/{project_id}/modules", headers=headers)
    ).json()
    assert {m["id"]: m["parent_id"] for m in roots} == {m1["id"]: None, m2["id"]: None}


# ---------- 删除解绑 ----------


async def test_delete_suite_module_reparents_children_and_ungroups(client: AsyncClient):
    headers, project_id = await _setup(client)
    parent = await _create_module(client, headers, project_id, "回归", scope="suite")
    child = await _create_module(
        client, headers, project_id, "回归-夜间", parent_id=parent["id"], scope="suite"
    )
    attached = await _create_suite(client, headers, project_id, "冒烟", parent["id"])

    resp = await client.delete(f"/api/modules/{parent['id']}", headers=headers)
    assert resp.status_code == 204

    remaining = (
        await client.get(f"/api/projects/{project_id}/modules?scope=suite", headers=headers)
    ).json()
    by_id = {m["id"]: m for m in remaining}
    assert parent["id"] not in by_id
    # 子模块提升为根层级
    assert by_id[child["id"]]["parent_id"] is None

    # 套件解绑为未分组
    detail = (await client.get(f"/api/suites/{attached['id']}", headers=headers)).json()
    assert detail["module_id"] is None
    assert detail["module_name"] is None


async def test_delete_case_module_ungroups_cases(client: AsyncClient):
    headers, project_id = await _setup(client)
    parent = await _create_module(client, headers, project_id, "登录")
    case_in = await _create_case(client, headers, project_id, "登录用例", parent["id"])

    resp = await client.delete(f"/api/modules/{parent['id']}", headers=headers)
    assert resp.status_code == 204

    detail = (await client.get(f"/api/cases/{case_in['id']}", headers=headers)).json()
    assert detail["module_id"] is None


# ---------- 未分组与子孙包含（用例侧回归） ----------


async def test_ungrouped_filter_for_cases(client: AsyncClient):
    headers, project_id = await _setup(client)
    parent = await _create_module(client, headers, project_id, "登录")
    child = await _create_module(client, headers, project_id, "验证码", parent_id=parent["id"])
    case_parent_scope = await _create_case(client, headers, project_id, "登录主流程", parent["id"])
    case_child = await _create_case(client, headers, project_id, "验证码校验", child["id"])
    case_free = await _create_case(client, headers, project_id, "未分组用例")

    # 未分组（修复前这里会返回全部用例）
    page = (
        await client.get(f"/api/projects/{project_id}/cases?ungrouped=true", headers=headers)
    ).json()
    assert page["total"] == 1
    assert {c["id"] for c in page["items"]} == {case_free["id"]}

    # 父模块选中包含子孙
    page = (
        await client.get(
            f"/api/projects/{project_id}/cases?module_id={parent['id']}", headers=headers
        )
    ).json()
    assert page["total"] == 2
    assert {c["id"] for c in page["items"]} == {case_parent_scope["id"], case_child["id"]}

    # 不过滤 → 全部
    page = (await client.get(f"/api/projects/{project_id}/cases", headers=headers)).json()
    assert page["total"] == 3
