from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import User, Variable
from app.schemas.suite import VariableCreate, VariableOut, VariableUpdate

router = APIRouter(prefix="/variables", tags=["变量管理"])


def _scope_filter(scope: str, project_id: int | None, suite_id: int | None, case_id: int | None):
    conditions = [Variable.scope == scope]
    if scope in ("project", "suite", "case") and project_id is not None:
        conditions.append(Variable.project_id == project_id)
    if scope == "suite" and suite_id is not None:
        conditions.append(Variable.suite_id == suite_id)
    if scope == "case" and case_id is not None:
        conditions.append(Variable.case_id == case_id)
    return conditions


@router.get("", response_model=list[VariableOut])
async def list_variables(
    scope: str = "project",
    project_id: int | None = None,
    suite_id: int | None = None,
    case_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Variable).where(*_scope_filter(scope, project_id, suite_id, case_id))
    rows = (await db.execute(query.order_by(Variable.name))).scalars().all()
    return rows


@router.post("", response_model=VariableOut, status_code=status.HTTP_201_CREATED)
async def create_variable(
    body: VariableCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(Variable).where(
            Variable.scope == body.scope,
            Variable.name == body.name,
            Variable.project_id == body.project_id,
            (Variable.suite_id == body.suite_id) if body.suite_id is not None else Variable.suite_id.is_(None),
            (Variable.case_id == body.case_id) if body.case_id is not None else Variable.case_id.is_(None),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="变量已存在")

    var = Variable(
        scope=body.scope,
        project_id=body.project_id,
        suite_id=body.suite_id,
        case_id=body.case_id,
        name=body.name,
        value=body.value,
        description=body.description,
        created_by=user.id,
    )
    db.add(var)
    await db.commit()
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
    if body.value is not None:
        var.value = body.value
    if body.description is not None:
        var.description = body.description
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
    if var is not None:
        await db.delete(var)
        await db.commit()
