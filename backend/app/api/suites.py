from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.core.errors import api_error
from app.models import Project, User
from app.schemas.suite import (
    SuiteAddCaseRequest,
    SuiteCaseOut,
    SuiteCaseVariableOverrideRequest,
    SuiteCaseVariablesOut,
    SuiteCreate,
    SuiteOut,
    SuitePage,
    SuiteReorderRequest,
    SuiteUpdate,
)
from app.services import suite_service
from app.services.profile_resolver_nodes import ProfileRuleError
from app.utils.pagination import get_pagination

router = APIRouter(tags=["套件管理"])


def _suite_out(suite, case_count: int, module_name: str | None = None) -> SuiteOut:
    result = SuiteOut.model_validate(suite)
    result.case_count = case_count
    result.module_name = module_name
    return result


@router.get("/projects/{project_id}/suites", response_model=SuitePage)
async def list_suites(
    project_id: int,
    pagination=Depends(get_pagination),
    module_id: int | None = None,
    ungrouped: bool = False,
    keyword: str = "",
    status_filter: str = Query(default="", alias="status"),
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    total, suites, counts, module_names = await suite_service.list_page(
        db,
        project_id=project_id,
        module_id=module_id,
        ungrouped=ungrouped,
        keyword=keyword,
        status=status_filter,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    return {
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "items": [
            _suite_out(
                suite,
                counts.get(suite.id, 0),
                module_names.get(suite.module_id) if suite.module_id else None,
            )
            for suite in suites
        ],
    }


@router.post("/projects/{project_id}/suites", response_model=SuiteOut, status_code=status.HTTP_201_CREATED)
async def create_suite(
    project_id: int,
    body: SuiteCreate,
    project: Project = Depends(get_editable_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await suite_service.create(db, project_id=project_id, body=body, user_id=user.id)


@router.get("/suites/{suite_id}", response_model=SuiteOut)
async def get_suite(
    suite_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await suite_service.get_or_404(db, suite_id)
    await get_project_permission(suite.project_id, user, db)
    return _suite_out(
        suite,
        await suite_service.count_cases(db, suite.id),
        await suite_service.module_name_of(db, suite),
    )


@router.put("/suites/{suite_id}", response_model=SuiteOut)
async def update_suite(
    suite_id: int,
    body: SuiteUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await suite_service.get_or_404(db, suite_id)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    return await suite_service.update(db, suite=suite, body=body)


@router.delete("/suites/{suite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_suite(
    suite_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await suite_service.get_or_404(db, suite_id)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    await suite_service.delete(db, suite=suite)


@router.get("/suites/{suite_id}/cases", response_model=list[SuiteCaseOut])
async def list_suite_cases(
    suite_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await suite_service.get_or_404(db, suite_id)
    await get_project_permission(suite.project_id, user, db)
    return [
        SuiteCaseOut(**item)
        for item in await suite_service.list_cases_with_variables(db, suite)
    ]


@router.get(
    "/suites/{suite_id}/cases/{membership_id}/variables",
    response_model=SuiteCaseVariablesOut,
)
async def get_suite_case_variables(
    suite_id: int,
    membership_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await suite_service.get_or_404(db, suite_id)
    await get_project_permission(suite.project_id, user, db)
    return await suite_service.membership_variables(
        db, suite=suite, membership_id=membership_id
    )


@router.patch(
    "/suites/{suite_id}/cases/{membership_id}/variable-overrides",
    response_model=SuiteCaseVariablesOut,
)
async def patch_suite_case_variables(
    suite_id: int,
    membership_id: int,
    body: SuiteCaseVariableOverrideRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await suite_service.get_or_404(db, suite_id)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    try:
        return await suite_service.update_membership_variables(
            db, suite=suite, membership_id=membership_id,
            updates=body.updates, user_id=user.id,
        )
    except ProfileRuleError as exc:
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, exc.message
        ) from None


@router.post(
    "/suites/{suite_id}/cases",
    response_model=list[SuiteCaseOut],
    status_code=status.HTTP_201_CREATED,
)
async def add_suite_case(
    suite_id: int,
    body: SuiteAddCaseRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await suite_service.get_or_404(db, suite_id)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    created = await suite_service.add_cases(db, suite=suite, case_ids=body.case_ids or [])
    created_ids = {relation.id for relation, _case in created}
    items = await suite_service.list_cases_with_variables(db, suite)
    return [SuiteCaseOut(**item) for item in items if item["id"] in created_ids]


@router.put("/suites/{suite_id}/cases/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_suite_cases(
    suite_id: int,
    body: SuiteReorderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await suite_service.get_or_404(db, suite_id)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    await suite_service.reorder_cases(db, suite=suite, membership_ids=body.membership_ids)


@router.delete("/suites/{suite_id}/cases/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_suite_case(
    suite_id: int,
    membership_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await suite_service.get_or_404(db, suite_id)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    await suite_service.remove_case(db, suite=suite, membership_id=membership_id)
