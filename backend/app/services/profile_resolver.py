"""APP 档案配置解析引擎（方案 §1.1/§3）。

纯服务层：读取公共资产、应用跳过规则与覆盖、合并变量、Registry 校验、
输出不可变快照与排除项。FastAPI 路由与 Worker 均复用本模块，不得在路由中重复实现解析逻辑。

- `preview`：只读解析，不写执行相关数据。
- `materialize`：与 Execution/ExecutionCase/ExecutionStep/ExecutionExclusion/ExecutionQueue 同事务固化快照（B10）。
"""

import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppProfile,
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileRelease,
    AppProfileSkipRule,
    AppProfileVariableOverride,
    Project,
    TestCase,
    TestElement,
    TestModule,
    TestSuite,
    TestSuiteCase,
    Variable,
)
from app.schemas.generated_case_params import (
    ASSERTION_PARAM_MODELS,
    KNOWN_ACTIONS,
    KNOWN_ASSERTIONS,
    STEP_PARAM_MODELS,
)

_VAR_RE = re.compile(r"\$\{(\w+)\}")

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
}
NODE_IDENTITY_FIELDS = {"key", "order", "phase", "action", "type", "assertion_type"}


# ---------- 异常（方案 §4.10 错误码） ----------


class ProfileEmpty(Exception):
    """解析后没有可执行用例（PROFILE_EMPTY）。"""

    def __init__(self, exclusions: list["ExclusionItem"]) -> None:
        self.exclusions = exclusions
        super().__init__("解析后没有可执行用例")


class ProfileRevisionConflict(Exception):
    """profile_revision 或 test_asset_revision 变化（409）。"""

    def __init__(self, code: str, current: int, expected: int) -> None:
        self.code = code
        self.current = current
        self.expected = expected
        super().__init__(f"{code}: 期望 {expected}，实际 {current}")


class ProfileRuleError(Exception):
    """规则/覆盖/变量解析错误；用 code 对应 422/413 错误码。"""

    def __init__(self, code: str = "PROFILE_RULE_INVALID", message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(message or code)


# ---------- 数据类（方案 §3.1） ----------


@dataclass(frozen=True)
class ResolutionRequest:
    project_id: int
    profile_id: int
    release_id: int | None
    target_type: Literal["case", "suite", "batch"]
    target_ids: list[int]
    expected_profile_revision: int
    expected_test_asset_revision: int
    run_options: dict[str, bool] = field(default_factory=dict)
    execution_variables: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedCase:
    case_id: int
    case_name: str
    module_name: str | None
    steps_snapshot: list[dict[str, Any]]
    assertions_snapshot: list[dict[str, Any]]
    elements_snapshot: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ExclusionItem:
    target_type: Literal["suite", "case", "step", "assertion"]
    suite_id: int | None
    case_id: int | None
    node_key: UUID | None
    source_type: Literal["direct", "inherited", "empty_after_filter"]
    reason_code: str
    reason_note: str | None
    display_snapshot: dict[str, Any]


@dataclass(frozen=True)
class ResolutionResult:
    profile_revision: int
    test_asset_revision: int
    profile_name: str
    release_version: str
    cases: list[ResolvedCase]
    exclusions: list[ExclusionItem]
    summary: dict[str, int]
    warnings: list[dict[str, Any]]


# ---------- 缓存（方案 §3.4） ----------


class _LRUCache:
    def __init__(self, maxsize: int = 128, ttl: int = 300) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: dict[tuple, tuple[float, Any]] = {}

    def get(self, key: tuple) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        ts, value = item
        if (datetime.now(UTC).timestamp() - ts) > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: tuple, value: Any) -> None:
        if len(self._store) >= self.maxsize:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (datetime.now(UTC).timestamp(), value)


# ---------- 解析器 ----------


class ProfileResolver:
    def __init__(self, cache: _LRUCache | None = None) -> None:
        self.cache: _LRUCache = cache or _LRUCache()

    async def preview(self, request: ResolutionRequest, db: AsyncSession) -> ResolutionResult:
        return await resolve(request, db, self.cache)

    async def materialize(self, db: AsyncSession, execution: Any, result: ResolutionResult) -> None:
        from app.services.execution_snapshot import materialize_snapshot

        await materialize_snapshot(db, execution, result)


_resolver: ProfileResolver | None = None


def get_resolver() -> ProfileResolver:
    global _resolver
    if _resolver is None:
        _resolver = ProfileResolver()
    return _resolver


# ---------- 变量渲染 ----------


def render_text(text: str, variables: dict) -> str:
    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            raise ProfileRuleError("PROFILE_VARIABLE_UNRESOLVED", f"未定义变量: ${{{name}}}")
        return str(variables[name])

    return _VAR_RE.sub(repl, text)


def render_value(value: Any, variables: dict) -> Any:
    if isinstance(value, str):
        return render_text(value, variables)
    if isinstance(value, dict):
        return {k: render_value(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, variables) for v in value]
    return value


# ---------- 上下文加载 ----------


async def _load_config(db: AsyncSession, profile_id: int) -> dict:
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
    skip_case: dict[int, AppProfileSkipRule] = {}
    step_rules: dict[int, dict[str, AppProfileSkipRule]] = {}
    assertion_rules: dict[int, dict[str, AppProfileSkipRule]] = {}
    for rule in skip_rules:
        if rule.target_type == "suite" and rule.suite_id is not None:
            skip_suite[rule.suite_id] = rule
        elif rule.target_type == "case" and rule.case_id is not None:
            skip_case[rule.case_id] = rule
        elif rule.target_type in ("step", "assertion") and rule.case_id is not None and rule.node_key is not None:
            bucket = step_rules if rule.target_type == "step" else assertion_rules
            bucket.setdefault(rule.case_id, {})[str(rule.node_key)] = rule

    step_overrides: dict[int, dict[str, dict[str, Any]]] = {}
    assertion_overrides: dict[int, dict[str, dict[str, Any]]] = {}
    for ov in node_overrides:
        bucket = step_overrides if ov.target_type == "step" else assertion_overrides
        bucket.setdefault(ov.case_id, {})[str(ov.node_key)] = deepcopy(ov.patch)

    return {
        "skip_suite": skip_suite,
        "skip_case": skip_case,
        "step_rules": step_rules,
        "assertion_rules": assertion_rules,
        "element_overrides": {ov.element_id: ov for ov in element_overrides},
        "variable_overrides": {ov.name: ov.value for ov in variable_overrides},
        "step_overrides": step_overrides,
        "assertion_overrides": assertion_overrides,
    }


async def _load_targets(db: AsyncSession, request: ResolutionRequest) -> dict:
    """加载目标用例与套件成员。返回 {suites:[id...], case_suite:{case_id:suite_id}}。"""
    if request.target_type == "case":
        return {"case_ids": list(request.target_ids), "suite_ids": []}
    suite_ids = list(request.target_ids)
    if request.target_type == "batch":
        return {"case_ids": [], "suite_ids": suite_ids}
    # suite 执行：套件 id 列表
    return {"case_ids": [], "suite_ids": suite_ids}


async def _collect_cases(db: AsyncSession, target: dict, suite_skip: dict) -> tuple[dict[int, int], list[TestCase], list[ExclusionItem]]:
    """解析目标 → 去重用例列表 + case→suite 映射 + 套件级排除项。"""
    case_ids: list[int] = list(target["case_ids"])
    case_to_suite: dict[int, int] = {}
    suite_ids = list(target["suite_ids"])
    if suite_ids:
        memberships = (
            await db.execute(
                select(
                    TestSuiteCase.suite_id,
                    TestSuiteCase.case_id,
                    TestSuiteCase.sort_order,
                )
                .where(TestSuiteCase.suite_id.in_(suite_ids))
                .order_by(TestSuiteCase.suite_id, TestSuiteCase.sort_order, TestSuiteCase.id)
            )
        ).all()
        by_suite: dict[int, list[int]] = {sid: [] for sid in suite_ids}
        for suite_id, case_id, _sort_order in memberships:
            by_suite.setdefault(suite_id, []).append(case_id)
        # 严格保留请求中的套件顺序以及套件内部 sort_order。
        for suite_id in suite_ids:
            if suite_id in suite_skip:
                continue
            for case_id in by_suite.get(suite_id, []):
                case_ids.append(case_id)
                case_to_suite.setdefault(case_id, suite_id)

    case_ids = list(dict.fromkeys(case_ids))
    if not case_ids:
        return {}, [], []
    case_rows = (
        await db.execute(
            select(TestCase).where(TestCase.id.in_(case_ids), TestCase.deleted_at.is_(None))
        )
    ).scalars().all()
    cases_by_id = {case.id: case for case in case_rows}
    cases = [cases_by_id[case_id] for case_id in case_ids if case_id in cases_by_id]
    return case_to_suite, cases, []


# ---------- 节点过滤与覆盖 ----------


def _filter_and_patch(
    nodes: list[dict],
    rules: dict[str, AppProfileSkipRule],
    overrides: dict[str, dict[str, Any]],
    target_type: str,
    case_id: int,
    *,
    case_name: str,
    suite_id: int | None,
    suite_name: str | None,
) -> tuple[list[dict], list[ExclusionItem]]:
    """过滤被跳过节点并应用白名单覆盖，保留 _source_key/_source_order。"""
    kept: list[dict] = []
    exclusions: list[ExclusionItem] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        node_key = str(node.get("key") or "")
        rule = rules.get(node_key)
        if rule is not None:
            exclusions.append(
                ExclusionItem(
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
                )
            )
            continue
        patch = overrides.get(node_key)
        if patch:
            node = _apply_whitelist_patch(deepcopy(node), patch)
        kept.append({**deepcopy(node), "_source_order": node.get("order"), "_source_key": node_key})
    return kept, exclusions


def _apply_whitelist_patch(node: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if key in NODE_IDENTITY_FIELDS:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"禁止修改节点字段: {key}")
        if key not in NODE_PATCH_ALLOWED:
            raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"不允许覆盖字段: {key}")
        node[key] = value
    return node


# ---------- 渲染与 Registry 校验 ----------


def _render_node_with_context(node: dict, variables: dict, case_name: str) -> dict:
    try:
        rendered = render_value(deepcopy(node), variables)
    except ProfileRuleError as err:
        raise ProfileRuleError(
            err.code, f"用例[{case_name}] 节点[{node.get('key') or node.get('order')}]: {err.message}"
        ) from err
    return rendered


def _registry_validate_step(step: dict) -> dict:
    action = step.get("action")
    if action not in KNOWN_ACTIONS:
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"未知动作: {action}")
    model = STEP_PARAM_MODELS[action]
    params = deepcopy(step.get("params") or {})
    step["params"] = model(**params).model_dump(exclude_none=False)
    return step


def _registry_validate_assertion(assertion: dict) -> dict:
    assertion_type = assertion.get("type") or assertion.get("assertion_type")
    if assertion_type not in KNOWN_ASSERTIONS:
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"未知断言: {assertion_type}")
    model = ASSERTION_PARAM_MODELS[assertion_type]
    params = deepcopy(assertion.get("params") or {})
    assertion["params"] = model(**params).model_dump(exclude_none=False)
    return assertion


def validate_node_patch(node_type: str, source_node: dict, patch: dict[str, Any]) -> dict:
    """保存覆盖前，以公共节点合并补丁并执行与解析阶段相同的 Registry 校验。"""
    patched = _apply_whitelist_patch(deepcopy(source_node), patch)
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
    out = {k: v for k, v in node.items() if not k.startswith("_")}
    out["phase"] = node.get("phase") or phase
    out["source_order"] = node.get("_source_order")
    out["source_key"] = node.get("_source_key")
    return out


# ---------- 主解析（方案 §3.2） ----------


async def resolve(request: ResolutionRequest, db: AsyncSession, cache: _LRUCache | None = None) -> ResolutionResult:
    cache = cache or get_resolver().cache

    profile = await db.get(AppProfile, request.profile_id)
    if profile is None or profile.deleted_at is not None:
        raise ProfileRuleError("APP_PROFILE_NOT_FOUND", "档案不存在")
    if profile.project_id != request.project_id:
        raise ProfileRuleError("PROFILE_TARGET_NOT_FOUND", "档案不属于该项目")
    if profile.status != "active":
        raise ProfileRuleError("APP_PROFILE_NOT_FOUND", "档案已停用")
    if request.expected_profile_revision != profile.revision:
        raise ProfileRevisionConflict("PROFILE_REVISION_CONFLICT", profile.revision, request.expected_profile_revision)

    project = await db.get(Project, request.project_id)
    if request.expected_test_asset_revision != project.test_asset_revision:
        raise ProfileRevisionConflict(
            "TEST_ASSET_REVISION_CONFLICT", project.test_asset_revision, request.expected_test_asset_revision
        )

    if request.release_id is None:
        raise ProfileRuleError("APP_RELEASE_REQUIRED", "执行必须指定发布版本")
    release = await db.get(AppProfileRelease, request.release_id)
    if (
        release is None
        or release.deleted_at is not None
        or release.profile_id != request.profile_id
        or release.status != "active"
    ):
        raise ProfileRuleError("APP_RELEASE_NOT_FOUND", "发布版本不存在、已停用或不属于该档案")
    release_version = release.version

    cache_key = (request.project_id, project.test_asset_revision, request.profile_id, profile.revision)
    config = cache.get(cache_key)
    if config is None:
        config = await _load_config(db, request.profile_id)
        cache.set(cache_key, config)

    target = await _load_targets(db, request)
    case_to_suite, cases, suite_exclusions = await _collect_cases(db, target, config["skip_suite"])
    suite_rows = (
        await db.execute(select(TestSuite).where(TestSuite.id.in_(target["suite_ids"])))
    ).scalars().all() if target["suite_ids"] else []
    suite_names = {suite.id: suite.name for suite in suite_rows}

    # 套件整体跳过：以排除项记录（不展开子节点）
    exclusions: list[ExclusionItem] = [*suite_exclusions]
    for sid in target["suite_ids"]:
        rule = config["skip_suite"].get(sid)
        if rule is not None:
            exclusions.append(
                ExclusionItem(
                    target_type="suite",
                    suite_id=sid,
                    case_id=None,
                    node_key=None,
                    source_type="direct",
                    reason_code=rule.reason_code,
                    reason_note=rule.reason_note,
                    display_snapshot={
                        "name": suite_names.get(sid, f"suite:{sid}"),
                        "key": str(sid),
                        "suite_name": suite_names.get(sid),
                    },
                )
            )

    resolved_cases: list[ResolvedCase] = []
    applied_override_count = 0
    module_ids = {case.module_id for case in cases if case.module_id is not None}
    module_names: dict[int, str] = {}
    if module_ids:
        module_rows = (
            await db.execute(select(TestModule).where(TestModule.id.in_(module_ids)))
        ).scalars().all()
        module_names = {module.id: module.name for module in module_rows}
    for case in cases:
        case_rule = config["skip_case"].get(case.id)
        if case_rule:
            exclusions.append(
                ExclusionItem(
                    target_type="case",
                    suite_id=case_to_suite.get(case.id),
                    case_id=case.id,
                    node_key=None,
                    source_type="direct",
                    reason_code=case_rule.reason_code,
                    reason_note=case_rule.reason_note,
                    display_snapshot={
                        "name": case.name,
                        "key": str(case.id),
                        "suite_name": suite_names.get(case_to_suite.get(case.id)),
                        "case_name": case.name,
                    },
                )
            )
            continue

        suite_id = case_to_suite.get(case.id)
        suite_name = suite_names.get(suite_id) if suite_id is not None else None
        variables = await _merge_variables(db, request.project_id, suite_id, case, config, request.execution_variables)

        selected_steps = _select_steps_for_run(case.steps or [], request.run_options)
        applied_override_count += len(
            {str(node.get("key") or "") for node in selected_steps}
            & set(config["step_overrides"].get(case.id, {}))
        )
        applied_override_count += len(
            {str(node.get("key") or "") for node in (case.assertions or []) if isinstance(node, dict)}
            & set(config["assertion_overrides"].get(case.id, {}))
        )
        kept_steps, step_ex = _filter_and_patch(
            selected_steps, config["step_rules"].get(case.id, {}),
            config["step_overrides"].get(case.id, {}), "step", case.id,
            case_name=case.name, suite_id=suite_id, suite_name=suite_name,
        )
        exclusions.extend(step_ex)
        kept_assertions, assert_ex = _filter_and_patch(
            case.assertions or [], config["assertion_rules"].get(case.id, {}),
            config["assertion_overrides"].get(case.id, {}), "assertion", case.id,
            case_name=case.name, suite_id=suite_id, suite_name=suite_name,
        )
        exclusions.extend(assert_ex)

        # 渲染 + Registry 校验
        steps = [_registry_validate_step(_render_node_with_context(n, variables, case.name)) for n in kept_steps]
        assertions = [
            _registry_validate_assertion(_render_node_with_context(n, variables, case.name))
            for n in kept_assertions
        ]

        # 重新生成执行级连续 order，保留 source_order/source_key。
        steps = _assign_order(steps, order_offset=0)
        assertions = _assign_order(assertions, order_offset=len(steps))

        if not steps and not assertions:
            exclusions.append(
                ExclusionItem(
                    target_type="case",
                    suite_id=suite_id,
                    case_id=case.id,
                    node_key=None,
                    source_type="empty_after_filter",
                    reason_code="other",
                    reason_note="过滤后无可执行内容",
                    display_snapshot={
                        "name": case.name,
                        "key": str(case.id),
                        "suite_name": suite_name,
                        "case_name": case.name,
                    },
                )
            )
            continue

        elements = await _resolve_element_snapshots(
            db, steps, assertions, config["element_overrides"], variables
        )
        applied_override_count += sum(
            1 for element_id in elements if int(element_id) in config["element_overrides"]
        )
        resolved_cases.append(
            ResolvedCase(
                case_id=case.id,
                case_name=case.name,
                module_name=module_names.get(case.module_id) if case.module_id is not None else None,
                steps_snapshot=[finalize_snapshot_node(s) for s in steps],
                assertions_snapshot=[finalize_snapshot_node(a) for a in assertions],
                elements_snapshot=elements,
            )
        )

    if not resolved_cases:
        raise ProfileEmpty(exclusions)

    summary = {
        "source_suites": len(target["suite_ids"]),
        "source_cases": len(cases),
        "executable_cases": len(resolved_cases),
        "executable_steps": sum(len(c.steps_snapshot) for c in resolved_cases),
        "executable_assertions": sum(len(c.assertions_snapshot) for c in resolved_cases),
        "na_suites": sum(1 for e in exclusions if e.target_type == "suite"),
        "na_cases": sum(1 for e in exclusions if e.target_type == "case"),
        "na_steps": sum(1 for e in exclusions if e.target_type == "step"),
        "na_assertions": sum(1 for e in exclusions if e.target_type == "assertion"),
        "overrides": applied_override_count + len(config["variable_overrides"]),
    }

    return ResolutionResult(
        profile_revision=profile.revision,
        test_asset_revision=project.test_asset_revision,
        profile_name=profile.name,
        release_version=release_version,
        cases=resolved_cases,
        exclusions=exclusions,
        summary=summary,
        warnings=[],
    )


def _assign_order(nodes: list[dict], order_offset: int) -> list[dict]:
    for i, node in enumerate(nodes):
        node["order"] = i + 1 + order_offset
    return nodes


def _select_steps_for_run(nodes: list[dict], run_options: dict[str, bool]) -> list[dict]:
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
    return sorted(
        selected,
        key=lambda node: (
            phase_rank.get(str(node.get("phase") or "main"), 1),
            int(node.get("order") or 0),
        ),
    )


async def _merge_variables(
    db: AsyncSession,
    project_id: int,
    suite_id: int | None,
    case: TestCase | None,
    config: dict,
    execution_variables: dict,
) -> dict:
    """变量按 全局 → 项目 → 用例 → 套件 → APP档案 → 执行参数 合并。"""
    merged: dict = {}
    for v in (await db.execute(select(Variable).where(Variable.scope == "global"))).scalars().all():
        merged[v.name] = v.value
    for v in (
        await db.execute(select(Variable).where(Variable.scope == "project", Variable.project_id == project_id))
    ).scalars().all():
        merged[v.name] = v.value
    if case is not None and case.variables:
        merged.update(case.variables)
    if suite_id is not None:
        for v in (
            await db.execute(select(Variable).where(Variable.scope == "suite", Variable.suite_id == suite_id))
        ).scalars().all():
            merged[v.name] = v.value
    merged.update(config["variable_overrides"])
    merged.update(execution_variables)
    return merged


async def _resolve_element_snapshots(
    db: AsyncSession,
    steps: list[dict],
    assertions: list[dict],
    element_overrides: dict[int, AppProfileElementOverride],
    variables: dict,
) -> dict[str, dict[str, Any]]:
    ids: set[int] = set()
    for item in [*steps, *assertions]:
        element_id = item.get("element_id") if isinstance(item, dict) else None
        if element_id is not None:
            try:
                ids.add(int(element_id))
            except (TypeError, ValueError):
                continue
    if not ids:
        return {}
    rows = (await db.execute(select(TestElement).where(TestElement.id.in_(ids)))).scalars().all()
    elements: dict[str, dict[str, Any]] = {}
    for el in rows:
        override = element_overrides.get(el.id)
        locator_value = override.locator_value if override else el.locator_value
        locator_type = override.locator_type if override else el.locator_type
        elements[str(el.id)] = {
            "name": el.name,
            "platform": el.platform,
            "locator_type": locator_type,
            "locator_value": render_value(locator_value, variables),
        }
    return elements


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False
