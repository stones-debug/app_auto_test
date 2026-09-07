"""测试用例业务规则与事务编排。"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestCase
from app.repositories import cases as cases_repo
from app.schemas.case import CaseCreate, CaseUpdate
from app.schemas.generated_case_params import ASSERTION_NEEDS_ELEMENT, ELEMENT_PARAM_FIELDS
from app.services import asset_service


def _legacy_steps_from_nodes(flow_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """生成旧客户端只读所需的嵌套投影，不参与执行和解析。"""
    steps: list[dict[str, Any]] = []
    for node in flow_nodes:
        if node.get("kind") == "assertion":
            if steps:
                assertion = {
                    key: value
                    for key, value in node.items()
                    if key not in {"kind", "order", "phase"}
                }
                assertion["order"] = len(steps[-1].get("assertions") or []) + 1
                steps[-1].setdefault("assertions", []).append(assertion)
            continue
        action = {
            key: value
            for key, value in node.items()
            if key not in {"kind", "order"}
        }
        action["order"] = len(steps) + 1
        steps.append(action)
    return steps


def validate_orders(steps: list | None) -> None:
    legacy_shape = any(
        isinstance(step, dict) and "kind" not in step for step in (steps or [])
    )
    step_orders = [
        (str(step.get("phase") or "main"), int(step["order"]))
        for step in (steps or [])
        if isinstance(step, dict) and "order" in step
    ]
    if len(step_orders) != len(set(step_orders)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="同一阶段内执行节点 order 不得重复",
        )
    nested_assertion_orders = [
        (str(step.get("phase") or "main"), int(assertion["order"]))
        for step in (steps or [])
        if isinstance(step, dict)
        for assertion in (step.get("assertions") or [])
        if isinstance(assertion, dict) and "order" in assertion
    ]
    if len(nested_assertion_orders) != len(set(nested_assertion_orders)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="同一阶段内执行节点 order 不得重复",
        )
    if legacy_shape:
        return
    for phase in {phase for phase, _order in step_orders}:
        orders = sorted(order for current_phase, order in step_orders if current_phase == phase)
        if orders != list(range(1, len(orders) + 1)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{phase} 阶段执行节点 order 必须从 1 连续递增",
            )


def validate_keys(steps: list | None) -> None:
    keys = [step.get("key") for step in (steps or []) if isinstance(step, dict) and step.get("key")]
    if len(keys) != len(set(keys)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用例内步骤/断言 key 不得重复",
        )


def validate_node_requirements(nodes: list | None) -> None:
    for node in nodes or []:
        if (
            isinstance(node, dict)
            and node.get("kind") == "assertion"
            and node.get("type") in ASSERTION_NEEDS_ELEMENT
            and node.get("element_id") is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该断言需要元素",
            )


def _element_ids(steps: list | None, extra: list | None = None) -> set[int]:
    items = [*(steps or []), *(extra or [])]
    ids: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        values = [item.get("element_id")]
        node_name = str(item.get("action") or item.get("type") or "")
        values.extend(
            item.get("params", {}).get(field)
            for field in ELEMENT_PARAM_FIELDS.get(node_name, ())
            if isinstance(item.get("params"), dict)
        )
        for value in values:
            if value is not None:
                ids.add(int(value))
    return ids


async def validate_elements(
    db: AsyncSession,
    *,
    project_id: int,
    steps: list | None,
    extra_steps: list | None = None,
) -> None:
    element_ids = _element_ids(steps, extra_steps)
    rows = await cases_repo.find_elements_by_ids(db, element_ids)
    found = {row.id for row in rows}
    missing = element_ids - found
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"元素不存在: {sorted(missing)}",
        )
    cross_project = [row.id for row in rows if row.project_id != project_id]
    if cross_project:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"元素不属于该项目: {sorted(cross_project)}",
        )


async def get_or_404(db: AsyncSession, case_id: int) -> TestCase:
    case = await cases_repo.get_by_id(db, case_id)
    if case is None or case.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
    return case


async def list_page(
    db: AsyncSession, *, project_id: int, module_id: int | None, keyword: str, status_: str, offset: int, limit: int
):
    return await cases_repo.list_page(
        db,
        project_id=project_id,
        module_id=module_id,
        keyword=keyword,
        status=status_,
        offset=offset,
        limit=limit,
    )


async def create(db: AsyncSession, *, project_id: int, body: CaseCreate, user_id: int) -> TestCase:
    if not await cases_repo.module_belongs_to_project(db, project_id, body.module_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模块不存在")
    await validate_elements(db, project_id=project_id, steps=body.flow_nodes)
    validate_orders(body.steps if body.steps is not None else body.flow_nodes)
    validate_keys(body.flow_nodes)
    validate_node_requirements(body.flow_nodes)
    try:
        case = await cases_repo.create(
            db,
            project_id=project_id,
            module_id=body.module_id,
            name=body.name,
            description=body.description,
            status=body.status,
            flow_nodes=cast(list[dict], body.flow_nodes),
            steps=_legacy_steps_from_nodes(cast(list[dict], body.flow_nodes)),
            variables=body.variables,
            user_id=user_id,
        )
        await asset_service.commit_asset_change(db, [project_id])
        await cases_repo.refresh(db, case)
        return case
    except Exception:
        await db.rollback()
        raise


async def update(db: AsyncSession, *, case: TestCase, body: CaseUpdate, user_id: int) -> TestCase:
    if "module_id" in body.model_fields_set and not await cases_repo.module_belongs_to_project(
        db, case.project_id, body.module_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模块不存在")
    if body.flow_nodes is not None:
        await validate_elements(db, project_id=case.project_id, steps=body.flow_nodes)
    validate_orders(body.steps if body.steps is not None else body.flow_nodes)
    validate_keys(body.flow_nodes)
    validate_node_requirements(body.flow_nodes)
    allowed_fields = {"name", "module_id", "description", "status", "flow_nodes", "variables"}
    values = {
        field: getattr(body, field)
        for field in body.model_fields_set
        if field in allowed_fields
    }
    fields = set(values)
    if "flow_nodes" in fields:
        values["flow_nodes"] = cast(list[dict], values["flow_nodes"] or [])
        values["steps"] = _legacy_steps_from_nodes(cast(list[dict], values["flow_nodes"] or []))
        fields.add("steps")
    try:
        await cases_repo.update_fields(case, fields=fields, values=values, user_id=user_id)
        await asset_service.commit_asset_change(db, [case.project_id])
        await cases_repo.refresh(db, case)
        return case
    except Exception:
        await db.rollback()
        raise


async def soft_delete_many(
    db: AsyncSession, *, ids: list[int], project_id: int | None = None
) -> list[TestCase]:
    cases = await cases_repo.load_deletable(db, ids=ids, project_id=project_id)
    if not cases:
        return []
    try:
        await cases_repo.soft_delete_many(cases, datetime.now(UTC))
        await asset_service.commit_asset_change(db, [case.project_id for case in cases])
        return cases
    except Exception:
        await db.rollback()
        raise


async def load_deletable(
    db: AsyncSession, *, ids: list[int], project_id: int | None = None
) -> list[TestCase]:
    return await cases_repo.load_deletable(db, ids=ids, project_id=project_id)


async def soft_delete(db: AsyncSession, *, case: TestCase) -> None:
    try:
        await cases_repo.soft_delete(case, datetime.now(UTC))
        await asset_service.commit_asset_change(db, [case.project_id])
    except Exception:
        await db.rollback()
        raise


async def clone(db: AsyncSession, *, source: TestCase, user_id: int) -> TestCase:
    flow_nodes = deepcopy(source.flow_nodes or [])
    for node in flow_nodes:
        node["key"] = str(uuid4())
    try:
        cloned = await cases_repo.clone(
            db,
            source,
            flow_nodes=flow_nodes,
            steps=_legacy_steps_from_nodes(flow_nodes),
            user_id=user_id,
        )
        await asset_service.commit_asset_change(db, [source.project_id])
        await cases_repo.refresh(db, cloned)
        return cloned
    except Exception:
        await db.rollback()
        raise
