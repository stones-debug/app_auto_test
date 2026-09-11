"""套件编排项与 APP 档案用例节点的变量引用/覆盖共享计算。

统一口径：只提取动作与断言 ``params``/``parameters`` 中真实出现的 ``${name}``，
按首次引用顺序去重；元素智能定位配置中的变量不纳入本次快捷编辑。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestCase, TestSuiteCase
from app.repositories.app_profiles import overrides as overrides_repo
from app.repositories.app_profiles import resolution as resolution_repo
from app.services.profile_resolver_nodes import (
    ProfileRuleError,
    node_variable_references,
)

# 列表接口只携带前 2 项摘要，避免用例很多时响应过大
MAX_PREVIEW = 2
_RANDOM_KINDS = {"random_integer", "random_choice"}
_SCOPE_LABELS = {
    "global": "全局",
    "project": "项目",
    "suite": "套件",
    "case": "用例",
    # 节点覆盖之下的两层覆盖：编排项覆盖与 APP 档案变量覆盖
    "occurrence": "编排项",
    "profile": "档案",
}


def is_assertion_node(node: dict) -> bool:
    """与解析器保持一致：kind == 'assertion' 或携带断言 type。"""
    return node.get("kind") == "assertion" or "type" in node


def case_variable_reference_counts(case: TestCase) -> dict[str, int]:
    """变量名 → 引用它的节点数；键顺序即变量首次引用顺序。"""
    counts: dict[str, int] = {}
    for node in case.flow_nodes or case.steps or []:
        if not isinstance(node, dict):
            continue
        for name in node_variable_references(node):
            counts[name] = counts.get(name, 0) + 1
    return counts


def node_variable_names(node: dict) -> list[str]:
    return node_variable_references(node)


def describe_definition(scope: str, kind: str, value: str | None, spec: Any) -> dict[str, Any]:
    """把变量定义描述成展示用结构；随机变量只展示规则，不提前生成结果。"""
    display = value or ""
    if kind == "random_integer":
        spec = spec if isinstance(spec, dict) else {}
        display = f"随机 {spec.get('min')}～{spec.get('max')}"
    elif kind == "random_choice":
        items = spec.get("items") if isinstance(spec, dict) else None
        display = f"随机列表（{len(items or [])}项）"
    return {
        "scope": scope,
        "scope_label": _SCOPE_LABELS.get(scope, scope),
        "kind": kind,
        "value": value or "",
        "display": display,
    }


def definitions_for_case(
    rows: list[Any], suite_id: int | None, case: TestCase
) -> dict[str, dict[str, Any]]:
    """从已加载变量行中挑出某用例可见的定义；低优先级先写、高优先级覆盖。"""
    result: dict[str, dict[str, Any]] = {}
    for scope in ("global", "project", "case"):
        for row in rows:
            if row.scope != scope:
                continue
            if scope == "case" and row.case_id != case.id:
                continue
            result[row.name] = describe_definition(row.scope, row.kind or "fixed", row.value, row.spec)
    if suite_id is not None:
        for row in rows:
            if row.scope == "suite" and row.suite_id == suite_id:
                result[row.name] = describe_definition("suite", row.kind or "fixed", row.value, row.spec)
    return result


async def inherited_variable_definitions(
    db: AsyncSession, *, project_id: int, suite_id: int | None, case: TestCase
) -> dict[str, dict[str, Any]]:
    """编排项/节点覆盖之外，该用例可见的变量定义（全局→项目→用例→套件）。"""
    rows = await resolution_repo.load_resolution_variables_batch(
        db,
        project_id=project_id,
        suite_ids={suite_id} if suite_id is not None else set(),
        case_ids={case.id},
    )
    return definitions_for_case(rows, suite_id, case)


async def load_definitions_batch(
    db: AsyncSession, *, project_id: int, suite_id: int | None, cases: list[TestCase]
) -> dict[int, dict[str, dict[str, Any]]]:
    """一次加载套件内全部用例的变量定义，避免列表接口逐行查询造成 N+1。"""
    if not cases:
        return {}
    rows = await resolution_repo.load_resolution_variables_batch(
        db,
        project_id=project_id,
        suite_ids={suite_id} if suite_id is not None else set(),
        case_ids={case.id for case in cases},
    )
    return {case.id: definitions_for_case(rows, suite_id, case) for case in cases}


def apply_fixed_layer(
    definitions: dict[str, dict[str, Any]], scope: str, values: dict[str, str] | None
) -> dict[str, dict[str, Any]]:
    """在节点覆盖之下再叠一层固定值覆盖（编排项覆盖 / 档案变量覆盖）。"""
    for name, value in (values or {}).items():
        definitions[name] = describe_definition(scope, "fixed", value, None)
    return definitions


def _status_for(definition: dict[str, Any] | None, overridden: bool) -> str:
    if overridden:
        return "overridden"
    if definition is None:
        return "undefined"
    if definition["kind"] in _RANDOM_KINDS:
        return "random"
    return "inherited"


def build_membership_variables(
    case: TestCase, definitions: dict[str, dict[str, Any]], overrides: dict[str, str]
) -> list[dict[str, Any]]:
    """套件编排项视角的变量详情（单值继承/覆盖）。"""
    counts = case_variable_reference_counts(case)
    variables: list[dict[str, Any]] = []
    for name, count in counts.items():
        definition = definitions.get(name)
        overridden = name in overrides
        display = overrides[name] if overridden else (definition["display"] if definition else "")
        variables.append(
            {
                "name": name,
                "reference_count": count,
                "status": _status_for(definition, overridden),
                "display_value": display,
                "inherited_value": definition["display"] if definition else None,
                "inherited_scope": definition["scope"] if definition else None,
                "override_enabled": overridden,
                "override_value": overrides.get(name, ""),
            }
        )
    return variables


def membership_preview(variables: list[dict[str, Any]]) -> dict[str, Any]:
    preview = []
    for item in variables[:MAX_PREVIEW]:
        if item["override_enabled"]:
            source = "occurrence"
        else:
            source = item["inherited_scope"] or item["status"]
        preview.append(
            {
                "name": item["name"],
                "display_value": item["display_value"],
                "source": source,
                "status": item["status"],
                "reference_count": item["reference_count"],
            }
        )
    return {"variable_count": len(variables), "variables_preview": preview}


def validate_membership_updates(case: TestCase, updates: Any) -> dict[str, str | None]:
    """编排项覆盖的增量校验：只能改当前用例实际引用的变量。"""
    if not isinstance(updates, dict):
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "updates 必须是对象")
    references = set(case_variable_reference_counts(case))
    normalized: dict[str, str | None] = {}
    for name, value in updates.items():
        if not isinstance(name, str) or not name:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "变量名不能为空")
        if name not in references:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"变量未被当前用例引用: {name}")
        if value is not None and not isinstance(value, str):
            raise ProfileRuleError(
                "PROFILE_OVERRIDE_INVALID", f"编排项覆盖值必须是字符串或 null: {name}"
            )
        normalized[name] = value
    return normalized


def apply_membership_updates(
    overrides: dict[str, str], updates: dict[str, str | None]
) -> dict[str, str]:
    """``null`` 表示删除覆盖并恢复继承；空字符串是合法覆盖值。"""
    merged: dict[str, str] = {str(key): str(value) for key, value in (overrides or {}).items()}
    for name, value in updates.items():
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = value
    return merged


def build_profile_variables(
    case: TestCase,
    definitions: dict[str, dict[str, Any]],
    occurrence_overrides: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """APP 档案视角：按变量分组；一个 occurrence 覆盖自动作用于全部引用节点。"""
    occurrence_overrides = occurrence_overrides or {}
    counts = case_variable_reference_counts(case)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for node in case.flow_nodes or case.steps or []:
        if not isinstance(node, dict):
            continue
        node_key = str(node.get("key") or "")
        if not node_key:
            continue
        is_assertion = is_assertion_node(node)
        for name in node_variable_names(node):
            grouped.setdefault(name, []).append(
                {
                    "node_type": "assertion" if is_assertion else "step",
                    "node_key": node_key,
                    "order": node.get("order"),
                    "node_name": node.get("description")
                    or node.get("action")
                    or node.get("type")
                    or "",
                    "inherited_value": definitions[name]["display"] if name in definitions else None,
                    "override_enabled": name in occurrence_overrides,
                    "override_value": occurrence_overrides.get(name, ""),
                }
            )
    variables: list[dict[str, Any]] = []
    for name, count in counts.items():
        references = grouped.get(name, [])
        definition = definitions.get(name)
        enabled_values = {ref["override_value"] for ref in references if ref["override_enabled"]}
        if len(enabled_values) > 1:
            status = "mixed"
        elif enabled_values:
            status = "overridden"
        else:
            status = _status_for(definition, False)
        variables.append(
            {
                "name": name,
                "reference_count": count,
                "status": status,
                "inherited_value": definition["display"] if definition else None,
                "inherited_scope": definition["scope"] if definition else None,
                "references": references,
            }
        )
    return variables


async def apply_profile_occurrence_variable_updates(
    db: AsyncSession,
    *,
    profile_id: int,
    membership: TestSuiteCase,
    case: TestCase,
    updates: Any,
    user_id: int,
) -> list[dict[str, Any]]:
    """批量写入当前 occurrence 的统一变量覆盖。"""
    if not isinstance(updates, list) or not updates:
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "updates 不能为空")
    references = set(case_variable_reference_counts(case))
    rows = await overrides_repo.list_suite_case_variable_overrides(db, profile_id, membership.id)
    by_name = {row.name: row for row in rows}
    changes: list[dict[str, Any]] = []
    for update in updates:
        if not isinstance(update, dict):
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "updates 项必须是对象")
        name = update.get("name")
        value = update.get("value")
        if not isinstance(name, str) or not name:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "变量名不能为空")
        if name not in references:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"变量未被当前用例引用: {name}")
        if value is not None and not isinstance(value, str):
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"覆盖值必须是字符串或 null: {name}")
        existing = by_name.get(name)
        before = existing.value if existing is not None else None
        changes.append(
            {"name": name, "before": before, "after": value, "suite_case_id": membership.id}
        )

    try:
        for update in updates:
            name = update["name"]
            value = update.get("value")
            existing = by_name.get(name)
            if value is None:
                if existing is not None:
                    await overrides_repo.soft_delete(existing, datetime.now(UTC), user_id)
            else:
                await overrides_repo.upsert_suite_case_variable(
                    db, profile_id=profile_id, suite_case_id=membership.id,
                    name=name, value=value, user_id=user_id,
                )
    except ProfileRuleError:
        # 任一更新无效则整体回滚，不留下部分写入
        await db.rollback()
        raise
    return changes
