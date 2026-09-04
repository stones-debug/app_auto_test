"""一次性清理已删除的列表视口参数。

默认只扫描并报告，不修改数据库。生产环境执行时必须显式传入 ``--apply``：

    uv run python scripts/cleanup_removed_viewport_params.py
    uv run python scripts/cleanup_removed_viewport_params.py --apply --project-id 42

脚本不生成备份文件，也不会连接配置以外的数据库。事务中的任意异常都会回滚。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    AppProfile,
    AppProfileNodeOverride,
    Project,
    TestCase,
    TestSuite,
)

REMOVED_PARAMETER_KEYS = frozenset({"viewport_element_id", "viewport_mode"})
PARAMETER_CONTAINER_KEYS = frozenset({"params", "parameters"})


def _clean_value(value: Any, *, drop_empty_containers: bool = False) -> tuple[Any, int]:
    """复制并清理参数对象，返回 ``(新值, 删除字段数)``。

    ``viewport_*`` 只有在 ``params``/``parameters`` 对象（及其递归子树）内才会
    被视为已删除参数。这样节点的业务字段、定位字段等同名属性不会被误删。
    """

    def visit(current: Any, in_parameters: bool) -> tuple[Any, int]:
        if isinstance(current, list):
            result: list[Any] = []
            removed = 0
            for item in current:
                new_item, item_removed = visit(item, in_parameters)
                result.append(new_item)
                removed += item_removed
            return result, removed
        if not isinstance(current, dict):
            return current, 0

        result: dict[Any, Any] = {}
        removed = 0
        for key, child in current.items():
            if in_parameters and key in REMOVED_PARAMETER_KEYS:
                removed += 1
                continue

            child_in_parameters = in_parameters or key in PARAMETER_CONTAINER_KEYS
            new_child, child_removed = visit(child, child_in_parameters)
            removed += child_removed
            if (
                drop_empty_containers
                and key in PARAMETER_CONTAINER_KEYS
                and child_in_parameters
                and isinstance(new_child, dict)
                and not new_child
            ):
                continue
            result[key] = new_child
        return result, removed

    return visit(copy.deepcopy(value), False)


def _column_stats() -> dict[str, int]:
    return {"scanned_rows": 0, "affected_rows": 0, "deleted_fields": 0}


def _new_stats() -> dict[str, Any]:
    return {
        "scanned_rows": 0,
        "affected_rows": 0,
        "deleted_fields": 0,
        "columns": defaultdict(_column_stats),
        "project_revisions_bumped": 0,
        "profile_revisions_bumped": 0,
    }


async def _clean_session(db: Any, project_id: int | None) -> dict[str, Any]:
    stats = _new_stats()
    changed_project_ids: set[int] = set()
    changed_profile_ids: set[int] = set()

    def project_filter(statement: Any, model: Any) -> Any:
        if project_id is not None:
            statement = statement.where(model.project_id == project_id)
        return statement

    cases = (
        await db.scalars(project_filter(select(TestCase), TestCase))
    ).all()
    for case in cases:
        stats["scanned_rows"] += 1
        row_changed = False
        for column_name in ("flow_nodes", "steps"):
            column = stats["columns"][f"test_cases.{column_name}"]
            column["scanned_rows"] += 1
            original = getattr(case, column_name)
            cleaned, removed = _clean_value(original)
            if removed:
                setattr(case, column_name, cleaned)
                row_changed = True
                column["affected_rows"] += 1
                column["deleted_fields"] += removed
                stats["deleted_fields"] += removed
        if row_changed:
            stats["affected_rows"] += 1
            changed_project_ids.add(case.project_id)

    suites = (
        await db.scalars(project_filter(select(TestSuite), TestSuite))
    ).all()
    for suite in suites:
        stats["scanned_rows"] += 1
        row_changed = False
        for column_name in ("setup_steps", "teardown_steps"):
            column = stats["columns"][f"test_suites.{column_name}"]
            column["scanned_rows"] += 1
            original = getattr(suite, column_name)
            cleaned, removed = _clean_value(original)
            if removed:
                setattr(suite, column_name, cleaned)
                row_changed = True
                column["affected_rows"] += 1
                column["deleted_fields"] += removed
                stats["deleted_fields"] += removed
        if row_changed:
            stats["affected_rows"] += 1
            changed_project_ids.add(suite.project_id)

    # A previously soft-deleted override is not part of execution and must not
    # be repeatedly counted (an empty patch is intentionally retained on that
    # tombstone because the check constraint forbids ``{}``).
    override_statement = select(AppProfileNodeOverride).where(
        AppProfileNodeOverride.deleted_at.is_(None)
    )
    if project_id is not None:
        override_statement = override_statement.join(
            AppProfile, AppProfile.id == AppProfileNodeOverride.profile_id
        ).where(AppProfile.project_id == project_id)
    overrides = (await db.scalars(override_statement)).all()
    override_column = stats["columns"]["app_profile_node_overrides.patch"]
    for override in overrides:
        stats["scanned_rows"] += 1
        override_column["scanned_rows"] += 1
        cleaned, removed = _clean_value(override.patch, drop_empty_containers=True)
        if not removed:
            continue
        stats["deleted_fields"] += removed
        override_column["affected_rows"] += 1
        override_column["deleted_fields"] += removed
        stats["affected_rows"] += 1
        changed_profile_ids.add(override.profile_id)
        if isinstance(cleaned, dict) and not cleaned:
            # Node overrides use the same soft-delete convention as the API. Do not
            # assign an invalid {} patch: ck_profile_node_override_patch forbids it.
            if override.deleted_at is None:
                override.deleted_at = datetime.now(UTC)
        else:
            override.patch = cleaned

    if changed_project_ids:
        projects = (
            await db.scalars(select(Project).where(Project.id.in_(changed_project_ids)))
        ).all()
        for project in projects:
            project.test_asset_revision += 1
        stats["project_revisions_bumped"] = len(projects)

    if changed_profile_ids:
        profiles = (
            await db.scalars(select(AppProfile).where(AppProfile.id.in_(changed_profile_ids)))
        ).all()
        for profile in profiles:
            profile.revision += 1
        stats["profile_revisions_bumped"] = len(profiles)

    stats["columns"] = dict(stats["columns"])
    return stats


async def run(*, apply: bool = False, project_id: int | None = None) -> dict[str, Any]:
    """执行清理并返回不含敏感数据的统计信息。

    ``apply=False`` 是安全的默认值：即使发现数据，也会显式 rollback。
    """

    _validate_project_id(project_id)
    async with SessionLocal() as db:
        try:
            stats = await _clean_session(db, project_id)
            if apply:
                await db.commit()
            else:
                await db.rollback()
            return stats
        except Exception:
            await db.rollback()
            raise


def _validate_project_id(project_id: int | None) -> None:
    if project_id is not None and project_id <= 0:
        raise ValueError("--project-id 必须是正整数")


def _positive_project_id(value: str) -> int:
    try:
        project_id = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--project-id 必须是正整数") from exc
    if project_id <= 0:
        raise argparse.ArgumentTypeError("--project-id 必须是正整数")
    return project_id


def _print_stats(stats: dict[str, Any], *, apply: bool) -> None:
    mode = "apply（已提交）" if apply else "dry-run（已回滚，未写入）"
    print(f"mode: {mode}")
    print(f"scanned_rows: {stats['scanned_rows']}")
    print(f"affected_rows: {stats['affected_rows']}")
    print(f"deleted_fields: {stats['deleted_fields']}")
    for name, column in sorted(stats["columns"].items()):
        print(
            f"{name}: scanned_rows={column['scanned_rows']} "
            f"affected_rows={column['affected_rows']} deleted_fields={column['deleted_fields']}"
        )
    print(f"project_revisions_bumped: {stats['project_revisions_bumped']}")
    print(f"profile_revisions_bumped: {stats['profile_revisions_bumped']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际提交清理；省略时默认 dry-run 并回滚（生产执行前请先检查 dry-run 输出）",
    )
    parser.add_argument("--project-id", type=_positive_project_id, help="只处理指定项目（正整数）")
    args = parser.parse_args()
    try:
        stats = asyncio.run(run(apply=args.apply, project_id=args.project_id))
    except Exception as exc:
        print(f"清理失败，事务已回滚：{exc}", file=sys.stderr)
        return 1
    _print_stats(stats, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
