"""变量作用域校验和变量写事务。"""

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project_permission
from app.models import User, Variable
from app.repositories import variables as variables_repo
from app.schemas.suite import VariableCreate, VariableUpdate
from app.services import asset_service
from app.services.random_variables import validate_definition


async def resolve_scope_project(
    db: AsyncSession,
    *,
    scope: str,
    project_id: int | None,
    suite_id: int | None,
    case_id: int | None,
) -> int | None:
    if scope == "global":
        return None
    if scope == "project":
        if project_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="project scope 需要 project_id")
        return project_id
    if scope == "suite":
        if suite_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="suite scope 需要 suite_id")
        suite = await variables_repo.get_suite(db, suite_id)
        if suite is None or suite.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
        return suite.project_id
    if case_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="case scope 需要 case_id")
    case = await variables_repo.get_case(db, case_id)
    if case is None or case.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
    return case.project_id


async def list_variables(
    db: AsyncSession,
    *,
    scope: str,
    project_id: int | None,
    suite_id: int | None,
    case_id: int | None,
) -> list[Variable]:
    return await variables_repo.list_scoped(
        db,
        scope=scope,
        project_id=project_id,
        suite_id=suite_id,
        case_id=case_id,
    )


async def require_scope_access(
    db: AsyncSession, *, user: User, scope: str, project_id: int | None
) -> None:
    if scope == "global":
        return
    if project_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少项目上下文")
    await get_project_permission(project_id, user, db)


async def get_or_none(db: AsyncSession, variable_id: int) -> Variable | None:
    return await variables_repo.get_by_id(db, variable_id)


async def create(
    db: AsyncSession, *, body: VariableCreate, project_id: int | None, user: User
) -> Variable:
    target_project_id = project_id if body.scope != "global" else None
    target_suite_id = body.suite_id if body.scope == "suite" else None
    target_case_id = body.case_id if body.scope == "case" else None
    if await variables_repo.find_same_scope(
        db,
        scope=body.scope,
        name=body.name,
        project_id=target_project_id,
        suite_id=target_suite_id,
        case_id=target_case_id,
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="变量已存在")
    variable = await variables_repo.create(
        db,
        scope=body.scope,
        project_id=target_project_id,
        suite_id=target_suite_id,
        case_id=target_case_id,
        name=body.name,
        value=body.value,
        kind=body.kind,
        spec=body.spec,
        description=body.description,
        user_id=user.id,
    )
    try:
        if target_project_id is None:
            await asset_service.commit_global_asset_change(db)
        else:
            await asset_service.commit_asset_change(db, [target_project_id])
        await variables_repo.refresh(db, variable)
        return variable
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="变量已存在") from None
    except Exception:
        await db.rollback()
        raise


async def update(db: AsyncSession, *, variable: Variable, body: VariableUpdate) -> Variable:
    merged_kind = (body.kind if "kind" in body.model_fields_set else variable.kind) or "fixed"
    merged_value = body.value if "value" in body.model_fields_set and body.value is not None else variable.value
    merged_spec = body.spec if "spec" in body.model_fields_set else variable.spec
    # A kind transition can be expressed as a partial update while still
    # producing one valid definition; the mutually-exclusive field is reset.
    if "kind" in body.model_fields_set and "value" not in body.model_fields_set and merged_kind != "fixed":
        merged_value = ""
    if "kind" in body.model_fields_set and "spec" not in body.model_fields_set and merged_kind == "fixed":
        merged_spec = None
    validate_definition(merged_kind, merged_value, merged_spec)
    fields = set(body.model_fields_set)
    if "kind" in fields and merged_kind != "fixed" and "value" not in fields:
        variable.value = ""
    if "kind" in fields and merged_kind == "fixed" and "spec" not in fields:
        variable.spec = None
    await variables_repo.update_fields(
        variable,
        fields=body.model_fields_set,
        value=body.value,
        kind=body.kind,
        spec=body.spec,
        description=body.description,
    )
    try:
        if variable.project_id is None:
            await asset_service.commit_global_asset_change(db)
        else:
            await asset_service.commit_asset_change(db, [variable.project_id])
        await variables_repo.refresh(db, variable)
        return variable
    except Exception:
        await db.rollback()
        raise


async def delete(db: AsyncSession, *, variable: Variable) -> None:
    try:
        await variables_repo.delete(db, variable)
        if variable.project_id is None:
            await asset_service.commit_global_asset_change(db)
        else:
            await asset_service.commit_asset_change(db, [variable.project_id])
    except Exception:
        await db.rollback()
        raise
