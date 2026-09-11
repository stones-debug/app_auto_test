"""Create/consume opaque, short-lived preview snapshots."""

import hashlib
import json
import secrets
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import ExecutionPrepare
from app.repositories import execution_prepares as prepares_repo
from app.services.profile_resolver import (
    ExclusionItem,
    ResolutionResult,
    ResolvedCase,
    ResolvedSuite,
)

PREPARE_PAYLOAD_VERSION = 1


def token_hash(token: str) -> str:
    # Hash arbitrary user input as UTF-8; malformed/unknown tokens become a
    # normal lookup miss and are reported as EXECUTION_PREPARE_INVALID.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def canonical_target(
    *,
    target_type: str,
    target_ids: list[int],
    target_scope: str,
    context_suite_id: int | None,
    resolved_ids: list[int] | None = None,
    excluded_suite_ids: list[int] | None = None,
) -> dict[str, Any]:
    normalized_excluded = sorted({int(value) for value in (excluded_suite_ids or [])})
    return {
        "type": target_type,
        "ids": list(dict.fromkeys(int(value) for value in (resolved_ids if resolved_ids is not None else target_ids))),
        "target_scope": target_scope,
        "context_suite_id": context_suite_id,
        "excluded_suite_ids": normalized_excluded,
    }


def request_hash(*, target: dict[str, Any], parameters: dict[str, Any]) -> str:
    value = json.dumps(
        {"target": target, "parameters": parameters},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def serialize_result(result: ResolutionResult) -> dict[str, Any]:
    def case_payload(case: ResolvedCase) -> dict[str, Any]:
        return {
            "suite_id": case.suite_id,
            "suite_name": case.suite_name,
            "case_id": case.case_id,
            "case_name": case.case_name,
            "module_name": case.module_name,
            "case_order": case.case_order,
            "flow_snapshot": _jsonable(deepcopy(case.flow_snapshot)),
            "elements_snapshot": _jsonable(deepcopy(case.elements_snapshot)),
        }

    def suite_payload(suite: ResolvedSuite) -> dict[str, Any]:
        return {
            "suite_id": suite.suite_id,
            "suite_name": suite.suite_name,
            "suite_order": suite.suite_order,
            "is_virtual": suite.is_virtual,
            "setup_steps_snapshot": _jsonable(deepcopy(suite.setup_steps_snapshot)),
            "teardown_steps_snapshot": _jsonable(deepcopy(suite.teardown_steps_snapshot)),
            "elements_snapshot": _jsonable(deepcopy(suite.elements_snapshot)),
            "is_na": suite.is_na,
            "cases": [case_payload(case) for case in suite.cases],
        }

    return {
        "version": PREPARE_PAYLOAD_VERSION,
        "profile_revision": result.profile_revision,
        "test_asset_revision": result.test_asset_revision,
        "profile_name": result.profile_name,
        "release_version": result.release_version,
        "suites": [suite_payload(suite) for suite in result.suites],
        "exclusions": [
            {
                "target_type": exclusion.target_type,
                "suite_id": exclusion.suite_id,
                "case_id": exclusion.case_id,
                "node_key": str(exclusion.node_key) if exclusion.node_key else None,
                "source_type": exclusion.source_type,
                "reason_code": exclusion.reason_code,
                "reason_note": exclusion.reason_note,
                "display_snapshot": _jsonable(deepcopy(exclusion.display_snapshot)),
                "phase": exclusion.phase,
                "occurrence_order": exclusion.occurrence_order,
            }
            for exclusion in result.exclusions
        ],
        "summary": _jsonable(deepcopy(result.summary)),
        "warnings": _jsonable(deepcopy(result.warnings)),
        "sensitive_variable_names": list(result.sensitive_variable_names),
    }


def deserialize_result(payload: dict[str, Any]) -> ResolutionResult:
    if payload.get("version") != PREPARE_PAYLOAD_VERSION:
        raise ValueError("不支持的预检快照版本")

    def build_case(value: dict[str, Any]) -> ResolvedCase:
        return ResolvedCase(
            suite_id=value.get("suite_id"),
            suite_name=value.get("suite_name"),
            case_id=int(value["case_id"]),
            case_name=str(value["case_name"]),
            module_name=value.get("module_name"),
            case_order=int(value["case_order"]),
            flow_snapshot=deepcopy(value.get("flow_snapshot") or []),
            elements_snapshot=deepcopy(value.get("elements_snapshot") or {}),
        )

    suites = []
    for value in payload.get("suites") or []:
        suites.append(
            ResolvedSuite(
                suite_id=value.get("suite_id"),
                suite_name=str(value["suite_name"]),
                suite_order=int(value["suite_order"]),
                is_virtual=bool(value.get("is_virtual", False)),
                setup_steps_snapshot=deepcopy(value.get("setup_steps_snapshot") or []),
                teardown_steps_snapshot=deepcopy(value.get("teardown_steps_snapshot") or []),
                elements_snapshot=deepcopy(value.get("elements_snapshot") or {}),
                cases=[build_case(case) for case in value.get("cases") or []],
                is_na=bool(value.get("is_na", False)),
            )
        )

    exclusions = []
    for value in payload.get("exclusions") or []:
        node_key = value.get("node_key")
        exclusions.append(
            ExclusionItem(
                target_type=value["target_type"],
                suite_id=value.get("suite_id"),
                case_id=value.get("case_id"),
                node_key=UUID(node_key) if node_key else None,
                source_type=value["source_type"],
                reason_code=value["reason_code"],
                reason_note=value.get("reason_note"),
                display_snapshot=deepcopy(value.get("display_snapshot") or {}),
                phase=value.get("phase"),
                occurrence_order=value.get("occurrence_order"),
            )
        )
    return ResolutionResult(
        profile_revision=int(payload["profile_revision"]),
        test_asset_revision=int(payload["test_asset_revision"]),
        profile_name=str(payload["profile_name"]),
        release_version=str(payload.get("release_version") or ""),
        suites=suites,
        exclusions=exclusions,
        summary={str(key): int(value) for key, value in (payload.get("summary") or {}).items()},
        warnings=deepcopy(payload.get("warnings") or []),
        sensitive_variable_names=[str(name) for name in payload.get("sensitive_variable_names") or []],
    )


async def create_prepare(
    db: AsyncSession,
    *,
    user_id: int,
    project_id: int,
    app_profile_id: int,
    app_release_id: int,
    app_release_version: str,
    profile_revision: int,
    test_asset_revision: int,
    target: dict[str, Any],
    device_id: int | None,
    parameters: dict[str, Any],
    result: ResolutionResult,
) -> tuple[str, datetime]:
    payload = serialize_result(result)
    payload_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if payload_size > settings.max_execution_snapshot_bytes:
        raise ValueError("预检快照超过配置上限")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.execution_prepare_ttl_seconds)
    await prepares_repo.create(
        db,
        ExecutionPrepare(
            token_hash=token_hash(token),
            user_id=user_id,
            project_id=project_id,
            app_profile_id=app_profile_id,
            app_release_id=app_release_id,
            app_release_version=app_release_version,
            profile_revision=profile_revision,
            test_asset_revision=test_asset_revision,
            target=deepcopy(target),
            device_id=device_id,
            parameters=deepcopy(parameters),
            request_hash=request_hash(target=target, parameters=parameters),
            resolution_version=PREPARE_PAYLOAD_VERSION,
            resolution_payload=payload,
            expires_at=expires_at,
        ),
    )
    # 有界 opportunistic cleanup；入口仍以 expires_at 实时校验，不依赖清理任务。
    await prepares_repo.delete_expired(db, limit=10)
    await db.commit()
    return token, expires_at


async def lock_for_create(db: AsyncSession, token: str) -> ExecutionPrepare | None:
    """Lock a token row; callers validate ownership and request semantics."""
    return await prepares_repo.get_for_update(db, token_hash(token))


async def peek(db: AsyncSession, token: str) -> ExecutionPrepare | None:
    return await prepares_repo.get(db, token_hash(token))
