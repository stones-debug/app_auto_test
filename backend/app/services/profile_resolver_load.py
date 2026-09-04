"""解析器的纯组装逻辑；所有数据库加载委托给 app_profiles Repository。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppProfileElementOverride, TestCase, TestElement
from app.repositories.app_profiles import resolution as resolution_repo
from app.schemas.generated_case_params import ELEMENT_PARAM_FIELDS
from app.services.profile_resolver_nodes import ProfileRuleError, render_value


async def load_config(db: AsyncSession, profile_id: int) -> dict:
    return await resolution_repo.load_config(db, profile_id)


async def collect_suite_cases(db: AsyncSession, suite_ids: list[int]) -> tuple[dict[int, list[int]], dict[int, TestCase]]:
    suite_case_ids = await resolution_repo.list_suite_members(db, suite_ids)
    case_ids = [case_id for ids in suite_case_ids.values() for case_id in ids]
    return suite_case_ids, await load_cases_by_id(db, case_ids)


async def load_cases_by_id(db: AsyncSession, case_ids: list[int]) -> dict[int, TestCase]:
    return await resolution_repo.load_cases_by_ids(db, case_ids)


async def merge_variables(db: AsyncSession, project_id: int, suite_id: int | None, case: TestCase | None, config: dict, execution_variables: dict) -> dict:
    """变量优先级：全局 → 项目 → 用例 → 套件 → APP 档案 → 执行参数。"""
    loaded = await resolution_repo.load_resolution_variables(db, project_id=project_id, suite_id=suite_id)
    merged = {variable.name: variable.value for variable in loaded if variable.scope != "suite"}
    if case is not None and case.variables:
        merged.update(case.variables)
    for variable in loaded:
        if variable.scope == "suite":
            merged[variable.name] = variable.value
    merged.update(config["variable_overrides"])
    merged.update(execution_variables)
    return merged


async def merge_suite_variables(db: AsyncSession, project_id: int, suite_id: int | None, config: dict, execution_variables: dict) -> dict:
    merged = {variable.name: variable.value for variable in await resolution_repo.load_resolution_variables(db, project_id=project_id, suite_id=suite_id)}
    merged.update(config["variable_overrides"])
    merged.update(execution_variables)
    return merged


async def resolve_element_snapshots(
    db: AsyncSession,
    project_id: int,
    steps: list[dict],
    element_overrides: dict[int, AppProfileElementOverride],
    variables: dict,
    runtime_variables: set[str] | frozenset[str] = frozenset(),
) -> dict[str, dict[str, Any]]:
    ids: set[int] = set()
    for item in steps:
        if not isinstance(item, dict):
            continue
        values = [item.get("element_id")]
        node_name = str(item.get("action") or item.get("type") or "")
        values.extend(
            item.get("params", {}).get(field)
            for field in ELEMENT_PARAM_FIELDS.get(node_name, ())
            if isinstance(item.get("params"), dict)
        )
        for element_id in values:
            if element_id is not None:
                try:
                    ids.add(int(element_id))
                except (TypeError, ValueError):
                    continue
    if not ids:
        return {}
    rows = await resolution_repo.load_by_ids(db, project_id=project_id, ids=ids)
    found = {row.id for row in rows}
    if found != ids:
        raise ProfileRuleError("PROFILE_ELEMENT_MISSING", f"步骤/断言引用的元素不存在、已删除或不属于该项目: {sorted(ids - found)}")
    return {
        str(element.id): element_snapshot(element, element_overrides.get(element.id), variables, runtime_variables)
        for element in rows
    }


def element_snapshot(
    element: TestElement,
    override: AppProfileElementOverride | None,
    variables: dict,
    runtime_variables: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    locator_type = override.locator_type if override else element.locator_type
    if locator_type == "smart":
        locator_config = override.locator_config if override else element.locator_config
        return {"name": element.name, "platform": element.platform, "locator_type": "smart", "locator_config": locator_config or None, "locator_value": None}
    locator_value = override.locator_value if override else element.locator_value
    return {
        "name": element.name,
        "platform": element.platform,
        "locator_type": locator_type,
        "locator_config": None,
        "locator_value": render_value(locator_value, variables, runtime_variables),
    }
