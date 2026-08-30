"""档案解析器的数据加载与快照组装逻辑。"""

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileSkipRule,
    AppProfileVariableOverride,
    TestCase,
    TestElement,
    TestSuiteCase,
    Variable,
)
from app.services.profile_resolver_nodes import ProfileRuleError, render_value


async def load_config(db: AsyncSession, profile_id: int) -> dict:
    """批量加载档案规则/覆盖索引，避免逐节点查询（方案 §10.2）。"""
    skip_rules = (
        await db.execute(
            select(AppProfileSkipRule).where(
                AppProfileSkipRule.profile_id == profile_id, AppProfileSkipRule.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    element_overrides = (
        await db.execute(
            select(AppProfileElementOverride).where(
                AppProfileElementOverride.profile_id == profile_id,
                AppProfileElementOverride.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    variable_overrides = (
        await db.execute(
            select(AppProfileVariableOverride).where(
                AppProfileVariableOverride.profile_id == profile_id,
                AppProfileVariableOverride.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    node_overrides = (
        await db.execute(
            select(AppProfileNodeOverride).where(
                AppProfileNodeOverride.profile_id == profile_id,
                AppProfileNodeOverride.deleted_at.is_(None),
            )
        )
    ).scalars().all()

    skip_suite: dict[int, AppProfileSkipRule] = {}
    skip_case: dict[tuple[int, int], AppProfileSkipRule] = {}
    step_rules: dict[tuple[int, int], dict[str, AppProfileSkipRule]] = {}
    assertion_rules: dict[tuple[int, int], dict[str, AppProfileSkipRule]] = {}
    skip_suite_step: dict[tuple[int, str], AppProfileSkipRule] = {}
    for rule in skip_rules:
        if rule.target_type == "suite" and rule.suite_id is not None:
            skip_suite[rule.suite_id] = rule
        elif (
            rule.target_type == "case"
            and rule.suite_id is not None
            and rule.case_id is not None
        ):
            skip_case[(rule.suite_id, rule.case_id)] = rule
        elif (
            rule.target_type == "suite_step"
            and rule.suite_id is not None
            and rule.node_key is not None
        ):
            skip_suite_step[(rule.suite_id, str(rule.node_key))] = rule
        elif (
            rule.target_type in ("step", "assertion")
            and rule.suite_id is not None
            and rule.case_id is not None
            and rule.node_key is not None
        ):
            bucket = step_rules if rule.target_type == "step" else assertion_rules
            bucket.setdefault((rule.suite_id, rule.case_id), {})[str(rule.node_key)] = rule

    # 节点覆盖键结构：(suite_id, case_id)；套件步骤覆盖（case_id 为空）单独索引
    step_overrides: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}
    assertion_overrides: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}
    suite_step_overrides: dict[tuple[int, str], dict[str, Any]] = {}
    for override in node_overrides:
        # 套件步骤覆盖：DB 存 target_type='suite_step'（case_id 恒空），独立索引。
        if override.target_type == "suite_step":
            if override.suite_id is not None:
                suite_step_overrides[(override.suite_id, str(override.node_key))] = deepcopy(override.patch)
            continue
        if override.suite_id is not None and override.case_id is not None:
            bucket = step_overrides if override.target_type == "step" else assertion_overrides
            bucket.setdefault((override.suite_id, override.case_id), {})[
                str(override.node_key)
            ] = deepcopy(override.patch)

    return {
        "skip_suite": skip_suite,
        "skip_case": skip_case,
        "step_rules": step_rules,
        "assertion_rules": assertion_rules,
        "skip_suite_step": skip_suite_step,
        "element_overrides": {override.element_id: override for override in element_overrides},
        "variable_overrides": {override.name: override.value for override in variable_overrides},
        "step_overrides": step_overrides,
        "assertion_overrides": assertion_overrides,
        "suite_step_overrides": suite_step_overrides,
    }


async def collect_suite_cases(
    db: AsyncSession, suite_ids: list[int]
) -> tuple[dict[int, list[int]], dict[int, TestCase]]:
    """按套件聚合用例 id（严格按 sort_order），并完成用例对象加载。"""
    suite_case_ids: dict[int, list[int]] = {suite_id: [] for suite_id in suite_ids}
    case_ids: list[int] = []
    if suite_ids:
        rows = (
            await db.execute(
                select(TestSuiteCase.suite_id, TestSuiteCase.case_id)
                .where(TestSuiteCase.suite_id.in_(suite_ids))
                .order_by(TestSuiteCase.suite_id, TestSuiteCase.sort_order, TestSuiteCase.id)
            )
        ).all()
        for suite_id, case_id in rows:
            suite_case_ids.setdefault(suite_id, []).append(case_id)
            case_ids.append(case_id)
    return suite_case_ids, await load_cases_by_id(db, case_ids)


async def load_cases_by_id(db: AsyncSession, case_ids: list[int]) -> dict[int, TestCase]:
    case_ids = list(dict.fromkeys(case_ids))
    if not case_ids:
        return {}
    rows = (
        await db.execute(
            select(TestCase).where(TestCase.id.in_(case_ids), TestCase.deleted_at.is_(None))
        )
    ).scalars().all()
    return {case.id: case for case in rows}


async def merge_variables(
    db: AsyncSession,
    project_id: int,
    suite_id: int | None,
    case: TestCase | None,
    config: dict,
    execution_variables: dict,
) -> dict:
    """变量按 全局 → 项目 → 用例 → 套件 → APP档案 → 执行参数 合并。"""
    merged: dict = {}
    for variable in (await db.execute(select(Variable).where(Variable.scope == "global"))).scalars().all():
        merged[variable.name] = variable.value
    for variable in (
        await db.execute(select(Variable).where(Variable.scope == "project", Variable.project_id == project_id))
    ).scalars().all():
        merged[variable.name] = variable.value
    if case is not None and case.variables:
        merged.update(case.variables)
    if suite_id is not None:
        for variable in (
            await db.execute(select(Variable).where(Variable.scope == "suite", Variable.suite_id == suite_id))
        ).scalars().all():
            merged[variable.name] = variable.value
    merged.update(config["variable_overrides"])
    merged.update(execution_variables)
    return merged


async def merge_suite_variables(
    db: AsyncSession,
    project_id: int,
    suite_id: int | None,
    config: dict,
    execution_variables: dict,
) -> dict:
    """套件级变量：全局 → 项目 → 套件 → APP档案 → 执行参数（不用例级）。"""
    merged: dict = {}
    for variable in (await db.execute(select(Variable).where(Variable.scope == "global"))).scalars().all():
        merged[variable.name] = variable.value
    for variable in (
        await db.execute(select(Variable).where(Variable.scope == "project", Variable.project_id == project_id))
    ).scalars().all():
        merged[variable.name] = variable.value
    if suite_id is not None:
        for variable in (
            await db.execute(select(Variable).where(Variable.scope == "suite", Variable.suite_id == suite_id))
        ).scalars().all():
            merged[variable.name] = variable.value
    merged.update(config["variable_overrides"])
    merged.update(execution_variables)
    return merged


async def resolve_element_snapshots(
    db: AsyncSession,
    project_id: int,
    steps: list[dict],
    element_overrides: dict[int, AppProfileElementOverride],
    variables: dict,
) -> dict[str, dict[str, Any]]:
    ids: set[int] = set()
    assertions = [assertion for step in steps for assertion in (step.get("assertions") or [])]
    for item in [*steps, *assertions]:
        element_id = item.get("element_id") if isinstance(item, dict) else None
        if element_id is not None:
            try:
                ids.add(int(element_id))
            except (TypeError, ValueError):
                continue
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(TestElement).where(
                TestElement.id.in_(ids),
                # 快照补全按 element_id 直接查询（含已逻辑删除记录），但仍校验项目归属。
                TestElement.project_id == project_id,
            )
        )
    ).scalars().all()
    found = {row.id for row in rows}
    if found != ids:
        missing = ids - found
        raise ProfileRuleError(
            "PROFILE_ELEMENT_MISSING",
            f"步骤/断言引用的元素不存在、已删除或不属于该项目: {sorted(missing)}",
        )
    elements: dict[str, dict[str, Any]] = {}
    for element in rows:
        override = element_overrides.get(element.id)
        elements[str(element.id)] = element_snapshot(element, override, variables)
    return elements


def element_snapshot(
    element: TestElement,
    override: AppProfileElementOverride | None,
    variables: dict,
) -> dict[str, Any]:
    """构造单个元素的执行快照；smart 定位原样透传 locator_config。"""
    locator_type = override.locator_type if override else element.locator_type
    if locator_type == "smart":
        locator_config = override.locator_config if override else element.locator_config
        return {
            "name": element.name,
            "platform": element.platform,
            "locator_type": "smart",
            "locator_config": locator_config or None,
            "locator_value": None,
        }
    locator_value = override.locator_value if override else element.locator_value
    return {
        "name": element.name,
        "platform": element.platform,
        "locator_type": locator_type,
        "locator_config": None,
        "locator_value": render_value(locator_value, variables),
    }
