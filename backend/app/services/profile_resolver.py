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
    ELEMENT_LABELS,
    KNOWN_ACTIONS,
    KNOWN_ASSERTIONS,
    STEP_NEEDS_ELEMENT,
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
    # 方案 §2：单用例执行时可指定套件上下文（引用该套件规则，而非虚拟套件）
    context_suite_id: int | None = None


@dataclass(frozen=True)
class ResolvedCase:
    suite_id: int | None
    suite_name: str | None
    case_id: int
    case_name: str
    module_name: str | None
    case_order: int
    steps_snapshot: list[dict[str, Any]]
    assertions_snapshot: list[dict[str, Any]]
    elements_snapshot: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ResolvedSuite:
    suite_id: int | None
    suite_name: str
    suite_order: int
    is_virtual: bool
    # 套件前后置快照（仅 Action，含 source_key/source_order）
    setup_steps_snapshot: list[dict[str, Any]]
    teardown_steps_snapshot: list[dict[str, Any]]
    elements_snapshot: dict[str, dict[str, Any]]
    cases: list[ResolvedCase]
    # 是否“整体 N/A”（所有用例被排除）：不创建实际套件，进入 N/A 区
    is_na: bool = False


@dataclass(frozen=True)
class ExclusionItem:
    target_type: Literal["suite", "case", "step", "assertion", "suite_step"]
    suite_id: int | None
    case_id: int | None
    node_key: UUID | None
    source_type: Literal["direct", "inherited", "empty_after_filter"]
    reason_code: str
    reason_note: str | None
    display_snapshot: dict[str, Any]
    phase: str | None = None


@dataclass(frozen=True)
class ResolutionResult:
    profile_revision: int
    test_asset_revision: int
    profile_name: str
    release_version: str
    suites: list[ResolvedSuite]
    exclusions: list[ExclusionItem]
    summary: dict[str, int]
    warnings: list[dict[str, Any]]

    @property
    def cases(self) -> list[ResolvedCase]:
        """兼容历史调用方（扁平展开所有套件用例）。"""
        return [c for s in self.suites for c in s.cases]


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
    for ov in node_overrides:
        # 套件步骤覆盖：DB 存 target_type='suite_step'（case_id 恒空），独立索引
        # （旧口径曾用 'step'+case_id=None 判断，导致 suite_step_overrides 永远为空）。
        if ov.target_type == "suite_step":
            if ov.suite_id is not None:
                suite_step_overrides[(ov.suite_id, str(ov.node_key))] = deepcopy(ov.patch)
            continue
        if ov.suite_id is not None and ov.case_id is not None:
            bucket = step_overrides if ov.target_type == "step" else assertion_overrides
            bucket.setdefault((ov.suite_id, ov.case_id), {})[str(ov.node_key)] = deepcopy(ov.patch)

    return {
        "skip_suite": skip_suite,
        "skip_case": skip_case,
        "step_rules": step_rules,
        "assertion_rules": assertion_rules,
        "skip_suite_step": skip_suite_step,
        "element_overrides": {ov.element_id: ov for ov in element_overrides},
        "variable_overrides": {ov.name: ov.value for ov in variable_overrides},
        "step_overrides": step_overrides,
        "assertion_overrides": assertion_overrides,
        "suite_step_overrides": suite_step_overrides,
    }


async def _collect_suite_cases(
    db: AsyncSession, suite_ids: list[int]
) -> tuple[dict[int, list[int]], dict[int, TestCase]]:
    """按套件聚合用例 id（严格按 sort_order），并完成用例对象加载。

    返回 ``({suite_id: [case_id...]}, {case_id: TestCase})``。
    """
    suite_case_ids: dict[int, list[int]] = {sid: [] for sid in suite_ids}
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
    return suite_case_ids, await _load_cases_by_id(db, case_ids)


async def _load_cases_by_id(db: AsyncSession, case_ids: list[int]) -> dict[int, TestCase]:
    case_ids = list(dict.fromkeys(case_ids))
    if not case_ids:
        return {}
    rows = (
        await db.execute(
            select(TestCase).where(TestCase.id.in_(case_ids), TestCase.deleted_at.is_(None))
        )
    ).scalars().all()
    return {case.id: case for case in rows}


# ---------- 节点过滤与覆盖 ----------


def _filter_and_patch(
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
                    phase=phase,
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
    # Step 12：需要元素的动作必须提供 element_id（覆盖档案/覆盖节点场景）
    if action in STEP_NEEDS_ELEMENT and step.get("element_id") is None:
        label = ELEMENT_LABELS.get(action, "元素")
        raise ProfileRuleError("PROFILE_OVERRIDE_INVALID", f"动作 {action} 需要元素（{label}）")
    # Step 12：目标文字去除首尾空格后不能为空
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


# 用例步骤阶段映射：源节点 setup/main/teardown → 执行树 case_setup/case_main/case_teardown
_CASE_PHASE_MAP = {"setup": "case_setup", "main": "case_main", "teardown": "case_teardown"}


def _finalize_case_step(node: dict) -> dict:
    out = {k: v for k, v in node.items() if not k.startswith("_")}
    raw_phase = str(out.get("phase") or "main")
    out["phase"] = _CASE_PHASE_MAP.get(raw_phase, "case_main")
    out["source_order"] = node.get("_source_order")
    out["source_key"] = node.get("_source_key")
    return out


def _finalize_suite_step(node: dict, phase: str) -> dict:
    out = {k: v for k, v in node.items() if not k.startswith("_")}
    out["phase"] = phase
    out["source_order"] = node.get("_source_order")
    out["source_key"] = node.get("_source_key")
    return out


def _case_scoped(
    config: dict, suite_id: int | None, case_id: int, kind: str
) -> dict[str, dict[str, Any]]:
    """读取当前套件中的用例节点覆盖；共享用例的其他套件不受影响。"""
    if suite_id is None:
        return {}
    bucket = config["step_overrides"] if kind == "step" else config["assertion_overrides"]
    return dict(bucket.get((suite_id, case_id), {}))


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

    # 构建待解析的套件规格：严格保留请求中的套件顺序与套件内 sort_order；
    # 单用例可选套件上下文（复用套件变量/规则），否则建虚拟套件。
    if request.target_type == "case":
        case_ids = list(dict.fromkeys(request.target_ids))
        if request.context_suite_id is not None:
            suite_specs: list[dict] = [
                {"suite_id": request.context_suite_id, "virtual": False, "case_ids": case_ids}
            ]
        else:
            suite_specs = [
                {"suite_id": None, "virtual": True, "case_ids": case_ids}
            ]
        cases_by_id = await _load_cases_by_id(db, case_ids)
    else:
        suite_ids = list(dict.fromkeys(request.target_ids))
        suite_case_ids, cases_by_id = await _collect_suite_cases(db, suite_ids)
        suite_specs = [
            {"suite_id": sid, "virtual": False, "case_ids": suite_case_ids.get(sid, [])}
            for sid in suite_ids
        ]

    module_ids = {case.module_id for case in cases_by_id.values() if case.module_id is not None}
    module_names: dict[int, str] = {}
    if module_ids:
        module_rows = (
            await db.execute(select(TestModule).where(TestModule.id.in_(module_ids)))
        ).scalars().all()
        module_names = {module.id: module.name for module in module_rows}

    # 逐套件解析：同一用例可在多套件各自生成独立 ResolvedCase（不跨套件去重）
    applied_override_count = 0
    suites: list[ResolvedSuite] = []
    exclusions: list[ExclusionItem] = []
    for suite_order, spec in enumerate(suite_specs, start=1):
        resolved_suite, suite_ex, ov_count = await _build_suite(
            db, request, config, spec, suite_order, cases_by_id, module_names
        )
        suites.append(resolved_suite)
        exclusions.extend(suite_ex)
        applied_override_count += ov_count

    if not any(not s.is_na for s in suites):
        raise ProfileEmpty(exclusions)

    executable_suites = [s for s in suites if not s.is_na]
    executable_cases = [c for s in executable_suites for c in s.cases]
    na_cases = [e for e in exclusions if e.target_type == "case"]

    summary = {
        "source_suites": len(suite_specs),
        "source_cases": sum(len(s["case_ids"]) for s in suite_specs),
        "executable_suites": len(executable_suites),
        "executable_cases": len(executable_cases),
        "executable_steps": sum(len(c.steps_snapshot) for c in executable_cases)
        + sum(
            len(s.setup_steps_snapshot) + len(s.teardown_steps_snapshot)
            for s in executable_suites
        ),
        "executable_assertions": sum(len(c.assertions_snapshot) for c in executable_cases),
        "na_suites": sum(1 for e in exclusions if e.target_type == "suite"),
        "na_cases": sum(1 for e in na_cases if e.suite_id is None),
        "na_steps": sum(1 for e in exclusions if e.target_type == "step"),
        "na_assertions": sum(1 for e in exclusions if e.target_type == "assertion"),
        "na_suite_steps": sum(1 for e in exclusions if e.target_type == "suite_step"),
        "na_suite_cases": sum(1 for e in na_cases if e.suite_id is not None),
        "overrides": applied_override_count + len(config["variable_overrides"]),
    }

    return ResolutionResult(
        profile_revision=profile.revision,
        test_asset_revision=project.test_asset_revision,
        profile_name=profile.name,
        release_version=release_version,
        suites=suites,
        exclusions=exclusions,
        summary=summary,
        warnings=[],
    )


async def _build_suite(
    db: AsyncSession,
    request: ResolutionRequest,
    config: dict,
    spec: dict,
    suite_order: int,
    cases_by_id: dict[int, TestCase],
    module_names: dict[int, str],
) -> tuple[ResolvedSuite, list[ExclusionItem], int]:
    """按一个套件规格生成 ResolvedSuite（含前后置步骤与用例），并返回该套件产生的排除项与覆盖计数。"""
    suite_id = spec["suite_id"]
    virtual = spec["virtual"]
    exclusions: list[ExclusionItem] = []
    override_count = 0

    if virtual:
        suite_name = "虚拟套件"
        setup_snapshot: list[dict[str, Any]] = []
        teardown_snapshot: list[dict[str, Any]] = []
        suite_elements: dict[str, dict[str, Any]] = {}
    else:
        suite = await db.get(TestSuite, suite_id)
        if suite is None or suite.deleted_at is not None:
            exclusions.append(
                ExclusionItem(
                    target_type="suite", suite_id=suite_id, case_id=None, node_key=None,
                    source_type="direct", reason_code="other", reason_note="套件不存在或已删除",
                    display_snapshot={"name": f"suite:{suite_id}", "key": str(suite_id), "suite_name": None},
                )
            )
            return (
                ResolvedSuite(
                    suite_id=suite_id, suite_name=f"suite:{suite_id}", suite_order=suite_order,
                    is_virtual=False, setup_steps_snapshot=[], teardown_steps_snapshot=[],
                    elements_snapshot={}, cases=[], is_na=True,
                ),
                exclusions, override_count,
            )
        suite_name = suite.name
        skip_rule = config["skip_suite"].get(suite_id)
        if skip_rule is not None:
            exclusions.append(
                ExclusionItem(
                    target_type="suite", suite_id=suite_id, case_id=None, node_key=None,
                    source_type="direct", reason_code=skip_rule.reason_code,
                    reason_note=skip_rule.reason_note,
                    display_snapshot={"name": suite_name, "key": str(suite_id), "suite_name": suite_name},
                )
            )
            setup_snapshot, teardown_snapshot, suite_elements, step_ex, step_ov = await _parse_suite_steps(
                db, request.project_id, suite.setup_steps or [], suite.teardown_steps or [],
                config, suite_id, suite_name, request.execution_variables,
            )
            exclusions.extend(step_ex)
            override_count += step_ov
            return (
                ResolvedSuite(
                    suite_id=suite_id, suite_name=suite_name, suite_order=suite_order,
                    is_virtual=False, setup_steps_snapshot=setup_snapshot,
                    teardown_steps_snapshot=teardown_snapshot, elements_snapshot=suite_elements,
                    cases=[], is_na=True,
                ),
                exclusions, override_count,
            )
        setup_snapshot, teardown_snapshot, suite_elements, step_ex, step_ov = await _parse_suite_steps(
            db, request.project_id, suite.setup_steps or [], suite.teardown_steps or [],
            config, suite_id, suite_name, request.execution_variables,
        )
        exclusions.extend(step_ex)
        override_count += step_ov

    resolved_cases: list[ResolvedCase] = []
    for case_pos, case_id in enumerate(spec["case_ids"], start=1):
        case = cases_by_id.get(case_id)
        if case is None:
            continue
        if not virtual:
            case_rule = config["skip_case"].get((suite_id, case_id))
            if case_rule is not None:
                exclusions.append(
                    ExclusionItem(
                        target_type="case", suite_id=suite_id, case_id=case.id, node_key=None,
                        source_type="direct", reason_code=case_rule.reason_code,
                        reason_note=case_rule.reason_note,
                        display_snapshot={
                            "name": case.name, "key": str(case.id),
                            "suite_name": suite_name, "case_name": case.name,
                        },
                    )
                )
                continue
        resolved_case, case_ex, case_ov = await _resolve_case(
            db, request, config, case, suite_id, suite_name, case_pos, module_names
        )
        exclusions.extend(case_ex)
        override_count += case_ov
        if resolved_case is not None:
            resolved_cases.append(resolved_case)

    if not resolved_cases:
        if not virtual:
            exclusions.append(
                ExclusionItem(
                    target_type="suite",
                    suite_id=suite_id,
                    case_id=None,
                    node_key=None,
                    source_type="empty_after_filter",
                    reason_code="other",
                    reason_note="套件内无可执行用例",
                    display_snapshot={
                        "name": suite_name,
                        "key": str(suite_id),
                        "suite_name": suite_name,
                    },
                )
            )
        return (
            ResolvedSuite(
                suite_id=suite_id, suite_name=suite_name, suite_order=suite_order,
                is_virtual=virtual, setup_steps_snapshot=setup_snapshot,
                teardown_steps_snapshot=teardown_snapshot, elements_snapshot=suite_elements,
                cases=[], is_na=True,
            ),
            exclusions, override_count,
        )
    return (
        ResolvedSuite(
            suite_id=suite_id, suite_name=suite_name, suite_order=suite_order,
            is_virtual=virtual, setup_steps_snapshot=setup_snapshot,
            teardown_steps_snapshot=teardown_snapshot, elements_snapshot=suite_elements,
            cases=resolved_cases, is_na=False,
        ),
        exclusions, override_count,
    )


async def _resolve_case(
    db: AsyncSession,
    request: ResolutionRequest,
    config: dict,
    case: TestCase,
    suite_id: int | None,
    suite_name: str | None,
    case_order: int,
    module_names: dict[int, str],
) -> tuple[ResolvedCase | None, list[ExclusionItem], int]:
    """解析单个用例（步骤/断言/元素），返回 ResolvedCase（或 None 表示整体 N/A）与排除项。"""
    variables = await _merge_variables(db, request.project_id, suite_id, case, config, request.execution_variables)
    selected_steps = _select_steps_for_run(case.steps or [], request.run_options)
    step_overrides = _case_scoped(config, suite_id, case.id, "step")
    assert_overrides = _case_scoped(config, suite_id, case.id, "assertion")
    override_count = len(
        {str(node.get("key") or "") for node in selected_steps if isinstance(node, dict)} & set(step_overrides)
    )
    override_count += len(
        {str(node.get("key") or "") for node in (case.assertions or []) if isinstance(node, dict)}
        & set(assert_overrides)
    )

    kept_steps, step_ex = _filter_and_patch(
        selected_steps, config["step_rules"].get((suite_id, case.id), {}), step_overrides,
        "step", case.id, case_name=case.name, suite_id=suite_id, suite_name=suite_name,
    )
    kept_assertions, assert_ex = _filter_and_patch(
        case.assertions or [], config["assertion_rules"].get((suite_id, case.id), {}), assert_overrides,
        "assertion", case.id, case_name=case.name, suite_id=suite_id, suite_name=suite_name,
    )
    exclusions = [*step_ex, *assert_ex]

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
                target_type="case", suite_id=suite_id, case_id=case.id, node_key=None,
                source_type="empty_after_filter", reason_code="other", reason_note="过滤后无可执行内容",
                display_snapshot={
                    "name": case.name, "key": str(case.id),
                    "suite_name": suite_name, "case_name": case.name,
                },
            )
        )
        return None, exclusions, override_count

    elements = await _resolve_element_snapshots(db, request.project_id, steps, assertions, config["element_overrides"], variables)
    override_count += sum(1 for element_id in elements if int(element_id) in config["element_overrides"])

    return (
        ResolvedCase(
            suite_id=suite_id,
            suite_name=suite_name,
            case_id=case.id,
            case_name=case.name,
            module_name=module_names.get(case.module_id) if case.module_id is not None else None,
            case_order=case_order,
            steps_snapshot=[_finalize_case_step(s) for s in steps],
            assertions_snapshot=[finalize_snapshot_node(a) for a in assertions],
            elements_snapshot=elements,
        ),
        exclusions, override_count,
    )


async def _parse_suite_steps(
    db: AsyncSession,
    project_id: int,
    setup_nodes: list,
    teardown_nodes: list,
    config: dict,
    suite_id: int,
    suite_name: str,
    execution_variables: dict,
) -> tuple[list[dict], list[dict], dict[str, dict[str, Any]], list[ExclusionItem], int]:
    """套件前后置步骤：过滤+覆盖+渲染+Registry 校验+元素，返回 (setup, teardown, elements, exclusions, overrides)。"""
    variables = await _merge_suite_variables(db, project_id, suite_id, config, execution_variables)
    suite_rules = {nk: r for (sid, nk), r in config["skip_suite_step"].items() if sid == suite_id}
    suite_overrides = {nk: patch for (sid, nk), patch in config["suite_step_overrides"].items() if sid == suite_id}
    exclusions: list[ExclusionItem] = []
    override_count = 0

    def _process(nodes: list, phase: str) -> tuple[list[dict], list[ExclusionItem]]:
        nonlocal override_count
        kept, ex = _filter_and_patch(
            nodes or [], suite_rules, suite_overrides, "suite_step", None,
            case_name=None, suite_id=suite_id, suite_name=suite_name, phase=phase,
        )
        override_count += len(
            {str(n.get("key") or "") for n in (nodes or []) if isinstance(n, dict)} & set(suite_overrides)
        )
        steps = [_registry_validate_step(_render_node_with_context(n, variables, suite_name)) for n in kept]
        steps = _assign_order(steps, order_offset=0)
        return [_finalize_suite_step(n, phase) for n in steps], ex

    setup_snapshot, setup_ex = _process(setup_nodes, "suite_setup")
    teardown_snapshot, teardown_ex = _process(teardown_nodes, "suite_teardown")
    exclusions = [*setup_ex, *teardown_ex]
    elements = await _resolve_element_snapshots(
        db, project_id, [*setup_snapshot, *teardown_snapshot], [], config["element_overrides"], variables
    )
    override_count += sum(1 for element_id in elements if int(element_id) in config["element_overrides"])
    return setup_snapshot, teardown_snapshot, elements, exclusions, override_count


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
    # 兜底：用例步骤全为非 main 阶段（如纯 setup）时，关闭 pre/post 会把步骤过滤成空集，
    # 从而被误判为 PROFILE_EMPTY。此处回退纳入全部阶段，保证有步骤的用例始终可执行。
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


async def _merge_suite_variables(
    db: AsyncSession,
    project_id: int,
    suite_id: int | None,
    config: dict,
    execution_variables: dict,
) -> dict:
    """套件级变量：全局 → 项目 → 套件 → APP档案 → 执行参数（不用例级）。"""
    merged: dict = {}
    for v in (await db.execute(select(Variable).where(Variable.scope == "global"))).scalars().all():
        merged[v.name] = v.value
    for v in (
        await db.execute(select(Variable).where(Variable.scope == "project", Variable.project_id == project_id))
    ).scalars().all():
        merged[v.name] = v.value
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
    project_id: int,
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
    rows = (
        await db.execute(
            select(TestElement).where(
                TestElement.id.in_(ids),
                # 设计 §10.3：快照补全按 element_id 直接查询（含已逻辑删除记录），
                # 避免已保存用例引用元素被删除后无法预览/执行。此处仅校验元素
                # 存在且属于本项目；「不存在/跨项目」仍算缺失。
                TestElement.project_id == project_id,
            )
        )
    ).scalars().all()
    found = {r.id for r in rows}
    if found != ids:
        missing = ids - found
        raise ProfileRuleError(
            "PROFILE_ELEMENT_MISSING",
            f"步骤/断言引用的元素不存在、已删除或不属于该项目: {sorted(missing)}",
        )
    elements: dict[str, dict[str, Any]] = {}
    for el in rows:
        override = element_overrides.get(el.id)
        elements[str(el.id)] = _element_snapshot(el, override, variables)
    return elements


def _element_snapshot(
    el: TestElement,
    override: AppProfileElementOverride | None,
    variables: dict,
) -> dict[str, Any]:
    """构造单个元素的执行快照（普通定位渲染变量；smart 定位原样透传 config）。

    smart 定位的 locator_config 不能调用 render_value：其中可能包含 ${device_name}
    等运行时才可求值的占位符，后端求值会报未定义变量，必须 raw 透传给 Agent。
    """
    locator_type = override.locator_type if override else el.locator_type
    if locator_type == "smart":
        locator_config = override.locator_config if override else el.locator_config
        return {
            "name": el.name,
            "platform": el.platform,
            "locator_type": "smart",
            "locator_config": locator_config or None,
            "locator_value": None,
        }
    locator_value = override.locator_value if override else el.locator_value
    return {
        "name": el.name,
        "platform": el.platform,
        "locator_type": locator_type,
        "locator_config": None,
        "locator_value": render_value(locator_value, variables),
    }


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False
