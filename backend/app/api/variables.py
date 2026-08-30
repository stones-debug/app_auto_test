from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    require_platform_admin,
    require_project_write,
)
from app.core.database import get_db
from app.models import User
from app.schemas.suite import VariableCreate, VariableOut, VariableUpdate
from app.services import variable_service

router = APIRouter(prefix="/variables", tags=["变量管理"])


@router.get("", response_model=list[VariableOut])
async def list_variables(
    scope: str = "project",
    project_id: int | None = None,
    suite_id: int | None = None,
    case_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await variable_service.resolve_scope_project(
        db,
        scope=scope,
        project_id=project_id,
        suite_id=suite_id,
        case_id=case_id,
    )
    await variable_service.require_scope_access(
        db, user=user, scope=scope, project_id=resolved
    )
    return await variable_service.list_variables(
        db,
        scope=scope,
        project_id=resolved,
        suite_id=suite_id,
        case_id=case_id,
    )


@router.post("", response_model=VariableOut, status_code=status.HTTP_201_CREATED)
async def create_variable(
    body: VariableCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved_project_id = await variable_service.resolve_scope_project(
        db,
        scope=body.scope,
        project_id=body.project_id,
        suite_id=body.suite_id,
        case_id=body.case_id,
    )
    if body.scope == "global":
        await require_platform_admin()(user=user)
    else:
        if resolved_project_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="变量作用域缺少项目")
        project, _role = await require_project_write(resolved_project_id, user, db)
        resolved_project_id = project.id
    return await variable_service.create(
        db, body=body, project_id=resolved_project_id, user=user
    )


@router.put("/{variable_id}", response_model=VariableOut)
async def update_variable(
    variable_id: int,
    body: VariableUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    variable = await variable_service.get_or_none(db, variable_id)
    if variable is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="变量不存在")
    if variable.scope == "global":
        await require_platform_admin()(user=user)
    else:
        if variable.project_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="变量作用域缺少项目")
        await require_project_write(variable.project_id, user, db)
    return await variable_service.update(db, variable=variable, body=body)


@router.delete("/{variable_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variable(
    variable_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    variable = await variable_service.get_or_none(db, variable_id)
    if variable is None:
        return
    if variable.scope == "global":
        await require_platform_admin()(user=user)
    else:
        if variable.project_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="变量作用域缺少项目")
        await require_project_write(variable.project_id, user, db)
    await variable_service.delete(db, variable=variable)
