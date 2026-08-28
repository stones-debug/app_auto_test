from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.models import Execution, Project, TestCase, TestElement, TestModule, User
from app.schemas.case import (
    CaseBatchDeleteRequest,
    CaseBatchDeleteResponse,
    CaseCreate,
    CaseListItem,
    CaseOut,
    CasePage,
    CaseUpdate,
)
from app.services.profile_revision import touch_project_asset_revision
from app.utils.pagination import get_pagination

router = APIRouter(tags=["用例管理"])

def _ids_from_query_values(values: list[str]) -> list[int]:
    result: list[int] = []
    for raw in values:
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                result.append(int(part))
    return result



async def _get_case_or_404(case_id: int, db: AsyncSession) -> TestCase:
    case = await db.get(TestCase, case_id)
    if case is None or case.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
    return case


async def _check_module_belongs(project_id: int, module_id: int | None, db: AsyncSession) -> None:
    if module_id is None:
        return
    module = await db.get(TestModule, module_id)
    if module is None or module.deleted_at is not None or module.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模块不存在")


def _nested_assertions(steps: list | None):
    for step in steps or []:
        assertions = step.get("assertions", []) if isinstance(step, dict) else getattr(step, "assertions", [])
        yield from assertions or []


async def _check_elements_belong(
    project_id: int,
    steps: list | None,
    assertions_or_db: list | AsyncSession | None = None,
    db: AsyncSession | None = None,
) -> None:
    """CR-09：步骤/断言引用的 element_id 必须存在且属于当前项目（未删除）。"""
    # 套件 API 仍以 (setup_steps, teardown_steps, db) 调用；用例 API 使用
    # (steps, db)。两种入口都归一化为嵌套步骤断言集合。
    if db is None:
        db = assertions_or_db  # type: ignore[assignment]
        extra: list = []
    else:
        extra = assertions_or_db if isinstance(assertions_or_db, list) else []
    ids: set[int] = set()
    for item in [*(steps or []), *extra, *_nested_assertions(steps), *_nested_assertions(extra)]:
        if isinstance(item, dict):
            element_id = item.get("element_id")
        else:
            element_id = getattr(item, "element_id", None)
        if element_id is not None:
            ids.add(int(element_id))
    if not ids:
        return
    rows = (
        await db.execute(
            select(TestElement).where(
                TestElement.id.in_(ids), TestElement.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    found = {r.id for r in rows}
    missing = ids - found
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"元素不存在: {sorted(missing)}"
        )
    cross = [r.id for r in rows if r.project_id != project_id]
    if cross:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"元素不属于该项目: {sorted(cross)}"
        )


async def _check_orders_unique(steps: list | None) -> None:
    """同一阶段内步骤、同一步骤内断言 order 不得重复。"""
    step_orders = [
        (str(s.get("phase") or "main"), int(s["order"]))
        for s in (steps or [])
        if isinstance(s, dict) and "order" in s
    ]
    if len(step_orders) != len(set(step_orders)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="同一阶段内步骤 order 不得重复"
        )
    for step in steps or []:
        assertions = step.get("assertions", []) if isinstance(step, dict) else []
        orders = [int(a["order"]) for a in assertions if isinstance(a, dict) and "order" in a]
        if len(orders) != len(set(orders)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="同一步骤内断言 order 不得重复"
            )


def _check_keys_unique(steps: list | None) -> None:
    """方案 §2.8：用例内步骤/断言稳定 key 不得重复（NODE_KEY_DUPLICATED）。"""
    keys = [s.get("key") for s in (steps or []) if isinstance(s, dict) and s.get("key")]
    keys += [a.get("key") for a in _nested_assertions(steps) if isinstance(a, dict) and a.get("key")]
    if len(keys) != len(set(keys)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="用例内步骤/断言 key 不得重复"
        )


@router.get("/projects/{project_id}/cases", response_model=CasePage)
async def list_cases(
    project_id: int,
    pagination=Depends(get_pagination),
    module_id: int | None = None,
    keyword: str = "",
    status: str = "",
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    query = select(TestCase).where(
        TestCase.project_id == project_id,
        TestCase.deleted_at.is_(None),
    )
    count_query = select(func.count()).select_from(TestCase).where(
        TestCase.project_id == project_id,
        TestCase.deleted_at.is_(None),
    )
    if module_id is not None:
        query = query.where(TestCase.module_id == module_id)
        count_query = count_query.where(TestCase.module_id == module_id)
    if keyword:
        query = query.where(TestCase.name.ilike(f"%{keyword}%"))
        count_query = count_query.where(TestCase.name.ilike(f"%{keyword}%"))
    if status:
        query = query.where(TestCase.status == status)
        count_query = count_query.where(TestCase.status == status)

    total = await db.scalar(count_query)
    rows = (
        await db.execute(
            query.order_by(TestCase.updated_at.desc())
            .offset(pagination.offset)
            .limit(pagination.limit)
        )
    ).scalars().all()

    # 填充 module_name
    module_ids = {r.module_id for r in rows if r.module_id}
    modules: dict[int, str] = {}
    if module_ids:
        mod_rows = (
            await db.execute(select(TestModule).where(TestModule.id.in_(module_ids)))
        ).scalars().all()
        modules = {m.id: m.name for m in mod_rows}

    # 最近一次执行状态（B3）：type=case 且 case_id 匹配，取最新一条
    case_ids = [r.id for r in rows]
    last_exec: dict[int, tuple[str, datetime]] = {}
    if case_ids:
        exec_rows = (
            await db.execute(
                select(Execution.case_id, Execution.status, Execution.created_at)
                .where(Execution.type == "case", Execution.case_id.in_(case_ids))
                .order_by(Execution.created_at.desc())
            )
        ).all()
        seen: set[int] = set()
        for cid, st, created in exec_rows:
            if cid not in seen:
                last_exec[cid] = (st, created)
                seen.add(cid)

    items = []
    for case in rows:
        item = CaseListItem.model_validate(case)
        item.module_name = modules.get(case.module_id) if case.module_id else None
        item.step_count = len(case.steps or [])
        item.assertion_count = sum(len(s.get("assertions") or []) for s in (case.steps or []))
        if case.id in last_exec:
            item.last_execution_status, item.last_execution_at = last_exec[case.id]
        items.append(item)
    return {"total": total or 0, "page": pagination.page, "page_size": pagination.page_size, "items": items}


@router.post("/projects/{project_id}/cases", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def create_case(
    project_id: int,
    body: CaseCreate,
    project: Project = Depends(get_editable_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_module_belongs(project_id, body.module_id, db)
    await _check_elements_belong(project_id, body.steps, db)
    await _check_orders_unique(body.steps)
    _check_keys_unique(body.steps)
    case = TestCase(
        project_id=project_id,
        module_id=body.module_id,
        name=body.name,
        description=body.description,
        status=body.status,
        steps=body.steps,
        variables=body.variables,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(case)
    await touch_project_asset_revision(db, project_id)
    await db.commit()
    await db.refresh(case)
    return case


@router.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case_or_404(case_id, db)
    await get_project_permission(case.project_id, user, db)
    return case


@router.put("/cases/{case_id}", response_model=CaseOut)
async def update_case(
    case_id: int,
    body: CaseUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case_or_404(case_id, db)
    _project, role = await get_project_permission(case.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    await _check_module_belongs(case.project_id, body.module_id, db)
    if body.steps is not None:
        await _check_elements_belong(case.project_id, body.steps, db)
    await _check_orders_unique(body.steps)
    _check_keys_unique(body.steps)
    for field in ("name", "module_id", "description", "status", "steps", "variables"):
        # CR-25：model_fields_set 区分“未提交”与“显式 null”，支持清空可选字段
        if field in body.model_fields_set:
            setattr(case, field, getattr(body, field))
    case.updated_by = user.id
    await touch_project_asset_revision(db, case.project_id)
    await db.commit()
    await db.refresh(case)
    return case



async def _batch_delete_cases(
    project_id: int,
    ids: list[int],
    user: User,
    db: AsyncSession,
) -> list[TestCase]:
    _project, role = await get_project_permission(project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")

    rows = (
        await db.execute(
            select(TestCase).where(
                TestCase.id.in_(ids),
                TestCase.project_id == project_id,
                TestCase.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    by_id = {case.id: case for case in rows}
    ordered = [by_id[i] for i in ids if i in by_id]
    now = datetime.now(UTC)
    for case in ordered:
        case.deleted_at = now
    if ordered:
        await touch_project_asset_revision(db, project_id)
        await db.commit()
    return ordered


@router.post("/projects/{project_id}/cases/batch", response_model=CaseBatchDeleteResponse)
@router.post("/projects/{project_id}/cases/batch-delete", response_model=CaseBatchDeleteResponse)
async def batch_delete_cases(
    project_id: int,
    body: CaseBatchDeleteRequest | None = None,
    ids: list[str] = Query(default=[]),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    delete_ids = body.ids if body is not None and body.ids else _ids_from_query_values(ids)
    if not delete_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请提供至少一个用例 ID",
        )
    rows = await _batch_delete_cases(project_id, delete_ids, user, db)
    return {
        "deleted": len(rows),
        "deleted_count": len(rows),
        "ids": [case.id for case in rows],
    }


@router.delete("/projects/{project_id}/cases/batch-delete", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/projects/{project_id}/cases/batch", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/projects/{project_id}/cases", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cases_batch(
    project_id: int,
    body: CaseBatchDeleteRequest | None = None,
    ids: list[str] = Query(default=[]),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    delete_ids = body.ids if body is not None and body.ids else _ids_from_query_values(ids)
    if not delete_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请提供至少一个用例 ID",
        )
    await _batch_delete_cases(project_id, delete_ids, user, db)


async def _batch_delete_cases_global(
    ids: list[int],
    user: User,
    db: AsyncSession,
) -> list[TestCase]:
    rows = (
        await db.execute(
            select(TestCase).where(
                TestCase.id.in_(ids),
                TestCase.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if not rows:
        return []
    by_id = {case.id: case for case in rows}
    ordered = [by_id[i] for i in ids if i in by_id]
    project_ids = {case.project_id for case in ordered}
    for pid in project_ids:
        _project, role = await get_project_permission(pid, user, db)
        if role not in ("owner", "admin", "member"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    now = datetime.now(UTC)
    for case in ordered:
        case.deleted_at = now
    for pid in project_ids:
        await touch_project_asset_revision(db, pid)
    await db.commit()
    return ordered


@router.post("/cases/batch", response_model=CaseBatchDeleteResponse)
@router.post("/cases/batch-delete", response_model=CaseBatchDeleteResponse)
async def batch_delete_cases_global(
    body: CaseBatchDeleteRequest | None = None,
    ids: list[str] = Query(default=[]),
    project_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    delete_ids = body.ids if body is not None and body.ids else _ids_from_query_values(ids)
    if not delete_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请提供至少一个用例 ID",
        )
    effective_project_id = (body.project_id if body is not None else None) or project_id
    if effective_project_id is not None:
        rows = await _batch_delete_cases(effective_project_id, delete_ids, user, db)
    else:
        rows = await _batch_delete_cases_global(delete_ids, user, db)
    return {
        "deleted": len(rows),
        "deleted_count": len(rows),
        "ids": [case.id for case in rows],
    }


@router.delete("/cases/batch-delete", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/cases/batch", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cases_batch_global(
    body: CaseBatchDeleteRequest | None = None,
    ids: list[str] = Query(default=[]),
    project_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    delete_ids = body.ids if body is not None and body.ids else _ids_from_query_values(ids)
    if not delete_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请提供至少一个用例 ID",
        )
    effective_project_id = (body.project_id if body is not None else None) or project_id
    if effective_project_id is not None:
        await _batch_delete_cases(effective_project_id, delete_ids, user, db)
    else:
        await _batch_delete_cases_global(delete_ids, user, db)


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = await _get_case_or_404(case_id, db)
    _project, role = await get_project_permission(case.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    case.deleted_at = datetime.now(UTC)
    await touch_project_asset_revision(db, case.project_id)
    await db.commit()


@router.post("/cases/{case_id}/clone", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def clone_case(
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """克隆用例：复制步骤（含步骤断言）和变量，并重建全部节点 key。"""
    case = await _get_case_or_404(case_id, db)
    _project, role = await get_project_permission(case.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")

    cloned_steps = deepcopy(case.steps or [])
    for step in cloned_steps:
        step["key"] = str(uuid4())
        for assertion in step.get("assertions") or []:
            assertion["key"] = str(uuid4())
    new_case = TestCase(
        project_id=case.project_id,
        module_id=case.module_id,
        name=f"{case.name} (副本)",
        description=case.description,
        status="draft",
        steps=cloned_steps,
        variables=dict(case.variables or {}),
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(new_case)
    await touch_project_asset_revision(db, case.project_id)
    await db.commit()
    await db.refresh(new_case)
    return new_case
