"""档案解析器的节点处理逻辑。

本模块只负责节点的过滤、白名单覆盖、变量渲染、Registry 校验和快照最终化；
数据库读取与套件/用例编排由 :mod:`profile_resolver_load` 与
:mod:`profile_resolver` 负责。
"""

import re
from copy import deepcopy
from typing import Any
from uuid import UUID

from app.models import AppProfileSkipRule
from app.schemas.generated_case_params import (
    ASSERTION_NEEDS_ELEMENT,
    ASSERTION_PARAM_MODELS,
    ELEMENT_LABELS,
    KNOWN_ACTIONS,
    KNOWN_ASSERTIONS,
    STEP_NEEDS_ELEMENT,
    STEP_PARAM_MODELS,
)

_VAR_RE = re.compile(r"\$\{(\w+)\}")
_RUNTIME_VARIABLE_ACTIONS = frozenset({"get_text", "get_attribute"})

# 节点覆盖白名单（方案 §2.4）：仅允许覆盖业务字段；禁止改 key/order/phase/action/type/assertion_type
NODE_PATCH_ALLOWED = {
    "element_id",
    "params",
    "parameters",
    "expected",
    "expected_value",
    "timeout",
    "wait_timeout",
    "max_swipes",
    "duration",
    "max_wait_seconds",
    "variable_overrides",
}
NODE_IDENTITY_FIELDS = {"key", "order", "phase", "action", "type", "assertion_type"}


class ProfileRuleError(Exception):
    """规则/覆盖/变量解析错误；用 code 对应 422/413 错误码。"""

    def __init__(self, code: str = "PROFILE_RULE_INVALID", message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(message or code)


def render_text(text: str, variables: dict, runtime_variables: set[str] | frozenset[str] = frozenset()) -> str:
    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            if name in runtime_variables:
                return match.group(0)
            raise ProfileRuleError("PROFILE_VARIABLE_UNRESOLVED", f"未定义变量: ${{{name}}}")
        return str(variables[name])

    return _VAR_RE.sub(repl, text)


def render_value(value: Any, variables: dict, runtime_variables: set[str] | frozenset[str] = frozenset()) -> Any:
    if isinstance(value, str):
        return render_text(value, variables, runtime_variables)
    if isinstance(value, dict):
        return {k: render_value(v, variables, runtime_variables) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, variables, runtime_variables) for v in value]
    return value


def variable_references(value: Any) -> list[str]:
    """递归提取值中的变量名，保持首次出现顺序并去重。"""
    found: list[str] = []
    seen: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            for match in _VAR_RE.finditer(item):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    found.append(name)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return found


# 运行时输出变量名（如 get_text/get_attribute 的 variable_name），不是输入变量
_OUTPUT_PARAM_KEYS = frozenset({"variable_name"})


def node_variable_references(node: dict) -> list[str]:
    """节点参数中真实引用的输入变量；排除 variable_name 等运行时输出名。"""
    params = node.get("params")
    if not isinstance(params, dict):
        params = node.get("parameters")
    if not isinstance(params, dict):
        return []
    return variable_references(
        {key: value for key, value in params.items() if key not in _OUTPUT_PARAM_KEYS}
    )


def validate_variable_override(source_node: dict, value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "variable_overrides 必须是字符串字典")
    references = set(node_variable_references(source_node))
    result: dict[str, str] = {}
    for name, override in value.items():
        if not isinstance(name, str) or not name:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "variable_overrides 的变量名不能为空")
        if name not in references:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"变量未被目标节点引用: {name}")
        if not isinstance(override, str):
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"变量覆盖值必须是字符串: {name}")
        result[name] = override
    return result


def _apply_whitelist_patch(node: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if key in NODE_IDENTITY_FIELDS:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"禁止修改节点字段: {key}")
        if key not in NODE_PATCH_ALLOWED:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"不允许覆盖字段: {key}")
        node[key] = value
    return node


def _render_node_with_context(
    node: dict,
    variables: dict,
    case_name: str,
    runtime_variables: set[str] | frozenset[str] = frozenset(),
) -> dict:
    try:
        rendered = render_value(deepcopy(node), variables, runtime_variables)
    except ProfileRuleError as err:
        raise ProfileRuleError(
            err.code, f"用例[{case_name}] 节点[{node.get('key') or node.get('order')}]: {err.message}"
        ) from err
    return rendered


def runtime_variable_names(node: dict) -> set[str]:
    """返回节点成功执行后写入当前 Agent 执行上下文的变量名。"""
    if str(node.get("action") or "") not in _RUNTIME_VARIABLE_ACTIONS:
        return set()
    params = node.get("params") or node.get("parameters") or {}
    variable_name = params.get("variable_name") if isinstance(params, dict) else None
    return {variable_name} if isinstance(variable_name, str) and variable_name else set()


def _registry_validate_step(step: dict) -> dict:
    action = step.get("action")
    if action not in KNOWN_ACTIONS:
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"未知动作: {action}")
    model = STEP_PARAM_MODELS[action]
    params = deepcopy(step.get("params") or {})
    step["params"] = model(**params).model_dump(exclude_none=False)
    # 需要元素的动作必须提供 element_id（覆盖档案/覆盖节点场景）
    if action in STEP_NEEDS_ELEMENT and step.get("element_id") is None:
        label = ELEMENT_LABELS.get(action, "元素")
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"动作 {action} 需要元素（{label}）")
    # 目标文字去除首尾空格后不能为空
    if action == "swipe_in_element_find_text_click":
        target_text = step["params"].get("target_text")
        if not isinstance(target_text, str) or not target_text.strip():
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "目标文字不能为空")
    return step


def _registry_validate_assertion(assertion: dict) -> dict:
    assertion_type = assertion.get("type") or assertion.get("assertion_type")
    if assertion_type not in KNOWN_ASSERTIONS:
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"未知断言: {assertion_type}")
    model = ASSERTION_PARAM_MODELS[assertion_type]
    params = deepcopy(assertion.get("params") or {})
    assertion["params"] = model(**params).model_dump(exclude_none=False)
    if assertion_type in ASSERTION_NEEDS_ELEMENT and assertion.get("element_id") is None:
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "该断言需要元素")
    wait = float(assertion.get("max_wait_seconds", 10))
    if not 0 <= wait <= 300:
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", "断言最大等待时间必须在 0 到 300 秒之间")
    assertion["max_wait_seconds"] = wait
    assertion["continue_on_failure"] = bool(assertion.get("continue_on_failure", False))
    return assertion


def validate_node_patch(node_type: str, source_node: dict, patch: dict[str, Any]) -> dict:
    """保存覆盖前，以公共节点合并补丁并执行与解析阶段相同的 Registry 校验。"""
    variable_patch = patch.get("variable_overrides")
    if variable_patch is not None:
        # 动作与断言节点都允许覆盖变量；校验仍以该节点参数中真实引用的 ${name} 为界
        validate_variable_override(source_node, variable_patch)
    patched = _apply_whitelist_patch(
        deepcopy(source_node), {key: value for key, value in patch.items() if key != "variable_overrides"}
    )
    try:
        if node_type == "step":
            return _registry_validate_step(patched)
        if node_type == "assertion":
            return _registry_validate_assertion(patched)
    except Exception as exc:
        if isinstance(exc, ProfileRuleError):
            raise
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", str(exc)) from None
    raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"未知节点类型: {node_type}")


def finalize_snapshot_node(node: dict, phase: str = "main", order_offset: int = 0) -> dict:
    """将内部节点转为执行快照节点：丢弃 _source 内部字段，写入 source_key/source_order。"""
    out = {k: v for k, v in node.items() if not k.startswith("_") and k != "variable_overrides"}
    out["phase"] = node.get("phase") or phase
    out["source_order"] = node.get("_source_order")
    out["source_key"] = node.get("_source_key")
    return out


_CASE_PHASE_MAP = {"setup": "case_setup", "main": "case_main", "teardown": "case_teardown"}


def _finalize_case_step(node: dict) -> dict:
    out = {k: v for k, v in node.items() if not k.startswith("_") and k != "variable_overrides"}
    raw_phase = str(out.get("phase") or "main")
    out["phase"] = _CASE_PHASE_MAP.get(raw_phase, "case_main")
    out["source_order"] = node.get("_source_order")
    out["source_key"] = node.get("_source_key")
    out["kind"] = out.get("kind") or ("assertion" if "type" in out else "action")
    return out


def _finalize_suite_step(node: dict, phase: str) -> dict:
    out = {k: v for k, v in node.items() if not k.startswith("_") and k != "variable_overrides"}
    out["phase"] = phase
    out["source_order"] = node.get("_source_order")
    out["source_key"] = node.get("_source_key")
    return out


def filter_and_patch(
    nodes: list[dict],
    rules: dict[str, AppProfileSkipRule],
    overrides: dict[str, dict[str, Any]],
    target_type: str,
    case_id: int | None,
    *,
    case_name: str | None,
    suite_id: int | None,
    suite_name: str | None,
    phase: str | None = None,
    occurrence_order: int | None = None,
    exclusion_cls: Any,
) -> tuple[list[dict], list[Any]]:
    """过滤被跳过节点并应用白名单覆盖，保留 _source_key/_source_order。"""
    kept: list[dict] = []
    exclusions: list[Any] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        node_key = str(node.get("key") or "")
        rule = rules.get(node_key)
        if rule is not None:
            exclusions.append(
                exclusion_cls(
                    target_type=target_type,
                    suite_id=suite_id,
                    case_id=case_id,
                    node_key=UUID(node_key) if _is_uuid(node_key) else None,
                    source_type="direct",
                    reason_code=rule.reason_code,
                    reason_note=rule.reason_note,
                    display_snapshot={
                        "name": node.get("description") or node.get("action") or node.get("type") or "",
                        "key": node_key,
                        "suite_name": suite_name,
                        "case_name": case_name,
                        "node_name": node.get("description") or node.get("action") or node.get("type") or "",
                    },
                    phase=phase,
                    occurrence_order=occurrence_order,
                )
            )
            continue
        patch = overrides.get(node_key)
        if patch:
            node = _apply_whitelist_patch(deepcopy(node), patch)
        kept.append({**deepcopy(node), "_source_order": node.get("order"), "_source_key": node_key})
    return kept, exclusions


def assign_order(nodes: list[dict], order_offset: int) -> list[dict]:
    counters: dict[str, int] = {}
    for node in nodes:
        phase = str(node.get("phase") or "main")
        counters[phase] = counters.get(phase, 0) + 1
        node["order"] = counters[phase] + order_offset
    return nodes


def select_steps_for_run(nodes: list[dict], run_options: dict[str, bool]) -> list[dict]:
    """按执行选项选择阶段，并稳定排序为 setup → main → teardown。"""
    enabled_phases = {"main"}
    if run_options.get("use_pre_steps", False):
        enabled_phases.add("setup")
    if run_options.get("use_post_steps", False):
        enabled_phases.add("teardown")
    phase_rank = {"setup": 0, "main": 1, "teardown": 2}
    selected = [
        node
        for node in (nodes or [])
        if isinstance(node, dict) and str(node.get("phase") or "main") in enabled_phases
    ]
    # 纯 setup/teardown 用例关闭 pre/post 时回退纳入全部阶段，避免被误判为空。
    if not selected and any(isinstance(node, dict) for node in (nodes or [])):
        enabled_phases = {"setup", "main", "teardown"}
        selected = [
            node
            for node in (nodes or [])
            if isinstance(node, dict) and str(node.get("phase") or "main") in enabled_phases
        ]
    return sorted(
        selected,
        key=lambda node: (
            phase_rank.get(str(node.get("phase") or "main"), 1),
            int(node.get("order") or 0),
        ),
    )


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False
