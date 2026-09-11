"""解析器的纯组装逻辑；所有数据库加载委托给 app_profiles Repository。"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppProfileElementOverride, TestCase, TestElement, TestSuite, Variable
from app.repositories.app_profiles import resolution as resolution_repo
from app.services.profile_resolver_nodes import ProfileRuleError, render_value
from app.services.random_variables import resolve_rows
from app.utils.element_refs import collect_element_ids


@dataclass
class ResolutionLoadContext:
    """本次解析共享的批量加载索引，避免按 suite/case 重复访问数据库。"""

    suites: dict[int, TestSuite] = field(default_factory=dict)
    global_variables: list[Variable] = field(default_factory=list)
    project_variables: list[Variable] = field(default_factory=list)
    suite_variables: dict[int, list[Variable]] = field(default_factory=dict)
    case_variables: dict[int, list[Variable]] = field(default_factory=dict)
    elements: dict[int, TestElement] = field(default_factory=dict)
    variable_cache: dict[tuple[Any, ...], str] = field(default_factory=dict)


async def prepare_load_context(
    db: AsyncSession,
    *,
    project_id: int,
    suites: list[TestSuite],
    cases: dict[int, TestCase],
    config: dict,
) -> ResolutionLoadContext:
    suite_ids = {suite.id for suite in suites}
    case_ids = set(cases)
    variables = await resolution_repo.load_resolution_variables_batch(
        db, project_id=project_id, suite_ids=suite_ids, case_ids=case_ids
    )
    context = ResolutionLoadContext(
        suites={suite.id: suite for suite in suites},
        global_variables=[variable for variable in variables if variable.scope == "global"],
        project_variables=[variable for variable in variables if variable.scope == "project"],
    )
    for variable in variables:
        if variable.scope == "suite" and variable.suite_id is not None:
            context.suite_variables.setdefault(variable.suite_id, []).append(variable)
        elif variable.scope == "case" and variable.case_id is not None:
            context.case_variables.setdefault(variable.case_id, []).append(variable)

    element_ids: set[int] = set()
    for suite in suites:
        element_ids.update(collect_element_ids(suite.setup_steps, suite.teardown_steps))
    for case in cases.values():
        element_ids.update(collect_element_ids(case.flow_nodes or case.steps))
    for bucket in ("membership_step_overrides", "membership_assertion_overrides"):
        for patches in config.get(bucket, {}).values():
            for patch in patches.values():
                element_ids.update(collect_element_ids(patch))
    for (suite_id, _node_key), patch in config.get("suite_step_overrides", {}).items():
        if suite_id in suite_ids:
            element_ids.update(collect_element_ids(patch))
    rows = await resolution_repo.load_by_ids(db, project_id=project_id, ids=element_ids)
    context.elements = {element.id: element for element in rows}
    return context


async def load_config(db: AsyncSession, profile_id: int) -> dict:
    return await resolution_repo.load_config(db, profile_id)


async def collect_suite_cases(
    db: AsyncSession, suite_ids: list[int]
) -> tuple[dict[int, list[tuple[int, int, dict[str, str]]]], dict[int, TestCase]]:
    """返回每个套件的编排项 ``(membership_id, case_id, variable_overrides)`` 与用例索引。"""
    memberships = await resolution_repo.list_suite_memberships(db, suite_ids)
    case_ids = [case_id for members in memberships.values() for _mid, case_id, _ov in members]
    return memberships, await load_cases_by_id(db, case_ids)


async def load_cases_by_id(db: AsyncSession, case_ids: list[int]) -> dict[int, TestCase]:
    return await resolution_repo.load_cases_by_ids(db, case_ids)


async def merge_variables(
    db: AsyncSession,
    project_id: int,
    suite_id: int | None,
    case: TestCase | None,
    config: dict,
    execution_variables: dict,
    context: ResolutionLoadContext | None = None,
    case_occurrence: int | None = None,
    membership_overrides: dict[str, str] | None = None,
) -> dict:
    """变量优先级（§10.6）：全局 → 项目 → 用例 → 套件 → 编排项 → APP 档案 → 执行参数。"""
    loaded = (
        [*context.global_variables, *context.project_variables]
        if context is not None
        else await resolution_repo.load_resolution_variables(db, project_id=project_id, suite_id=suite_id)
    )
    cache = context.variable_cache if context is not None else {}
    case_rows = context.case_variables.get(case.id, []) if context is not None and case is not None else []
    suite_rows = (
        context.suite_variables.get(suite_id, [])
        if context is not None and suite_id is not None else
        [variable for variable in loaded if variable.scope == "suite"]
    )
    # 编排项覆盖高于套件/用例变量，但低于 APP 档案覆盖与执行参数
    membership_names = set(membership_overrides or {})
    higher_names = (
        set(execution_variables) | set(config["variable_overrides"]) | membership_names |
        {variable.name for variable in case_rows} | {variable.name for variable in suite_rows} |
        set(case.variables if case is not None else {})
    )
    project_names = {variable.name for variable in loaded if variable.scope == "project"}
    merged = resolve_rows(
        [variable for variable in loaded if variable.scope == "global"], cache, ("global",),
        skip_names=higher_names | project_names,
    )
    merged.update(resolve_rows(
        [variable for variable in loaded if variable.scope == "project"], cache,
        ("project", project_id), skip_names=higher_names,
    ))
    if context is not None and case is not None:
        merged.update(resolve_rows(
            case_rows, cache,
            ("case", suite_id, case.id, case_occurrence if case_occurrence is not None else case.id),
            skip_names=set(execution_variables) | set(config["variable_overrides"]) | membership_names
            | {variable.name for variable in suite_rows},
        ))
    if case is not None and case.variables:
        merged.update(case.variables)
    suite_variables = suite_rows
    for variable in suite_variables:
        if variable.scope == "suite":
            merged.update(resolve_rows(
                [variable], cache, ("suite", suite_id),
                skip_names=set(config["variable_overrides"]) | set(execution_variables) | membership_names,
            ))
    merged.update(membership_overrides or {})
    merged.update(config["variable_overrides"])
    merged.update(execution_variables)
    return merged


async def merge_suite_variables(
    db: AsyncSession,
    project_id: int,
    suite_id: int | None,
    config: dict,
    execution_variables: dict,
    context: ResolutionLoadContext | None = None,
) -> dict:
    if context is not None:
        loaded = [
            *context.global_variables,
            *context.project_variables,
            *context.suite_variables.get(suite_id, []),
        ] if suite_id is not None else [*context.global_variables, *context.project_variables]
    else:
        loaded = await resolution_repo.load_resolution_variables(
            db, project_id=project_id, suite_id=suite_id
        )
    cache = context.variable_cache if context is not None else {}
    suite_names = {variable.name for variable in loaded if variable.scope == "suite"}
    higher_names = suite_names | set(config["variable_overrides"]) | set(execution_variables)
    merged = resolve_rows(
        [variable for variable in loaded if variable.scope == "global"], cache, ("global",),
        skip_names=higher_names | {variable.name for variable in loaded if variable.scope == "project"},
    )
    merged.update(resolve_rows(
        [variable for variable in loaded if variable.scope == "project"], cache,
        ("project", project_id), skip_names=higher_names,
    ))
    merged.update(resolve_rows(
        [variable for variable in loaded if variable.scope == "suite"], cache,
        ("suite", suite_id), skip_names=set(config["variable_overrides"]) | set(execution_variables),
    ))
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
    preloaded_elements: dict[int, TestElement] | None = None,
) -> dict[str, dict[str, Any]]:
    ids = collect_element_ids(steps)
    if not ids:
        return {}
    if preloaded_elements is not None:
        missing = ids - preloaded_elements.keys()
        if missing:
            preloaded_elements.update(
                {
                    element.id: element
                    for element in await resolution_repo.load_by_ids(
                        db, project_id=project_id, ids=missing
                    )
                }
            )
        rows = [preloaded_elements[element_id] for element_id in ids if element_id in preloaded_elements]
    else:
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
