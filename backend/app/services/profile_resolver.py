"""APP 档案配置解析引擎的对外类型与编排入口。

节点处理位于 ``profile_resolver_nodes``，数据库加载与元素/变量组装位于
``profile_resolver_load``。本模块保留历史公开导入路径，并负责将各阶段编排成
``ResolutionResult``。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestCase
from app.repositories import projects as projects_repo
from app.repositories.app_profiles import profiles as profiles_repo
from app.repositories.app_profiles import releases as releases_repo
from app.repositories.app_profiles import resolution as resolution_repo
from app.services.profile_resolver_load import (
    collect_suite_cases as _collect_suite_cases,
)
from app.services.profile_resolver_load import (
    load_cases_by_id as _load_cases_by_id,
)
from app.services.profile_resolver_load import (
    load_config as _load_config,
)
from app.services.profile_resolver_load import (
    merge_suite_variables as _merge_suite_variables,
)
from app.services.profile_resolver_load import (
    merge_variables as _merge_variables,
)
from app.services.profile_resolver_load import (
    resolve_element_snapshots as _resolve_element_snapshots,
)
from app.services.profile_resolver_nodes import (
    NODE_IDENTITY_FIELDS,
    NODE_PATCH_ALLOWED,
    ProfileRuleError,
    _finalize_case_step,
    _finalize_suite_step,
    _registry_validate_assertion,
    _registry_validate_step,
    _render_node_with_context,
    render_text,
    render_value,
    validate_node_patch,
)
from app.services.profile_resolver_nodes import (
    assign_order as _assign_order,
)
from app.services.profile_resolver_nodes import filter_and_patch as _filter_and_patch_impl
from app.services.profile_resolver_nodes import (
    select_steps_for_run as _select_steps_for_run,
)

__all__ = [
    "ExclusionItem",
    "NODE_IDENTITY_FIELDS",
    "NODE_PATCH_ALLOWED",
    "ProfileEmpty",
    "ProfileRevisionConflict",
    "ProfileResolver",
    "ProfileRuleError",
    "ResolutionRequest",
    "ResolutionResult",
    "ResolvedCase",
    "ResolvedSuite",
    "get_resolver",
    "render_text",
    "render_value",
    "resolve",
    "validate_node_patch",
]


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
    elements_snapshot: dict[str, dict[str, Any]]

    @property
    def assertions_snapshot(self) -> list[dict[str, Any]]:
        """只读派生视图，便于旧的统计调用逐步迁移。"""
        return [assertion for step in self.steps_snapshot for assertion in (step.get("assertions") or [])]


@dataclass(frozen=True)
class ResolvedSuite:
    suite_id: int | None
    suite_name: str
    suite_order: int
    is_virtual: bool
    setup_steps_snapshot: list[dict[str, Any]]
    teardown_steps_snapshot: list[dict[str, Any]]
    elements_snapshot: dict[str, dict[str, Any]]
    cases: list[ResolvedCase]
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
        return [case for suite in self.suites for case in suite.cases]


class _LRUCache:
    def __init__(self, maxsize: int = 128, ttl: int = 300) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: dict[tuple, tuple[float, Any]] = {}

    def get(self, key: tuple) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        timestamp, value = item
        if (datetime.now(UTC).timestamp() - timestamp) > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: tuple, value: Any) -> None:
        if len(self._store) >= self.maxsize:
            oldest = min(self._store, key=lambda key: self._store[key][0])
            self._store.pop(oldest, None)
        self._store[key] = (datetime.now(UTC).timestamp(), value)


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


def _filter_and_patch(*args: Any, **kwargs: Any) -> tuple[list[dict], list[ExclusionItem]]:
    """兼容旧内部导入，为节点模块注入本模块定义的排除项类型。"""
    return _filter_and_patch_impl(*args, exclusion_cls=ExclusionItem, **kwargs)


def _case_scoped(
    config: dict, suite_id: int | None, case_id: int, kind: str
) -> dict[str, dict[str, Any]]:
    """读取当前套件中的用例节点覆盖；共享用例的其他套件不受影响。"""
    if suite_id is None:
        return {}
    bucket = config["step_overrides"] if kind == "step" else config["assertion_overrides"]
    return dict(bucket.get((suite_id, case_id), {}))


async def resolve(
    request: ResolutionRequest, db: AsyncSession, cache: _LRUCache | None = None
) -> ResolutionResult:
    cache = cache or get_resolver().cache
    profile = await profiles_repo.get_by_id(db, request.profile_id)
    if profile is None or profile.deleted_at is not None:
        raise ProfileRuleError("APP_PROFILE_NOT_FOUND", "档案不存在")
    if profile.project_id != request.project_id:
        raise ProfileRuleError("PROFILE_TARGET_NOT_FOUND", "档案不属于该项目")
    if profile.status != "active":
        raise ProfileRuleError("APP_PROFILE_NOT_FOUND", "档案已停用")
    if request.expected_profile_revision != profile.revision:
        raise ProfileRevisionConflict("PROFILE_REVISION_CONFLICT", profile.revision, request.expected_profile_revision)

    project = await projects_repo.get_by_id(db, request.project_id)
    if project is None:
        raise ProfileRuleError("PROFILE_TARGET_NOT_FOUND", "项目不存在")
    if request.expected_test_asset_revision != project.test_asset_revision:
        raise ProfileRevisionConflict(
            "TEST_ASSET_REVISION_CONFLICT", project.test_asset_revision, request.expected_test_asset_revision
        )
    if request.release_id is None:
        raise ProfileRuleError("APP_RELEASE_REQUIRED", "执行必须指定发布版本")
    release = await releases_repo.get_by_id(db, request.release_id)
    if (
        release is None
        or release.deleted_at is not None
        or release.profile_id != request.profile_id
        or release.status != "active"
    ):
        raise ProfileRuleError("APP_RELEASE_NOT_FOUND", "发布版本不存在、已停用或不属于该档案")

    cache_key = (request.project_id, project.test_asset_revision, request.profile_id, profile.revision)
    config = cache.get(cache_key)
    if config is None:
        config = await _load_config(db, request.profile_id)
        cache.set(cache_key, config)

    if request.target_type == "case":
        case_ids = list(dict.fromkeys(request.target_ids))
        if request.context_suite_id is not None:
            suite_specs: list[dict] = [
                {"suite_id": request.context_suite_id, "virtual": False, "case_ids": case_ids}
            ]
        else:
            suite_specs = [{"suite_id": None, "virtual": True, "case_ids": case_ids}]
        cases_by_id = await _load_cases_by_id(db, case_ids)
    else:
        suite_ids = list(dict.fromkeys(request.target_ids))
        suite_case_ids, cases_by_id = await _collect_suite_cases(db, suite_ids)
        suite_specs = [
            {"suite_id": suite_id, "virtual": False, "case_ids": suite_case_ids.get(suite_id, [])}
            for suite_id in suite_ids
        ]

    module_ids = {case.module_id for case in cases_by_id.values() if case.module_id is not None}
    module_names: dict[int, str] = {}
    if module_ids:
        module_names = await resolution_repo.get_project_modules(db, module_ids)

    applied_override_count = 0
    suites: list[ResolvedSuite] = []
    exclusions: list[ExclusionItem] = []
    for suite_order, spec in enumerate(suite_specs, start=1):
        resolved_suite, suite_exclusions, override_count = await _build_suite(
            db, request, config, spec, suite_order, cases_by_id, module_names
        )
        suites.append(resolved_suite)
        exclusions.extend(suite_exclusions)
        applied_override_count += override_count

    if not any(not suite.is_na for suite in suites):
        raise ProfileEmpty(exclusions)
    executable_suites = [suite for suite in suites if not suite.is_na]
    executable_cases = [case for suite in executable_suites for case in suite.cases]
    na_cases = [item for item in exclusions if item.target_type == "case"]
    summary = {
        "source_suites": len(suite_specs),
        "source_cases": sum(len(spec["case_ids"]) for spec in suite_specs),
        "executable_suites": len(executable_suites),
        "executable_cases": len(executable_cases),
        "executable_steps": sum(len(case.steps_snapshot) for case in executable_cases)
        + sum(
            len(suite.setup_steps_snapshot) + len(suite.teardown_steps_snapshot)
            for suite in executable_suites
        ),
        "executable_assertions": sum(
            len(step.get("assertions") or []) for case in executable_cases for step in case.steps_snapshot
        ),
        "na_suites": sum(1 for item in exclusions if item.target_type == "suite"),
        "na_cases": sum(1 for item in na_cases if item.suite_id is None),
        "na_steps": sum(1 for item in exclusions if item.target_type == "step"),
        "na_assertions": sum(1 for item in exclusions if item.target_type == "assertion"),
        "na_suite_steps": sum(1 for item in exclusions if item.target_type == "suite_step"),
        "na_suite_cases": sum(1 for item in na_cases if item.suite_id is not None),
        "overrides": applied_override_count + len(config["variable_overrides"]),
    }
    return ResolutionResult(
        profile_revision=profile.revision,
        test_asset_revision=project.test_asset_revision,
        profile_name=profile.name,
        release_version=release.version,
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
    """按一个套件规格生成 ResolvedSuite，并返回排除项与覆盖计数。"""
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
        suite = await resolution_repo.get_suite(db, suite_id)
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
                exclusions,
                override_count,
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
        setup_snapshot, teardown_snapshot, suite_elements, step_exclusions, step_override_count = (
            await _parse_suite_steps(
                db, request.project_id, suite.setup_steps or [], suite.teardown_steps or [],
                config, suite_id, suite_name, request.execution_variables,
            )
        )
        exclusions.extend(step_exclusions)
        override_count += step_override_count
        if skip_rule is not None:
            return (
                ResolvedSuite(
                    suite_id=suite_id, suite_name=suite_name, suite_order=suite_order,
                    is_virtual=False, setup_steps_snapshot=setup_snapshot,
                    teardown_steps_snapshot=teardown_snapshot, elements_snapshot=suite_elements,
                    cases=[], is_na=True,
                ),
                exclusions,
                override_count,
            )

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
        resolved_case, case_exclusions, case_override_count = await _resolve_case(
            db, request, config, case, suite_id, suite_name, case_pos, module_names
        )
        exclusions.extend(case_exclusions)
        override_count += case_override_count
        if resolved_case is not None:
            resolved_cases.append(resolved_case)

    if not resolved_cases:
        if not virtual:
            exclusions.append(
                ExclusionItem(
                    target_type="suite", suite_id=suite_id, case_id=None, node_key=None,
                    source_type="empty_after_filter", reason_code="other", reason_note="套件内无可执行用例",
                    display_snapshot={"name": suite_name, "key": str(suite_id), "suite_name": suite_name},
                )
            )
        return (
            ResolvedSuite(
                suite_id=suite_id, suite_name=suite_name, suite_order=suite_order,
                is_virtual=virtual, setup_steps_snapshot=setup_snapshot,
                teardown_steps_snapshot=teardown_snapshot, elements_snapshot=suite_elements,
                cases=[], is_na=True,
            ),
            exclusions,
            override_count,
        )
    return (
        ResolvedSuite(
            suite_id=suite_id, suite_name=suite_name, suite_order=suite_order,
            is_virtual=virtual, setup_steps_snapshot=setup_snapshot,
            teardown_steps_snapshot=teardown_snapshot, elements_snapshot=suite_elements,
            cases=resolved_cases, is_na=False,
        ),
        exclusions,
        override_count,
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
    """解析单个用例（步骤/断言/元素），返回 ResolvedCase 或整体 N/A。"""
    variables = await _merge_variables(
        db, request.project_id, suite_id, case, config, request.execution_variables
    )
    selected_steps = _select_steps_for_run(case.steps or [], request.run_options)
    step_overrides = _case_scoped(config, suite_id, case.id, "step")
    assertion_overrides = _case_scoped(config, suite_id, case.id, "assertion")
    override_count = len(
        {str(node.get("key") or "") for node in selected_steps if isinstance(node, dict)} & set(step_overrides)
    )
    source_assertions = [
        assertion
        for step in selected_steps
        for assertion in (step.get("assertions") or [])
        if isinstance(assertion, dict)
    ]
    override_count += len(
        {str(node.get("key") or "") for node in source_assertions} & set(assertion_overrides)
    )
    kept_steps, step_exclusions = _filter_and_patch(
        selected_steps, config["step_rules"].get((suite_id, case.id), {}), step_overrides,
        "step", case.id, case_name=case.name, suite_id=suite_id, suite_name=suite_name,
    )
    exclusions = [*step_exclusions]
    steps: list[dict] = []
    for node in kept_steps:
        raw_assertions = list(node.get("assertions") or [])
        node_without_assertions = {key: value for key, value in node.items() if key != "assertions"}
        resolved_step = _registry_validate_step(
            _render_node_with_context(node_without_assertions, variables, case.name)
        )
        kept_assertions, assertion_exclusions = _filter_and_patch(
            raw_assertions,
            config["assertion_rules"].get((suite_id, case.id), {}),
            assertion_overrides,
            "assertion",
            case.id,
            case_name=case.name,
            suite_id=suite_id,
            suite_name=suite_name,
        )
        exclusions.extend(assertion_exclusions)
        resolved_step["assertions"] = _assign_order(
            [
                _registry_validate_assertion(
                    _render_node_with_context(assertion, variables, case.name)
                )
                for assertion in kept_assertions
            ],
            order_offset=0,
        )
        steps.append(resolved_step)
    steps = _assign_order(steps, order_offset=0)
    if not steps:
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
    elements = await _resolve_element_snapshots(
        db, request.project_id, steps, config["element_overrides"], variables
    )
    override_count += sum(1 for element_id in elements if int(element_id) in config["element_overrides"])
    return (
        ResolvedCase(
            suite_id=suite_id,
            suite_name=suite_name,
            case_id=case.id,
            case_name=case.name,
            module_name=module_names.get(case.module_id) if case.module_id is not None else None,
            case_order=case_order,
            steps_snapshot=[_finalize_case_step(step) for step in steps],
            elements_snapshot=elements,
        ),
        exclusions,
        override_count,
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
    """套件前后置步骤：过滤、覆盖、渲染、校验并补全元素。"""
    variables = await _merge_suite_variables(db, project_id, suite_id, config, execution_variables)
    suite_rules = {
        node_key: rule for (current_suite_id, node_key), rule in config["skip_suite_step"].items()
        if current_suite_id == suite_id
    }
    suite_overrides = {
        node_key: patch for (current_suite_id, node_key), patch in config["suite_step_overrides"].items()
        if current_suite_id == suite_id
    }
    override_count = 0

    def process(nodes: list, phase: str) -> tuple[list[dict], list[ExclusionItem]]:
        nonlocal override_count
        kept, exclusions = _filter_and_patch(
            nodes or [], suite_rules, suite_overrides, "suite_step", None,
            case_name=None, suite_id=suite_id, suite_name=suite_name, phase=phase,
        )
        override_count += len(
            {str(node.get("key") or "") for node in (nodes or []) if isinstance(node, dict)}
            & set(suite_overrides)
        )
        steps = [_registry_validate_step(_render_node_with_context(node, variables, suite_name)) for node in kept]
        steps = _assign_order(steps, order_offset=0)
        return [_finalize_suite_step(step, phase) for step in steps], exclusions

    setup_snapshot, setup_exclusions = process(setup_nodes, "suite_setup")
    teardown_snapshot, teardown_exclusions = process(teardown_nodes, "suite_teardown")
    exclusions = [*setup_exclusions, *teardown_exclusions]
    elements = await _resolve_element_snapshots(
        db, project_id, [*setup_snapshot, *teardown_snapshot], config["element_overrides"], variables
    )
    override_count += sum(1 for element_id in elements if int(element_id) in config["element_overrides"])
    return setup_snapshot, teardown_snapshot, elements, exclusions, override_count
