"""Step 18：测试资产 Service 的批量事务与查询边界。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models import TestCase as CaseModel
from app.models import TestSuite as SuiteModel
from app.models import TestSuiteCase as SuiteCaseModel
from app.schemas.element import ElementCreate
from app.schemas.suite import SuiteReorderRequest
from app.services import case_service, element_service, suite_service


@pytest.mark.asyncio
async def test_element_import_conflict_rolls_back_without_partial_write(monkeypatch):
    db = AsyncMock()
    row1 = SimpleNamespace(element_id=None)
    row2 = SimpleNamespace(element_id=None)
    model = ElementCreate(name="按钮", locator_type="id", locator_value="button")
    add_imported = AsyncMock(
        side_effect=[SimpleNamespace(id=1), RuntimeError("conflict")]
    )
    monkeypatch.setattr(element_service.elements_repo, "add_imported", add_imported)

    with pytest.raises(RuntimeError, match="conflict"):
        await element_service.import_elements(
            db,
            project_id=7,
            rows=[(row1, model), (row2, model)],
            existing_by_id={},
            user_id=1,
        )

    assert add_imported.await_count == 2
    db.rollback.assert_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_suite_reorder_missing_item_does_not_partially_mutate(monkeypatch):
    db = AsyncMock()
    suite = SuiteModel(id=7, project_id=11)
    first = SuiteCaseModel(id=1, suite_id=7, case_id=101, sort_order=1)
    second = SuiteCaseModel(id=2, suite_id=7, case_id=102, sort_order=2)
    monkeypatch.setattr(
        suite_service.suites_repo,
        "list_relations",
        AsyncMock(return_value=[first, second]),
    )

    with pytest.raises(HTTPException) as exc_info:
        await suite_service.reorder_cases(
            db, suite=suite, membership_ids=SuiteReorderRequest(membership_ids=[1]).membership_ids or []
        )

    assert exc_info.value.status_code == 400
    assert (first.sort_order, second.sort_order) == (1, 2)
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_delete_referenced_cases_does_not_partially_mutate(monkeypatch):
    db = AsyncMock()
    first = CaseModel(id=201, project_id=9, name="引用", steps=[], variables={})
    second = CaseModel(id=202, project_id=9, name="未引用", steps=[], variables={})
    monkeypatch.setattr(
        case_service.cases_repo,
        "load_deletable",
        AsyncMock(return_value=[first, second]),
    )
    monkeypatch.setattr(
        case_service.cases_repo,
        "find_execution_references",
        AsyncMock(return_value={201}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await case_service.soft_delete_many(db, ids=[201, 202], project_id=9)

    assert exc_info.value.status_code == 409
    assert first.deleted_at is None
    assert second.deleted_at is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_element_list_enrichment_is_one_repository_query(monkeypatch):
    db = AsyncMock()
    element = SimpleNamespace(id=1)
    project = SimpleNamespace(id=7)
    creator = SimpleNamespace(id=3)
    list_page = AsyncMock(return_value=(1, [(element, project, creator)]))
    monkeypatch.setattr(element_service.elements_repo, "list_page", list_page)

    result = await element_service.list_page(
        db,
        keyword="",
        platform="",
        page_name="",
        locator_type="",
        project_id=None,
        offset=0,
        limit=20,
    )

    assert result[1] == [(element, project, creator)]
    list_page.assert_awaited_once()


@pytest.mark.asyncio
async def test_case_element_ownership_validation_uses_one_batch_query(monkeypatch):
    db = AsyncMock()
    rows = [SimpleNamespace(id=1, project_id=7), SimpleNamespace(id=2, project_id=7)]
    find_elements = AsyncMock(return_value=rows)
    monkeypatch.setattr(case_service.cases_repo, "find_elements_by_ids", find_elements)

    await case_service.validate_elements(
        db,
        project_id=7,
        steps=[
            {"element_id": 1, "assertions": [{"element_id": 2}]},
            {"element_id": 2},
        ],
    )

    find_elements.assert_awaited_once()
    assert find_elements.await_args.args[1] == {1, 2}


@pytest.mark.asyncio
async def test_suite_case_loading_is_one_repository_query(monkeypatch):
    db = AsyncMock()
    list_cases = AsyncMock(return_value=[])
    monkeypatch.setattr(suite_service.suites_repo, "list_cases", list_cases)

    assert await suite_service.list_cases(db, 7) == []
    list_cases.assert_awaited_once_with(db, 7)
