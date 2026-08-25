from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_project_permission,
    require_platform_admin,
    require_project_write,
)
from app.core.database import get_db
from app.models import TestCase, TestSuite, User, Variable
from app.schemas.suite import VariableCreate, VariableOut, VariableUpdate
from app.services.profile_revision import (
    touch_all_project_asset_revisions,
    touch_project_asset_revision,
)

router = APIRouter(prefix="/variables", tags=["变量管理"])


async def _touch_variable_scope(db: AsyncSession, project_id: int | None) -> None:
    if project_id is None:
        await touch_all_project_asset_revisions(db)
    else:
        await touch_project_asset_revision(db, project_id)


def _scope_filter(scope: str, project_id: int | None, suite_id: int | None, case_id: int | None):
    conditions = [Variable.scope == scope]
    if scope in ("project", "suite", "case") and project_id is not None:
        conditions.append(Variable.project_id == project_id)
    if scope == "suite" and suite_id is not None:
        conditions.append(Variable.suite_id == suite_id)
    if scope == "case" and case_id is not None:
        conditions.append(Variable.case_id == case_id)
    return conditions


async def _resolve_scope_project(
    db: AsyncSession,
    scope: str,
    project_id: int | None,
    suite_id: int | None,
    case_id: int | None,
) -> int | None:
    """CR-02：按 scope 反查变量所属项目；suite/case 归属以父级记录为准。"""
    if scope == "global":
        return None
    if scope == "project":
        if project_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="project scope 需要 project_id")
        return project_id
    if scope == "suite":
        if suite_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="suite scope 需要 suite_id")
        suite = await db.get(TestSuite, suite_id)
        if suite is None or suite.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
        return suite.project_id
    if case_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="case scope 需要 case_id")
    case = await db.get(TestCase, case_id)
    if case is None or case.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
    return case.project_id


async def _require_scope_access(
    db: AsyncSession, user: User, scope: str, project_id: int | None
) -> None:
    """global 任何登录用户可读；project/suite/case 需项目可访问（viewer 只读）。"""
    if scope == "global":
        return
    if project_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少项目上下文")
    await get_project_permission(project_id, user, db)


@router.get("", response_model=list[VariableOut])
async def list_variables(
    scope: str = "project",
    project_id: int | None = None,
    suite_id: int | None = None,
    case_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await _resolve_scope_project(db, scope, project_id, suite_id, case_id)
    await _require_scope_access(db, user, scope, resolved)
    query = select(Variable).where(*_scope_filter(scope, resolved, suite_id, case_id))
    rows = (await db.execute(query.order_by(Variable.name))).scalars().all()
    return rows


@router.post("", response_model=VariableOut, status_code=status.HTTP_201_CREATED)
async def create_variable(
    body: VariableCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved_project_id = await _resolve_scope_project(
        db, body.scope, body.project_id, body.suite_id, body.case_id
    )
    if body.scope == "global":
        # CR-02：global 仅平台管理员可写；外键一律落空（忽略前端携带的 project_id）
        await require_platform_admin()(user=user)
        project_id = suite_id = case_id = None
    else:
        project, _role = await require_project_write(resolved_project_id, user, db)
        project_id, suite_id, case_id = (
            project.id,
            body.suite_id if body.scope == "suite" else None,
            body.case_id if body.scope == "case" else None,
        )

    existing = await db.execute(
        select(Variable).where(
            Variable.scope == body.scope,
            Variable.name == body.name,
            Variable.project_id == project_id,
            (Variable.suite_id == suite_id) if suite_id is not None else Variable.suite_id.is_(None),
            (Variable.case_id == case_id) if case_id is not None else Variable.case_id.is_(None),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="变量已存在")

    var = Variable(
        scope=body.scope,
        project_id=project_id,
        suite_id=suite_id,
        case_id=case_id,
        name=body.name,
        value=body.value,
        description=body.description,
        created_by=user.id,
    )
    db.add(var)
    try:
        await _touch_variable_scope(db, project_id)
        await db.commit()
    except IntegrityError:
        # CR-02：DB 唯一约束兜底并发冲突
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="变量已存在") from None
    await db.refresh(var)
    return var


@router.put("/{variable_id}", response_model=VariableOut)
async def update_variable(
    variable_id: int,
    body: VariableUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    var = await db.get(Variable, variable_id)
    if var is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="变量不存在")
    if var.scope == "global":
        await require_platform_admin()(user=user)
    else:
        await require_project_write(var.project_id, user, db)
    if "value" in body.model_fields_set:
        var.value = body.value
    if "description" in body.model_fields_set:
        var.description = body.description
    await _touch_variable_scope(db, var.project_id)
    await db.commit()
    await db.refresh(var)
    return var


@router.delete("/{variable_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variable(
    variable_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    var = await db.get(Variable, variable_id)
    if var is None:
        return
    if var.scope == "global":
        await require_platform_admin()(user=user)
    else:
        await require_project_write(var.project_id, user, db)
    await db.delete(var)
    await _touch_variable_scope(db, var.project_id)
    await db.commit()
