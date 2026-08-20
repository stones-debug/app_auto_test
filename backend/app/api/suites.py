from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.models import Project, TestCase, TestModule, TestSuite, TestSuiteCase, User
from app.schemas.suite import (
    SuiteAddCaseRequest,
    SuiteCaseOut,
    SuiteCreate,
    SuiteOut,
    SuiteReorderRequest,
    SuiteUpdate,
)

router = APIRouter(tags=["套件管理"])


async def _get_suite_or_404(suite_id: int, db: AsyncSession) -> TestSuite:
    suite = await db.get(TestSuite, suite_id)
    if suite is None or suite.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
    return suite


@router.get("/projects/{project_id}/suites", response_model=list[SuiteOut])
async def list_suites(
    project_id: int,
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    suites = (
        await db.execute(
            select(TestSuite)
            .where(TestSuite.project_id == project_id, TestSuite.deleted_at.is_(None))
            .order_by(TestSuite.updated_at.desc())
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
    return items


@router.post("/projects/{project_id}/suites", response_model=SuiteOut, status_code=status.HTTP_201_CREATED)
async def create_suite(
    project_id: int,
    body: SuiteCreate,
    project: Project = Depends(get_editable_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suite = TestSuite(
        project_id=project_id,
        name=body.name,
        description=body.description,
        created_by=user.id,
    )
    db.add(suite)
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
    if body.name is not None:
        suite.name = body.name
    if body.description is not None:
        suite.description = body.description
    if body.status is not None:
        suite.status = body.status
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
    response_model=SuiteCaseOut,
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

    case = await db.get(TestCase, body.case_id)
    if case is None or case.deleted_at is not None or case.project_id != suite.project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")

    existing = await db.execute(
        select(TestSuiteCase).where(
            TestSuiteCase.suite_id == suite_id,
            TestSuiteCase.case_id == body.case_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该用例已在套件中")

    max_order = await db.scalar(
        select(func.max(TestSuiteCase.sort_order)).where(TestSuiteCase.suite_id == suite_id)
    )
    sc = TestSuiteCase(suite_id=suite_id, case_id=body.case_id, sort_order=(max_order or 0) + 1)
    db.add(sc)
    await db.commit()
    await db.refresh(sc)
    return SuiteCaseOut(
        id=sc.id,
        case_id=sc.case_id,
        case_name=case.name,
        module_name=None,
        sort_order=sc.sort_order,
    )


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
        await db.commit()
