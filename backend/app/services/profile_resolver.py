"""APP 档案配置解析引擎的对外类型与编排入口。

节点处理位于 ``profile_resolver_nodes``，数据库加载与元素/变量组装位于
``profile_resolver_load``。本模块保留历史公开导入路径，并负责将各阶段编排成
``ResolutionResult``。
"""

import logging
import time
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
    ResolutionLoadContext,
)
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
    load_user_variable_overrides as _load_user_variable_overrides,
)
from app.services.profile_resolver_load import merge_suite_variables as _merge_suite_variables
from app.services.profile_resolver_load import merge_variables as _merge_variables
from app.services.profile_resolver_load import (
    prepare_load_context as _prepare_load_context,
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
    runtime_variable_names,
    sensitive_parameter_paths,
    validate_node_patch,
    variable_references,
)
from app.services.profile_resolver_nodes import (
    assign_order as _assign_order,
)
from app.services.profile_resolver_nodes import filter_and_patch as _filter_and_patch_impl
from app.services.profile_resolver_nodes import (
    select_steps_for_run as _select_steps_for_run,
)

logger = logging.getLogger("app.profile_resolver")


def _unique_context_membership(
    case_id: int,
    candidates: list[tuple[int, int, dict[str, str]]],
    suite_id: int,
) -> tuple[int | None, int, dict[str, str]]:
    if len(candidates) > 1:
        raise ProfileRuleError(
            "OCCURRENCE_REQUIRED",
            f"用例 {case_id} 在套件 {suite_id} 存在多个编排项，必须指定 suite_case_id",
        )
    return candidates[0] if candidates else (None, case_id, {})

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
    "variable_references",
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
    context_suite_case_id: int | None = None
    target_scope: Literal["explicit", "profile_all"] = "explicit"
    # profile_all 的“默认全选”补集。显式目标禁止携带该字段。
    excluded_suite_ids: list[int] = field(default_factory=list)
    user_id: int | None = None


@dataclass(frozen=True)
class ResolvedCase:
    suite_id: int | None
    suite_case_id: int | None
    suite_name: str | None
    case_id: int
    case_name: str
    module_name: str | None
    case_order: int
    flow_snapshot: list[dict[str, Any]]
    elements_snapshot: dict[str, dict[str, Any]]

    @property
    def steps_snapshot(self) -> list[dict[str, Any]]:
        """旧详情/报告调用方的只读嵌套投影；执行只使用 flow_snapshot。"""
        steps: list[dict[str, Any]] = []
        for node in self.flow_snapshot:
            if node.get("kind") == "assertion":
                if steps:
                    assertion = {
                        key: value for key, value in node.items() if key not in {"kind", "order", "phase"}
                    }
                    assertion["order"] = len(steps[-1].get("assertions") or []) + 1
                    steps[-1].setdefault("assertions", []).append(assertion)
                continue
            step = {key: value for key, value in node.items() if key != "kind"}
            step["order"] = len(steps) + 1
            steps.append(step)
        return steps

    @property
    def assertions_snapshot(self) -> list[dict[str, Any]]:
        return [node for node in self.flow_snapshot if node.get("kind") == "assertion"]


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
    occurrence_order: int | None = None
    suite_case_id: int | None = None


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
    # 经过存在性/软删除/直接套件跳过过滤后的取消集合，供预检 canonical
    # target 与正式执行 parameters 使用；请求原始集合不应把被忽略 ID 带入审计。
    normalized_excluded_suite_ids: list[int] = field(default_factory=list)
    sensitive_variable_names: list[str] = field(default_factory=list)

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


def _membership_scoped(
    config: dict, membership_id: int | None, kind: str
) -> dict[str, dict[str, Any]]:
    """读取当前编排项（suite_case_id）的节点覆盖；重复编排的同一用例互不影响。"""
    if membership_id is None:
        return {}
    bucket = config["membership_step_overrides"] if kind == "step" else config["membership_assertion_overrides"]
    return dict(bucket.get(membership_id, {}))


async def resolve(
    request: ResolutionRequest, db: AsyncSession, cache: _LRUCache | None = None
) -> ResolutionResult:
    resolve_started = time.monotonic()
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

    normalized_excluded = sorted({int(value) for value in request.excluded_suite_ids})
    if request.target_scope == "profile_all":
        if request.target_type != "batch" or request.target_ids:
            raise ProfileRuleError("EXECUTION_TARGET_INVALID", "profile_all 仅支持空 ids 的批量套件目标")
        try:
            normalized_excluded = await resolution_repo.validate_profile_all_excluded_suite_ids(
                db, project_id=request.project_id, suite_ids=normalized_excluded
            )
        except ValueError as err:
            raise ProfileRuleError("EXECUTION_TARGET_INVALID", str(err)) from None
    elif normalized_excluded:
        raise ProfileRuleError("EXECUTION_TARGET_INVALID", "excluded_suite_ids 仅支持 profile_all")

    cache_key = (request.project_id, project.test_asset_revision, request.profile_id, profile.revision)
    shared_config = cache.get(cache_key)
    config_cache_hit = shared_config is not None
    if shared_config is None:
        shared_config = await _load_config(db, request.profile_id)
        cache.set(cache_key, shared_config)
    # User overrides are deliberately read outside the shared config cache:
    # they do not advance profile.revision and must be visible immediately.
    config = {**shared_config, "user_variable_overrides": {}}
    if request.user_id is not None:
        config["user_variable_overrides"] = await _load_user_variable_overrides(
            db, profile_id=request.profile_id, user_id=request.user_id
        )

    effective_context_suite_id = request.context_suite_id
    if request.context_suite_case_id is not None:
        if request.target_type != "case" or len(request.target_ids) != 1:
            raise ProfileRuleError(
                "EXECUTION_CONTEXT_INVALID",
                "context_suite_case_id 仅支持单个用例目标",
            )
        context_membership = await resolution_repo.get_suite_case(
            db, request.context_suite_case_id
        )
        if context_membership is None or context_membership.case_id != request.target_ids[0]:
            raise ProfileRuleError(
                "EXECUTION_CONTEXT_INVALID",
                "context_suite_case_id 与执行用例不匹配",
            )
        if (
            effective_context_suite_id is not None
            and effective_context_suite_id != context_membership.suite_id
        ):
            raise ProfileRuleError(
                "EXECUTION_CONTEXT_INVALID",
                "context_suite_id 与 context_suite_case_id 不匹配",
            )
        effective_context_suite_id = context_membership.suite_id

    suite_rows = []
    suite_ids: list[int] = []
    suite_specs: list[dict] = []
    load_started = time.monotonic()
    if request.target_type == "case":
        case_ids = list(dict.fromkeys(request.target_ids))
        if effective_context_suite_id is not None:
            if effective_context_suite_id in config["skip_suite"]:
                # 单用例携带已直接跳过的套件上下文时与工作台继承状态
                # 一致：目标不可执行，不留下整套 N/A 排除记录。
                suite_specs = []
                cases_by_id = {}
                case_ids = []
                # 下面的分支只负责构造普通 context suite；空目标直接
                # 进入 PROFILE_EMPTY。
                context_skipped = True
            else:
                context_skipped = False
            # 单用例带套件上下文必须有唯一编排项；重复编排不能靠排序猜测身份。
            if not context_skipped:
                members_map = await resolution_repo.list_suite_memberships(
                    db, [effective_context_suite_id]
                )
                members_by_case: dict[int, list[tuple[int, int, dict[str, str]]]] = {}
                for member in members_map.get(effective_context_suite_id, []):
                    members_by_case.setdefault(member[1], []).append(member)
                if request.context_suite_case_id is not None:
                    selected = [
                        member
                        for member in members_map.get(effective_context_suite_id, [])
                        if member[0] == request.context_suite_case_id
                    ]
                    if not selected:
                        raise ProfileRuleError(
                            "EXECUTION_CONTEXT_INVALID",
                            "context_suite_case_id 不属于指定套件",
                        )
                    members = selected
                else:
                    members = [
                        _unique_context_membership(
                            case_id, members_by_case.get(case_id, []), effective_context_suite_id
                        )
                        for case_id in case_ids
                    ]
                suite_specs = [
                    {"suite_id": effective_context_suite_id, "virtual": False, "members": members}
                ]
        else:
            suite_specs = [
                {
                    "suite_id": None,
                    "virtual": True,
                    "members": [(None, case_id, {}) for case_id in case_ids],
                }
            ]
        cases_by_id = await _load_cases_by_id(db, case_ids)
    else:
        if request.target_scope == "profile_all":
            all_suite_rows = await resolution_repo.list_suites(db, request.project_id)
            active_suite_ids = {suite.id for suite in all_suite_rows}
            normalized_excluded = [
                suite_id for suite_id in normalized_excluded
                if suite_id in active_suite_ids and suite_id not in config["skip_suite"]
            ]
            # 套件级跳过是“不可选目标”，不是执行中的 N/A 节点。先在
            # _build_suite 前过滤，因而不会展开用例，也不会产生 exclusion
            # 或报告 not_applicable_suites。
            suite_rows = [
                suite for suite in all_suite_rows
                if suite.id not in normalized_excluded
                and suite.id not in config["skip_suite"]
            ]
            suite_ids = [suite.id for suite in suite_rows]
        else:
            suite_ids = list(dict.fromkeys(request.target_ids))
            suite_rows = []
            # 显式单套件入口遇到档案直接跳过也视为不可执行目标；与
            # profile_all 保持一致，整套跳过不进入报告 N/A。
            if suite_ids:
                explicit_rows = await resolution_repo.load_suites_by_ids(
                    db, project_id=request.project_id, suite_ids=suite_ids
                )
                suite_ids = [suite_id for suite_id in suite_ids if suite_id not in config["skip_suite"]]
                suite_rows = [
                    explicit_rows[suite_id] for suite_id in suite_ids if suite_id in explicit_rows
                ]
        memberships, cases_by_id = await _collect_suite_cases(db, suite_ids)
        suite_specs = [
            {"suite_id": suite_id, "virtual": False, "members": memberships.get(suite_id, [])}
            for suite_id in suite_ids
        ]

    if request.target_type == "case":
        suite_rows = []
        if effective_context_suite_id is not None:
            suite = await resolution_repo.load_suites_by_ids(
                db, project_id=request.project_id, suite_ids=[effective_context_suite_id]
            )
            suite_rows = list(suite.values())
    else:
        if not suite_rows:
            suite_rows = list(
                (
                    await resolution_repo.load_suites_by_ids(
                        db, project_id=request.project_id, suite_ids=suite_ids
                    )
                ).values()
            )
    load_context = await _prepare_load_context(
        db,
        project_id=request.project_id,
        suites=suite_rows,
        cases=cases_by_id,
        config=config,
    )
    logger.info(
        "profile_resolution stage=load project_id=%s profile_id=%s target_scope=%s "
        "suite_count=%s case_count=%s cache=%s elapsed_ms=%.1f",
        request.project_id,
        request.profile_id,
        request.target_scope,
        len(suite_specs),
        len(cases_by_id),
        "hit" if config_cache_hit else "miss",
        (time.monotonic() - load_started) * 1000,
    )

    module_ids = {case.module_id for case in cases_by_id.values() if case.module_id is not None}
    module_names: dict[int, str] = {}
    if module_ids:
        module_names = await resolution_repo.get_project_modules(db, module_ids)

    applied_override_count = 0
    suites: list[ResolvedSuite] = []
    exclusions: list[ExclusionItem] = []
    for suite_order, spec in enumerate(suite_specs, start=1):
        resolved_suite, suite_exclusions, override_count = await _build_suite(
            db, request, config, spec, suite_order, cases_by_id, module_names, load_context
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
        "source_cases": sum(len(spec["members"]) for spec in suite_specs),
        "executable_suites": len(executable_suites),
        "executable_cases": len(executable_cases),
        "executable_steps": sum(
            sum(1 for node in case.flow_snapshot if node.get("kind") == "action")
            for case in executable_cases
        )
        + sum(
            len(suite.setup_steps_snapshot) + len(suite.teardown_steps_snapshot)
            for suite in executable_suites
        ),
        "executable_assertions": sum(len(case.assertions_snapshot) for case in executable_cases),
        "na_suites": sum(1 for item in exclusions if item.target_type == "suite"),
        "na_cases": sum(1 for item in na_cases if item.suite_id is None),
        "na_steps": sum(1 for item in exclusions if item.target_type == "step"),
        "na_assertions": sum(1 for item in exclusions if item.target_type == "assertion"),
        "na_suite_steps": sum(1 for item in exclusions if item.target_type == "suite_step"),
        "na_suite_cases": sum(1 for item in na_cases if item.suite_id is not None),
        "overrides": applied_override_count + len(config["variable_overrides"]),
    }
    result = ResolutionResult(
        profile_revision=profile.revision,
        test_asset_revision=project.test_asset_revision,
        profile_name=profile.name,
        release_version=release.version,
        suites=suites,
        exclusions=exclusions,
        summary=summary,
        warnings=[],
        normalized_excluded_suite_ids=normalized_excluded,
        sensitive_variable_names=sorted(load_context.sensitive_variable_names),
    )
    logger.info(
        "profile_resolution stage=resolve project_id=%s profile_id=%s target_scope=%s "
        "suite_count=%s case_count=%s node_count=%s elapsed_ms=%.1f",
        request.project_id,
        request.profile_id,
        request.target_scope,
        len(result.suites),
        len(executable_cases),
        result.summary["executable_steps"],
        (time.monotonic() - resolve_started) * 1000,
    )
    return result


async def _build_suite(
    db: AsyncSession,
    request: ResolutionRequest,
    config: dict,
    spec: dict,
    suite_order: int,
    cases_by_id: dict[int, TestCase],
    module_names: dict[int, str],
    load_context: ResolutionLoadContext,
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
        suite = load_context.suites.get(suite_id)
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
        setup_snapshot, teardown_snapshot, suite_elements, step_exclusions, step_override_count = (
            await _parse_suite_steps(
                db, request.project_id, suite.setup_steps or [], suite.teardown_steps or [],
                config, suite_id, suite_name, request.execution_variables,
                load_context,
            )
        )
        exclusions.extend(step_exclusions)
        override_count += step_override_count

    resolved_cases: list[ResolvedCase] = []
    for case_pos, (membership_id, case_id, membership_overrides) in enumerate(spec["members"], start=1):
        case = cases_by_id.get(case_id)
        if case is None:
            continue
        if not virtual:
            case_rule = config["skip_case"].get(membership_id)
            if case_rule is not None:
                exclusions.append(
                    ExclusionItem(
                        target_type="case", suite_id=suite_id, suite_case_id=membership_id,
                        case_id=case.id, node_key=None,
                        source_type="direct", reason_code=case_rule.reason_code,
                        reason_note=case_rule.reason_note,
                        display_snapshot={
                            "name": case.name, "key": str(case.id),
                            "suite_name": suite_name, "case_name": case.name,
                        },
                        occurrence_order=case_pos,
                    )
                )
                continue
        resolved_case, case_exclusions, case_override_count = await _resolve_case(
            db, request, config, case, suite_id, suite_name, case_pos, module_names, load_context,
            membership_id=membership_id, membership_overrides=membership_overrides,
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
    load_context: ResolutionLoadContext,
    membership_id: int | None = None,
    membership_overrides: dict[str, str] | None = None,
) -> tuple[ResolvedCase | None, list[ExclusionItem], int]:
    """解析单个用例（步骤/断言/元素），返回 ResolvedCase 或整体 N/A。"""
    variables = await _merge_variables(
        db, request.project_id, suite_id, case, config, request.execution_variables, load_context,
        case_occurrence=case_order, membership_overrides=membership_overrides,
        occurrence_profile_overrides=(
            config.get("occurrence_variable_overrides", {}).get(membership_id, {})
            if membership_id is not None else {}
        ),
    )
    selected_steps = _select_steps_for_run(case.flow_nodes or case.steps or [], request.run_options)
    step_overrides = _membership_scoped(config, membership_id, "step")
    assertion_overrides = _membership_scoped(config, membership_id, "assertion")
    override_count = len(
        {str(node.get("key") or "") for node in selected_steps if isinstance(node, dict)} & set(step_overrides)
    )
    if membership_id is not None:
        override_count += len(config.get("occurrence_variable_overrides", {}).get(membership_id, {}))
    source_assertions = [node for node in selected_steps if node.get("kind") == "assertion"]
    override_count += len(
        {str(node.get("key") or "") for node in source_assertions} & set(assertion_overrides)
    )
    kept_nodes: list[dict] = []
    exclusions: list[ExclusionItem] = []
    runtime_variables: set[str] = set()
    for node in selected_steps:
        is_assertion = node.get("kind") == "assertion" or "type" in node
        node_type = "assertion" if is_assertion else "step"
        rules = config["assertion_rules" if is_assertion else "step_rules"].get(membership_id, {})
        overrides = assertion_overrides if is_assertion else step_overrides
        kept, node_exclusions = _filter_and_patch(
            [node], rules, overrides, node_type, case.id,
            case_name=case.name, suite_id=suite_id, suite_name=suite_name,
            suite_case_id=membership_id,
            occurrence_order=case_order,
        )
        exclusions.extend(node_exclusions)
        if not kept:
            continue
        node = kept[0]
        render_variables = dict(variables)
        rendered = _render_node_with_context(node, render_variables, case.name, runtime_variables)
        rendered["sensitive_parameter_paths"] = sensitive_parameter_paths(
            node, load_context.sensitive_variable_names
        )
        validated = _registry_validate_assertion(rendered) if is_assertion else _registry_validate_step(rendered)
        kept_nodes.append(validated)
        runtime_variables.update(runtime_variable_names(validated))
    nodes = _assign_order(kept_nodes, order_offset=0)
    if not nodes:
        exclusions.append(
            ExclusionItem(
                target_type="case", suite_id=suite_id, suite_case_id=membership_id,
                case_id=case.id, node_key=None,
                source_type="empty_after_filter", reason_code="other", reason_note="过滤后无可执行内容",
                display_snapshot={
                    "name": case.name, "key": str(case.id),
                    "suite_name": suite_name, "case_name": case.name,
                },
                occurrence_order=case_order,
            )
        )
        return None, exclusions, override_count
    elements = await _resolve_element_snapshots(
        db, request.project_id, nodes, config["element_overrides"], variables, runtime_variables,
        load_context.elements,
    )
    override_count += sum(1 for element_id in elements if int(element_id) in config["element_overrides"])
    return (
        ResolvedCase(
            suite_id=suite_id,
            suite_case_id=membership_id,
            suite_name=suite_name,
            case_id=case.id,
            case_name=case.name,
            module_name=module_names.get(case.module_id) if case.module_id is not None else None,
            case_order=case_order,
            flow_snapshot=[_finalize_case_step(node) for node in nodes],
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
    load_context: ResolutionLoadContext,
) -> tuple[list[dict], list[dict], dict[str, dict[str, Any]], list[ExclusionItem], int]:
    """套件前后置步骤：过滤、覆盖、渲染、校验并补全元素。"""
    variables = await _merge_suite_variables(
        db, project_id, suite_id, config, execution_variables, load_context
    )
    suite_rules = {
        node_key: rule for (current_suite_id, node_key), rule in config["skip_suite_step"].items()
        if current_suite_id == suite_id
    }
    suite_overrides = {
        node_key: patch for (current_suite_id, node_key), patch in config["suite_step_overrides"].items()
        if current_suite_id == suite_id
    }
    override_count = 0
    runtime_variables: set[str] = set()

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
        steps: list[dict] = []
        for node in kept:
            render_variables = dict(variables)
            rendered = _render_node_with_context(node, render_variables, suite_name, runtime_variables)
            rendered["sensitive_parameter_paths"] = sensitive_parameter_paths(
                node, load_context.sensitive_variable_names
            )
            validated = _registry_validate_step(rendered)
            steps.append(validated)
            runtime_variables.update(runtime_variable_names(validated))
        steps = _assign_order(steps, order_offset=0)
        return [_finalize_suite_step(step, phase) for step in steps], exclusions

    setup_snapshot, setup_exclusions = process(setup_nodes, "suite_setup")
    teardown_snapshot, teardown_exclusions = process(teardown_nodes, "suite_teardown")
    exclusions = [*setup_exclusions, *teardown_exclusions]
    elements = await _resolve_element_snapshots(
        db,
        project_id,
        [*setup_snapshot, *teardown_snapshot],
        config["element_overrides"],
        variables,
        runtime_variables,
        load_context.elements,
    )
    override_count += sum(1 for element_id in elements if int(element_id) in config["element_overrides"])
    return setup_snapshot, teardown_snapshot, elements, exclusions, override_count
