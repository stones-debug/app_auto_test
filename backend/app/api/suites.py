from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cases import _check_elements_belong
from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.models import Project, TestCase, TestModule, TestSuite, TestSuiteCase, User
from app.schemas.suite import (
    SuiteAddCaseRequest,
    SuiteCaseOut,
    SuiteCreate,
    SuiteOut,
    SuitePage,
    SuiteReorderRequest,
    SuiteUpdate,
)
from app.services.profile_revision import touch_project_asset_revision
from app.utils.pagination import get_pagination

router = APIRouter(tags=["套件管理"])


async def _get_suite_or_404(suite_id: int, db: AsyncSession) -> TestSuite:
    suite = await db.get(TestSuite, suite_id)
    if suite is None or suite.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
    return suite


@router.get("/projects/{project_id}/suites", response_model=SuitePage)
async def list_suites(
    project_id: int,
    pagination=Depends(get_pagination),
    keyword: str = "",
    status_filter: str = Query(default="", alias="status"),
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    query = select(TestSuite).where(
        TestSuite.project_id == project_id, TestSuite.deleted_at.is_(None)
    )
    if keyword:
        query = query.where(TestSuite.name.ilike(f"%{keyword}%"))
    if status_filter:
        query = query.where(TestSuite.status == status_filter)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    suites = (
        await db.execute(
            query.order_by(TestSuite.updated_at.desc())
            .offset(pagination.offset)
            .limit(pagination.limit)
        )
    ).scalars().all()

    counts = dict(
        (
            await db.execute(
                select(TestSuiteCase.suite_id, func.count(TestSuiteCase.id)).where(
                    TestSuiteCase.suite_id.in_([s.id for s in suites])
                ).group_by(TestSuiteCase.suite_id)
            )
        ).all()
    ) if suites else {}
    items = []
    for suite in suites:
        out = SuiteOut.model_validate(suite)
        out.case_count = counts.get(suite.id, 0)
        items.append(out)
    return {
        "total": total or 0,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "items": items,
    }


@router.post("/projects/{project_id}/suites", response_model=SuiteOut, status_code=status.HTTP_201_CREATED)
async def create_suite(
    project_id: int,
    body: SuiteCreate,
    project: Project = Depends(get_editable_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_elements_belong(project_id, body.setup_steps, body.teardown_steps, db)
    suite = TestSuite(
        project_id=project_id,
        name=body.name,
        description=body.description,
        created_by=user.id,
        setup_steps=[s.model_dump() for s in body.setup_steps],
        teardown_steps=[s.model_dump() for s in body.teardown_steps],
    )
    db.add(suite)
    await touch_project_asset_revision(db, project_id)
    await db.commit()
    await db.refresh(suite)
    return suite


@router.get("/suites/{suite_id}", response_model=SuiteOut)
async def get_suite(
    suite_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await _get_suite_or_404(suite_id, db)
    await get_project_permission(suite.project_id, user, db)
    count = await db.scalar(
        select(func.count(TestSuiteCase.id)).where(TestSuiteCase.suite_id == suite_id)
    )
    out = SuiteOut.model_validate(suite)
    out.case_count = count or 0
    return out


@router.put("/suites/{suite_id}", response_model=SuiteOut)
async def update_suite(
    suite_id: int,
    body: SuiteUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await _get_suite_or_404(suite_id, db)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    # CR-25：model_fields_set 区分“未提交”与“显式 null”，支持清空可选字段
    if body.setup_steps is not None or body.teardown_steps is not None:
        await _check_elements_belong(suite.project_id, body.setup_steps, body.teardown_steps, db)
    for field in ("name", "description", "status"):
        if field in body.model_fields_set:
            setattr(suite, field, getattr(body, field))
    # 方案 §2：套件前后置步骤随元数据更新并递增资产 revision
    for field in ("setup_steps", "teardown_steps"):
        if field in body.model_fields_set and getattr(body, field) is not None:
            setattr(suite, field, [s.model_dump() for s in getattr(body, field)])
    await touch_project_asset_revision(db, suite.project_id)
    await db.commit()
    await db.refresh(suite)
    return suite


@router.delete("/suites/{suite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_suite(
    suite_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await _get_suite_or_404(suite_id, db)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    suite.deleted_at = datetime.now(UTC)
    await touch_project_asset_revision(db, suite.project_id)
    await db.commit()


# ---------------- 套件用例 ----------------
@router.get("/suites/{suite_id}/cases", response_model=list[SuiteCaseOut])
async def list_suite_cases(
    suite_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await _get_suite_or_404(suite_id, db)
    await get_project_permission(suite.project_id, user, db)

    rows = (
        await db.execute(
            select(TestSuiteCase, TestCase.name, TestModule.name)
            .join(TestCase, TestCase.id == TestSuiteCase.case_id)
            .outerjoin(TestModule, TestModule.id == TestCase.module_id)
            .where(TestSuiteCase.suite_id == suite_id, TestCase.deleted_at.is_(None))
            .order_by(TestSuiteCase.sort_order)
        )
    ).all()
    items = []
    for sc, case_name, module_name in rows:
        items.append(
            SuiteCaseOut(
                id=sc.id,
                case_id=sc.case_id,
                case_name=case_name,
                module_name=module_name,
                sort_order=sc.sort_order,
            )
        )
    return items


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
    suite = await _get_suite_or_404(suite_id, db)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")

    case_ids = body.case_ids or []
    cases = (
        await db.execute(
            select(TestCase).where(
                TestCase.id.in_(case_ids),
                TestCase.project_id == suite.project_id,
                TestCase.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    cases_by_id = {case.id: case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in cases_by_id]
    if missing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"用例不存在: {missing}")

    existing_ids = set(
        (
            await db.execute(
                select(TestSuiteCase.case_id).where(
                    TestSuiteCase.suite_id == suite_id,
                    TestSuiteCase.case_id.in_(case_ids),
                )
            )
        ).scalars().all()
    )
    if existing_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"用例已在套件中: {sorted(existing_ids)}",
        )

    max_order = await db.scalar(
        select(func.max(TestSuiteCase.sort_order)).where(TestSuiteCase.suite_id == suite_id)
    )
    created: list[tuple[TestSuiteCase, TestCase]] = []
    for offset, case_id in enumerate(case_ids, start=1):
        sc = TestSuiteCase(
            suite_id=suite_id,
            case_id=case_id,
            sort_order=(max_order or 0) + offset,
        )
        db.add(sc)
        created.append((sc, cases_by_id[case_id]))
    await touch_project_asset_revision(db, suite.project_id)
    await db.commit()
    result: list[SuiteCaseOut] = []
    for sc, case in created:
        await db.refresh(sc)
        result.append(
            SuiteCaseOut(
                id=sc.id,
                case_id=sc.case_id,
                case_name=case.name,
                module_name=None,
                sort_order=sc.sort_order,
            )
        )
    return result


@router.put("/suites/{suite_id}/cases/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_suite_cases(
    suite_id: int,
    body: SuiteReorderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await _get_suite_or_404(suite_id, db)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")

    rows = (
        await db.execute(
            select(TestSuiteCase).where(TestSuiteCase.suite_id == suite_id)
        )
    ).scalars().all()
    by_case = {sc.case_id: sc for sc in rows}
    for order, case_id in enumerate(body.order, start=1):
        sc = by_case.get(case_id)
        if sc is not None:
            sc.sort_order = order
    await touch_project_asset_revision(db, suite.project_id)
    await db.commit()


@router.delete("/suites/{suite_id}/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_suite_case(
    suite_id: int,
    case_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = await _get_suite_or_404(suite_id, db)
    _project, role = await get_project_permission(suite.project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    sc = await db.execute(
        select(TestSuiteCase).where(
            TestSuiteCase.suite_id == suite_id,
            TestSuiteCase.case_id == case_id,
        )
    )
    sc = sc.scalar_one_or_none()
    if sc is not None:
        await db.delete(sc)
        await touch_project_asset_revision(db, suite.project_id)
        await db.commit()
