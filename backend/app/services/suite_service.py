"""测试套件及套件用例关系的业务规则与事务编排。"""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestSuite
from app.repositories import suites as suites_repo
from app.schemas.suite import SuiteCreate, SuiteUpdate
from app.services import asset_service
from app.services.case_service import validate_elements


async def _rollback_on_error(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        pass


async def get_or_404(db: AsyncSession, suite_id: int) -> TestSuite:
    suite = await suites_repo.get_by_id(db, suite_id)
    if suite is None or suite.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
    return suite


async def list_page(db: AsyncSession, **filters):
    return await suites_repo.list_page(db, **filters)


async def create(db: AsyncSession, *, project_id: int, body: SuiteCreate, user_id: int) -> TestSuite:
    setup_steps = [step.model_dump() for step in body.setup_steps]
    teardown_steps = [step.model_dump() for step in body.teardown_steps]
    await validate_elements(
        db, project_id=project_id, steps=setup_steps, extra_steps=teardown_steps
    )
    try:
        suite = await suites_repo.create(
            db,
            project_id=project_id,
            name=body.name,
            description=body.description,
            setup_steps=setup_steps,
            teardown_steps=teardown_steps,
            user_id=user_id,
        )
        await asset_service.commit_asset_change(db, [project_id])
        await suites_repo.refresh(db, suite)
        return suite
    except Exception:
        await _rollback_on_error(db)
        raise


async def update(db: AsyncSession, *, suite: TestSuite, body: SuiteUpdate) -> TestSuite:
    setup_steps = (
        [step.model_dump() for step in body.setup_steps]
        if body.setup_steps is not None
        else None
    )
    teardown_steps = (
        [step.model_dump() for step in body.teardown_steps]
        if body.teardown_steps is not None
        else None
    )
    if setup_steps is not None or teardown_steps is not None:
        await validate_elements(
            db,
            project_id=suite.project_id,
            steps=setup_steps,
            extra_steps=teardown_steps,
        )
    fields = {}
    for field in ("name", "description", "status"):
        if field in body.model_fields_set:
            fields[field] = getattr(body, field)
    if setup_steps is not None:
        fields["setup_steps"] = setup_steps
    if teardown_steps is not None:
        fields["teardown_steps"] = teardown_steps
    try:
        await suites_repo.update_fields(suite, fields=fields)
        await asset_service.commit_asset_change(db, [suite.project_id])
        await suites_repo.refresh(db, suite)
        return suite
    except Exception:
        await _rollback_on_error(db)
        raise


async def delete(db: AsyncSession, *, suite: TestSuite) -> None:
    try:
        await suites_repo.soft_delete(suite, datetime.now(UTC))
        await asset_service.commit_asset_change(db, [suite.project_id])
    except Exception:
        await _rollback_on_error(db)
        raise


async def list_cases(db: AsyncSession, suite_id: int):
    return await suites_repo.list_cases(db, suite_id)


async def count_cases(db: AsyncSession, suite_id: int) -> int:
    return await suites_repo.count_cases(db, suite_id)


async def add_cases(
    db: AsyncSession, *, suite: TestSuite, case_ids: list[int]
):
    cases = await suites_repo.find_cases(db, case_ids=case_ids, project_id=suite.project_id)
    by_id = {case.id: case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"用例不存在: {missing}")
    existing = await suites_repo.find_existing_case_ids(db, suite_id=suite.id, case_ids=case_ids)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"用例已在套件中: {sorted(existing)}",
        )
    start_order = await suites_repo.max_sort_order(db, suite.id)
    try:
        created = await suites_repo.add_cases(
            db, suite_id=suite.id, case_ids=case_ids, start_order=start_order
        )
        await asset_service.commit_asset_change(db, [suite.project_id])
        await suites_repo.refresh_cases(db, created)
        return [(relation, by_id[relation.case_id]) for relation in created]
    except Exception:
        await _rollback_on_error(db)
        raise


async def reorder_cases(
    db: AsyncSession, *, suite: TestSuite, order: list[int]
) -> None:
    relations = await suites_repo.list_relations(db, suite.id)
    current_ids = {relation.case_id for relation in relations}
    requested_ids = set(order)
    if len(order) != len(requested_ids) or requested_ids != current_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="重排序必须包含套件中的全部用例且不能重复",
        )
    try:
        await suites_repo.reorder_cases(relations, order)
        await asset_service.commit_asset_change(db, [suite.project_id])
    except Exception:
        await _rollback_on_error(db)
        raise


async def remove_case(db: AsyncSession, *, suite: TestSuite, case_id: int) -> None:
    relation = await suites_repo.find_relation(db, suite_id=suite.id, case_id=case_id)
    if relation is None:
        return
    try:
        await suites_repo.delete_relation(db, relation)
        await asset_service.commit_asset_change(db, [suite.project_id])
    except Exception:
        await _rollback_on_error(db)
        raise
