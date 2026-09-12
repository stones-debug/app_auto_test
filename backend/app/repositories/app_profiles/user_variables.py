"""当前用户 APP 档案变量覆盖的候选变量与持久化查询。"""

from sqlalchemy import delete, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    TestCase,
    TestSuite,
    TestSuiteCase,
    UserAppProfileVariableOverride,
    Variable,
)
from app.services.profile_resolver_nodes import node_variable_references


async def candidate_variables(
    db: AsyncSession, *, project_id: int
) -> tuple[list[Variable], dict[int, int], dict[int, str], dict[int, str], dict[int, list[dict]]]:
    """批量读取项目档案可配置变量及节点引用统计。

    只收集 ``variables`` 表中的定义，变量 ID 是唯一身份。
    """
    suite_rows = list((await db.execute(select(TestSuite).where(
        TestSuite.project_id == project_id, TestSuite.deleted_at.is_(None)
    ).order_by(TestSuite.id))).scalars().all())
    suite_ids = {suite.id for suite in suite_rows}
    memberships = list((await db.execute(select(TestSuiteCase).where(
        TestSuiteCase.suite_id.in_(suite_ids) if suite_ids else false()
    ).order_by(TestSuiteCase.id))).scalars().all())
    cases = list((await db.execute(select(TestCase).where(
        TestCase.project_id == project_id,
        TestCase.deleted_at.is_(None),
    ))).scalars().all())
    case_ids = {case.id for case in cases}
    variable_filter = or_(
        (Variable.scope == "project") & (Variable.project_id == project_id),
        (Variable.scope == "suite") & Variable.suite_id.in_(suite_ids) if suite_ids else false(),
        (Variable.scope == "case") & Variable.case_id.in_(case_ids) if case_ids else false(),
    )
    variables = list((await db.execute(select(Variable).where(variable_filter))).scalars().all())

    by_project = {v.name: v for v in variables if v.scope == "project"}
    by_suite = {(v.suite_id, v.name): v for v in variables if v.scope == "suite"}
    by_case = {(v.case_id, v.name): v for v in variables if v.scope == "case"}
    cases_by_id = {case.id: case for case in cases}
    suite_names_by_id = {suite.id: suite.name for suite in suite_rows}
    refs_by_id: dict[int, int] = {}
    references_by_id: dict[int, list[dict]] = {}

    def add_node_refs(
        node: dict,
        definitions: dict[str, Variable],
        *,
        suite_id: int | None,
        suite_name: str | None,
        suite_case_id: int | None,
        case_id: int | None,
        case_name: str | None,
        phase: str | None = None,
        masked: set[str] | None = None,
    ) -> None:
        for name in node_variable_references(node):
            if masked and name in masked:
                continue
            variable = definitions.get(name)
            if variable is not None:
                refs_by_id[variable.id] = refs_by_id.get(variable.id, 0) + 1
                is_assertion = node.get("kind") == "assertion" or "type" in node
                references_by_id.setdefault(variable.id, []).append({
                    "suite_id": suite_id,
                    "suite_name": suite_name,
                    "suite_case_id": suite_case_id,
                    "case_id": case_id,
                    "case_name": case_name,
                    "node_type": "assertion" if is_assertion else "action",
                    "node_key": str(node.get("key") or ""),
                    "node_name": node.get("description") or node.get("action") or node.get("type") or "",
                    "action": node.get("action"),
                    "type": node.get("type"),
                    "phase": phase or node.get("phase"),
                    "order": node.get("order"),
                })

    for suite in suite_rows:
        definitions = {
            name: variable for (sid, name), variable in by_suite.items() if sid == suite.id
        }
        definitions.update({name: variable for name, variable in by_project.items() if name not in definitions})
        for phase, nodes in (("suite_setup", suite.setup_steps or []), ("suite_teardown", suite.teardown_steps or [])):
            for node in nodes:
                add_node_refs(
                    node, definitions, suite_id=suite.id, suite_name=suite.name,
                    suite_case_id=None, case_id=None, case_name=None, phase=phase,
                )

    compiled_cases: set[int] = set()
    for membership in memberships:
        compiled_cases.add(membership.case_id)
        case = cases_by_id.get(membership.case_id)
        if case is None:
            continue
        definitions = {
            name: variable for (sid, name), variable in by_suite.items() if sid == membership.suite_id
        }
        definitions.update({name: variable for (cid, name), variable in by_case.items() if cid == membership.case_id and name not in definitions})
        definitions.update({name: variable for name, variable in by_project.items() if name not in definitions})
        masked = set((membership.variable_overrides or {}).keys())
        for node in (case.flow_nodes or case.steps or []):
            add_node_refs(
                node, definitions, suite_id=membership.suite_id,
                suite_name=suite_names_by_id.get(membership.suite_id),
                suite_case_id=membership.id, case_id=case.id, case_name=case.name,
                masked=masked,
            )

    # Cases not compiled into a suite use the virtual-suite case > project semantics.
    for case in cases:
        if case.id in compiled_cases:
            continue
        definitions = {name: variable for (cid, name), variable in by_case.items() if cid == case.id}
        definitions.update({name: variable for name, variable in by_project.items() if name not in definitions})
        for node in (case.flow_nodes or case.steps or []):
            add_node_refs(
                node, definitions, suite_id=None, suite_name=None, suite_case_id=None,
                case_id=case.id, case_name=case.name,
            )

    variables = [variable for variable in variables if refs_by_id.get(variable.id, 0) > 0]
    scope_order = {"project": 0, "suite": 1, "case": 2}
    variables.sort(key=lambda v: (scope_order[v.scope], v.project_id or 0, v.suite_id or 0, v.case_id or 0, v.name, v.id))
    suite_names = {suite.id: suite.name for suite in suite_rows}
    case_names = {case.id: case.name for case in cases}
    for references in references_by_id.values():
        references.sort(key=lambda item: (
            item["suite_id"] or 0, item["suite_case_id"] or 0, item["case_id"] or 0,
            item["phase"] or "", item["order"] or 0, item["node_key"],
        ))
    return (
        variables,
        {variable.id: refs_by_id[variable.id] for variable in variables},
        suite_names,
        case_names,
        {variable.id: references_by_id.get(variable.id, []) for variable in variables},
    )


async def list_overrides(
    db: AsyncSession, *, user_id: int, profile_id: int
) -> list[UserAppProfileVariableOverride]:
    rows = await db.execute(select(UserAppProfileVariableOverride).where(
        UserAppProfileVariableOverride.user_id == user_id,
        UserAppProfileVariableOverride.profile_id == profile_id,
    ))
    return list(rows.scalars().all())


async def get_override(
    db: AsyncSession, *, user_id: int, profile_id: int, variable_id: int
) -> UserAppProfileVariableOverride | None:
    return await db.scalar(select(UserAppProfileVariableOverride).where(
        UserAppProfileVariableOverride.user_id == user_id,
        UserAppProfileVariableOverride.profile_id == profile_id,
        UserAppProfileVariableOverride.variable_id == variable_id,
    ))


async def upsert_override(
    db: AsyncSession, *, user_id: int, profile_id: int, variable_id: int, value_ciphertext: str
) -> UserAppProfileVariableOverride:
    row = await get_override(db, user_id=user_id, profile_id=profile_id, variable_id=variable_id)
    if row is None:
        row = UserAppProfileVariableOverride(
            user_id=user_id, profile_id=profile_id, variable_id=variable_id,
            value_ciphertext=value_ciphertext,
        )
        db.add(row)
    else:
        row.value_ciphertext = value_ciphertext
    await db.flush()
    return row


async def delete_override(
    db: AsyncSession, *, user_id: int, profile_id: int, variable_id: int
) -> None:
    await db.execute(delete(UserAppProfileVariableOverride).where(
        UserAppProfileVariableOverride.user_id == user_id,
        UserAppProfileVariableOverride.profile_id == profile_id,
        UserAppProfileVariableOverride.variable_id == variable_id,
    ))
