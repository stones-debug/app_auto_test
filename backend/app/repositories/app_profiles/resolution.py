"""档案工作台与执行解析所需的批量加载查询。"""

from copy import deepcopy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileSkipRule,
    AppProfileVariableOverride,
    TestCase,
    TestElement,
    TestModule,
    TestSuite,
    TestSuiteCase,
    Variable,
)


async def load_config(db: AsyncSession, profile_id: int) -> dict:
    """一次批量加载档案规则与覆盖，返回解析器索引。"""
    loaded = []
    for model in (
        AppProfileSkipRule, AppProfileElementOverride,
        AppProfileVariableOverride, AppProfileNodeOverride,
    ):
        rows = await db.execute(
            select(model).where(model.profile_id == profile_id, model.deleted_at.is_(None))
        )
        loaded.append(list(rows.scalars().all()))
    skip_rules, element_overrides, variable_overrides, node_overrides = loaded
    skip_suite: dict[int, Any] = {}
    skip_case: dict[tuple[int, int], Any] = {}
    step_rules: dict[tuple[int, int], dict[str, Any]] = {}
    assertion_rules: dict[tuple[int, int], dict[str, Any]] = {}
    skip_suite_step: dict[tuple[int, str], Any] = {}
    for rule in skip_rules:
        if rule.target_type == "suite" and rule.suite_id is not None:
            skip_suite[rule.suite_id] = rule
        elif rule.target_type == "case" and rule.suite_id is not None and rule.case_id is not None:
            skip_case[(rule.suite_id, rule.case_id)] = rule
        elif rule.target_type == "suite_step" and rule.suite_id is not None and rule.node_key is not None:
            skip_suite_step[(rule.suite_id, str(rule.node_key))] = rule
        elif rule.target_type in ("step", "assertion") and rule.suite_id is not None and rule.case_id is not None and rule.node_key is not None:
            bucket = step_rules if rule.target_type == "step" else assertion_rules
            bucket.setdefault((rule.suite_id, rule.case_id), {})[str(rule.node_key)] = rule
    step_overrides: dict[int, dict[str, dict[str, Any]]] = {}
    assertion_overrides: dict[int, dict[str, dict[str, Any]]] = {}
    suite_step_overrides: dict[tuple[int, str], dict[str, Any]] = {}
    for override in node_overrides:
        if override.target_type == "suite_step":
            if override.suite_id is not None:
                suite_step_overrides[(override.suite_id, str(override.node_key))] = deepcopy(override.patch)
        elif override.suite_case_id is not None:
            # 用例节点覆盖以编排项 suite_case_id 为身份，重复编排互不污染
            bucket = step_overrides if override.target_type == "step" else assertion_overrides
            bucket.setdefault(override.suite_case_id, {})[str(override.node_key)] = deepcopy(override.patch)
    return {
        "skip_suite": skip_suite, "skip_case": skip_case, "step_rules": step_rules,
        "assertion_rules": assertion_rules, "skip_suite_step": skip_suite_step,
        "element_overrides": {row.element_id: row for row in element_overrides},
        "variable_overrides": {row.name: row.value for row in variable_overrides},
        "membership_step_overrides": step_overrides,
        "membership_assertion_overrides": assertion_overrides,
        "suite_step_overrides": suite_step_overrides,
    }


async def list_suite_members(db: AsyncSession, suite_ids: list[int]) -> dict[int, list[int]]:
    result = {suite_id: [] for suite_id in suite_ids}
    if not suite_ids:
        return result
    rows = await db.execute(
        select(TestSuiteCase.suite_id, TestSuiteCase.case_id)
        .where(TestSuiteCase.suite_id.in_(suite_ids))
        .order_by(TestSuiteCase.suite_id, TestSuiteCase.sort_order, TestSuiteCase.id)
    )
    for suite_id, case_id in rows.all():
        result.setdefault(suite_id, []).append(case_id)
    return result


async def list_suite_memberships(
    db: AsyncSession, suite_ids: list[int]
) -> dict[int, list[tuple[int, int, dict[str, str]]]]:
    """返回每个套件的编排项 ``(membership_id, case_id, variable_overrides)``，按执行顺序。"""
    result: dict[int, list[tuple[int, int, dict[str, str]]]] = {suite_id: [] for suite_id in suite_ids}
    if not suite_ids:
        return result
    rows = await db.execute(
        select(
            TestSuiteCase.id,
            TestSuiteCase.suite_id,
            TestSuiteCase.case_id,
            TestSuiteCase.variable_overrides,
        )
        .where(TestSuiteCase.suite_id.in_(suite_ids))
        .order_by(TestSuiteCase.suite_id, TestSuiteCase.sort_order, TestSuiteCase.id)
    )
    for membership_id, suite_id, case_id, overrides in rows.all():
        result.setdefault(suite_id, []).append((membership_id, case_id, dict(overrides or {})))
    return result


async def load_cases_by_ids(db: AsyncSession, case_ids: list[int]) -> dict[int, TestCase]:
    ids = list(dict.fromkeys(case_ids))
    if not ids:
        return {}
    rows = await db.execute(select(TestCase).where(TestCase.id.in_(ids), TestCase.deleted_at.is_(None)))
    return {case.id: case for case in rows.scalars().all()}


async def load_suites_by_ids(
    db: AsyncSession, *, project_id: int, suite_ids: list[int]
) -> dict[int, TestSuite]:
    ids = list(dict.fromkeys(suite_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(TestSuite).where(
            TestSuite.id.in_(ids),
            TestSuite.project_id == project_id,
            TestSuite.deleted_at.is_(None),
        )
    )
    return {suite.id: suite for suite in rows.scalars().all()}


async def load_resolution_variables(
    db: AsyncSession, *, project_id: int, suite_id: int | None
) -> list[Variable]:
    conditions = [Variable.scope == "global"]
    global_rows = await db.execute(select(Variable).where(*conditions))
    result = list(global_rows.scalars().all())
    project_rows = await db.execute(
        select(Variable).where(Variable.scope == "project", Variable.project_id == project_id)
    )
    result.extend(project_rows.scalars().all())
    if suite_id is not None:
        suite_rows = await db.execute(
            select(Variable).where(Variable.scope == "suite", Variable.suite_id == suite_id)
        )
        result.extend(suite_rows.scalars().all())
    return result


async def load_by_ids(db: AsyncSession, *, project_id: int, ids: set[int]) -> list[TestElement]:
    if not ids:
        return []
    rows = await db.execute(
        select(TestElement).where(TestElement.id.in_(ids), TestElement.project_id == project_id)
    )
    return list(rows.scalars().all())


async def list_suites(db: AsyncSession, project_id: int) -> list[TestSuite]:
    rows = await db.execute(
        select(TestSuite).where(TestSuite.project_id == project_id, TestSuite.deleted_at.is_(None)).order_by(TestSuite.name, TestSuite.id)
    )
    return list(rows.scalars().all())


async def get_suite(db: AsyncSession, suite_id: int) -> TestSuite | None:
    return await db.get(TestSuite, suite_id)


async def get_case(db: AsyncSession, case_id: int) -> TestCase | None:
    return await db.get(TestCase, case_id)


async def get_suite_case(db: AsyncSession, membership_id: int) -> TestSuiteCase | None:
    return await db.get(TestSuiteCase, membership_id)


async def find_membership(
    db: AsyncSession, suite_id: int, case_id: int
) -> TestSuiteCase | None:
    """取套件中该用例排序最前的编排项（兼容仅按 suite/case 寻址的旧调用）。"""
    return (
        await db.execute(
            select(TestSuiteCase)
            .where(TestSuiteCase.suite_id == suite_id, TestSuiteCase.case_id == case_id)
            .order_by(TestSuiteCase.sort_order, TestSuiteCase.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_project_modules(db: AsyncSession, module_ids: set[int]) -> dict[int, str]:
    if not module_ids:
        return {}
    rows = await db.execute(select(TestModule).where(TestModule.id.in_(module_ids)))
    return {row.id: row.name for row in rows.scalars().all()}


async def suite_cases(db: AsyncSession, suite_id: int) -> list[TestCase]:
    rows = await db.execute(
        select(TestCase).join(TestSuiteCase, TestSuiteCase.case_id == TestCase.id).where(
            TestSuiteCase.suite_id == suite_id, TestCase.deleted_at.is_(None)
        ).order_by(TestSuiteCase.sort_order, TestSuiteCase.id)
    )
    # 工作台展示的是资产配置，不是套件编排 occurrence；同一用例只展示首次出现。
    seen: set[int] = set()
    result: list[TestCase] = []
    for case in rows.scalars().all():
        if case.id in seen:
            continue
        seen.add(case.id)
        result.append(case)
    return result


async def load_resolution_variables_batch(
    db: AsyncSession, *, project_id: int, suite_ids: set[int], case_ids: set[int]
) -> list[Variable]:
    """一次读取本次解析所需的所有变量作用域。"""
    from sqlalchemy import or_
    from sqlalchemy.sql.elements import ColumnElement

    clauses: list[ColumnElement[bool]] = [
        Variable.scope == "global",
        (Variable.scope == "project") & (Variable.project_id == project_id),
    ]
    if suite_ids:
        clauses.append((Variable.scope == "suite") & Variable.suite_id.in_(suite_ids))
    if case_ids:
        clauses.append((Variable.scope == "case") & Variable.case_id.in_(case_ids))
    rows = await db.execute(
        select(Variable).where(or_(*clauses))
    )
    return list(rows.scalars().all())


async def load_skip_index(db: AsyncSession, profile_id: int) -> dict:
    rows = await db.execute(select(AppProfileSkipRule).where(AppProfileSkipRule.profile_id == profile_id, AppProfileSkipRule.deleted_at.is_(None)))
    idx = {"suite": {}, "case": {}, "step": {}, "assertion": {}, "suite_step": {}}
    for rule in rows.scalars().all():
        if rule.target_type == "suite" and rule.suite_id is not None:
            idx["suite"][rule.suite_id] = rule
        elif rule.target_type == "case" and rule.suite_id is not None and rule.case_id is not None:
            idx["case"][(rule.suite_id, rule.case_id)] = rule
        elif rule.target_type == "suite_step" and rule.suite_id is not None and rule.node_key is not None:
            idx["suite_step"][(rule.suite_id, str(rule.node_key))] = rule
        elif rule.target_type in ("step", "assertion") and rule.suite_id is not None and rule.case_id is not None and rule.node_key is not None:
            idx[rule.target_type].setdefault((rule.suite_id, rule.case_id), {})[str(rule.node_key)] = rule
    return idx


async def load_override_index(db: AsyncSession, profile_id: int) -> dict:
    loaded = []
    for model in (AppProfileElementOverride, AppProfileVariableOverride, AppProfileNodeOverride):
        rows = await db.execute(select(model).where(model.profile_id == profile_id, model.deleted_at.is_(None)))
        loaded.append(list(rows.scalars().all()))
    element_rows, variable_rows, node_rows = loaded
    node_idx: dict[tuple[int, int], dict[str, str]] = {}
    membership_idx: dict[int, dict[str, str]] = {}
    suite_step_idx: dict[tuple[int, str], dict] = {}
    node_patches: dict[tuple[int, int, str], dict] = {}
    membership_patches: dict[int, dict[str, dict]] = {}
    for row in node_rows:
        if row.target_type == "suite_step" and row.suite_id is not None:
            suite_step_idx[(row.suite_id, str(row.node_key))] = row.patch
        elif row.suite_id is not None and row.case_id is not None:
            node_key = str(row.node_key)
            node_idx.setdefault((row.suite_id, row.case_id), {})[node_key] = row.target_type
            node_patches[(row.suite_id, row.case_id, node_key)] = deepcopy(row.patch)
            if row.suite_case_id is not None:
                membership_idx.setdefault(row.suite_case_id, {})[node_key] = row.target_type
                membership_patches.setdefault(row.suite_case_id, {})[node_key] = deepcopy(row.patch)
    return {
        "element": [row.element_id for row in element_rows],
        "variable": [row.name for row in variable_rows],
        "node": node_idx,
        "node_patches": node_patches,
        "membership": membership_idx,
        "membership_patches": membership_patches,
        "suite_step": suite_step_idx,
    }


async def suite_memberships(db: AsyncSession, suite_id: int) -> list[TestSuiteCase]:
    """套件内的全部编排项（occurrence），按执行顺序；已软删用例被排除。"""
    rows = await db.execute(
        select(TestSuiteCase)
        .join(TestCase, TestCase.id == TestSuiteCase.case_id)
        .where(TestSuiteCase.suite_id == suite_id, TestCase.deleted_at.is_(None))
        .order_by(TestSuiteCase.sort_order, TestSuiteCase.id)
    )
    return list(rows.scalars().all())


async def case_counts(db: AsyncSession, project_id: int) -> dict[int, int]:
    rows = await db.execute(
        select(TestSuiteCase.suite_id, func.count(func.distinct(TestSuiteCase.case_id))).join(TestCase, TestCase.id == TestSuiteCase.case_id).where(
            TestCase.project_id == project_id, TestCase.deleted_at.is_(None)
        ).group_by(TestSuiteCase.suite_id)
    )
    return dict(rows.tuples().all())


async def override_counts(db: AsyncSession, profile_id: int) -> dict[int, int]:
    rows = await db.execute(
        select(AppProfileNodeOverride.suite_id, func.count(AppProfileNodeOverride.id)).where(
            AppProfileNodeOverride.profile_id == profile_id,
            AppProfileNodeOverride.deleted_at.is_(None),
            AppProfileNodeOverride.suite_id.is_not(None),
        ).group_by(AppProfileNodeOverride.suite_id)
    )
    return {
        suite_id: count for suite_id, count in rows.tuples().all() if suite_id is not None
    }


async def diff_counts(
    db: AsyncSession, profile_id: int, *, skip_index: dict | None = None
) -> dict[int, int]:
    skip = skip_index if skip_index is not None else await load_skip_index(db, profile_id)
    counts: dict[int, int] = {}
    for suite_id, _case_id in skip["case"]:
        counts[suite_id] = counts.get(suite_id, 0) + 1
    for bucket_name in ("step", "assertion"):
        for (suite_id, _case_id), rules in skip[bucket_name].items():
            counts[suite_id] = counts.get(suite_id, 0) + len(rules)
    for suite_id, _node_key in skip["suite_step"]:
        counts[suite_id] = counts.get(suite_id, 0) + 1
    return counts


async def difference_names(
    db: AsyncSession, *, suite_ids: set[int], case_ids: set[int]
) -> tuple[dict[int, str], dict[int, str], dict[int, TestSuite]]:
    suites: dict[int, TestSuite] = {}
    if suite_ids:
        rows = await db.execute(select(TestSuite).where(TestSuite.id.in_(suite_ids)))
        suites = {row.id: row for row in rows.scalars().all()}
    cases: dict[int, str] = {}
    if case_ids:
        rows = await db.execute(select(TestCase).where(TestCase.id.in_(case_ids)))
        cases = {row.id: row.name for row in rows.scalars().all()}
    return {key: row.name for key, row in suites.items()}, cases, suites
