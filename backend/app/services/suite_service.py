"""测试套件及套件用例关系的业务规则与事务编排。"""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, api_error
from app.models import TestSuite
from app.repositories import elements as elements_repo
from app.repositories import suites as suites_repo
from app.schemas.suite import SuiteCreate, SuiteUpdate
from app.services import asset_service, element_service
from app.services.case_service import validate_elements


async def _rollback_on_error(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        pass


async def _assert_suite_module(
    db: AsyncSession, *, project_id: int, module_id: int | None
) -> None:
    """套件的模块必须存在、属于该项目，且来自套件模块树（scope='suite'）。

    跨表 CHECK 约束在 PostgreSQL 里表达不了，这层校验是唯一的防线。
    """
    if module_id is None:
        return
    module = await elements_repo.get_module(db, module_id)
    if module is None or module.deleted_at is not None or module.project_id != project_id:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.MODULE_NOT_FOUND, "模块不存在")
    if module.scope != element_service.MODULE_SCOPE_SUITE:
        raise api_error(
            status.HTTP_400_BAD_REQUEST,
            ErrorCode.MODULE_SCOPE_MISMATCH,
            "该模块属于用例模块树，不能用于套件",
        )


async def get_or_404(db: AsyncSession, suite_id: int) -> TestSuite:
    suite = await suites_repo.get_by_id(db, suite_id)
    if suite is None or suite.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套件不存在")
    return suite


async def list_page(
    db: AsyncSession,
    *,
    project_id: int,
    module_id: int | None = None,
    ungrouped: bool = False,
    keyword: str = "",
    status: str = "",
    offset: int,
    limit: int,
):
    # 选中父模块时包含其子孙模块；ungrouped 只取未分组套件。
    module_ids = (
        await element_service.module_ids_with_descendants(
            db, project_id=project_id, scope=element_service.MODULE_SCOPE_SUITE, module_id=module_id
        )
        if module_id is not None and not ungrouped
        else None
    )
    return await suites_repo.list_page(
        db,
        project_id=project_id,
        module_ids=module_ids,
        ungrouped=ungrouped,
        keyword=keyword,
        status=status,
        offset=offset,
        limit=limit,
    )


async def create(db: AsyncSession, *, project_id: int, body: SuiteCreate, user_id: int) -> TestSuite:
    setup_steps = [step.model_dump() for step in body.setup_steps]
    teardown_steps = [step.model_dump() for step in body.teardown_steps]
    await _assert_suite_module(db, project_id=project_id, module_id=body.module_id)
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
            module_id=body.module_id,
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
    if "module_id" in body.model_fields_set:
        await _assert_suite_module(db, project_id=suite.project_id, module_id=body.module_id)
    if setup_steps is not None or teardown_steps is not None:
        await validate_elements(
            db,
            project_id=suite.project_id,
            steps=setup_steps,
            extra_steps=teardown_steps,
        )
    fields = {}
    for field in ("name", "description", "status", "module_id"):
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


async def module_name_of(db: AsyncSession, suite: TestSuite) -> str | None:
    """套件所属模块名；模块已被删除时按未分组处理。"""
    if suite.module_id is None:
        return None
    module = await elements_repo.get_module(db, suite.module_id)
    if module is None or module.deleted_at is not None:
        return None
    return module.name


async def add_cases(
    db: AsyncSession, *, suite: TestSuite, case_ids: list[int]
):
    cases = await suites_repo.find_cases(db, case_ids=case_ids, project_id=suite.project_id)
    by_id = {case.id: case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"用例不存在: {missing}")
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
    db: AsyncSession, *, suite: TestSuite, membership_ids: list[int]
) -> None:
    relations = await suites_repo.list_relations(db, suite.id)
    current_ids = {relation.id for relation in relations}
    requested_ids = set(membership_ids)
    if len(membership_ids) != len(requested_ids) or requested_ids != current_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="重排序必须包含套件中的全部编排项且不能重复",
        )
    try:
        await suites_repo.reorder_cases(relations, membership_ids)
        await asset_service.commit_asset_change(db, [suite.project_id])
    except Exception:
        await _rollback_on_error(db)
        raise


async def remove_case(db: AsyncSession, *, suite: TestSuite, membership_id: int) -> None:
    relation = await suites_repo.find_relation(db, suite_id=suite.id, membership_id=membership_id)
    if relation is None:
        return
    try:
        await suites_repo.delete_relation(db, relation)
        await asset_service.commit_asset_change(db, [suite.project_id])
    except Exception:
        await _rollback_on_error(db)
        raise
