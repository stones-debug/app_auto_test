"""幂等回填稳定节点 key 与项目默认 APP 档案。

用法：uv run python scripts/backfill_app_profiles.py --dry-run
      uv run python scripts/backfill_app_profiles.py --backup backups/case_nodes.jsonl
"""

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal  # noqa: E402
from app.models import AppProfile, AppProfileRelease, Project, TestCase  # noqa: E402

DEFAULT_PROFILE_NAME = "通用配置（待调整）"
DEFAULT_RELEASE_VERSION = "未标注历史版本"


def normalize_nodes(case_id: int, node_type: str, nodes: list) -> tuple[list, bool]:
    """保留合法且唯一的 UUID；其余按用例、类型和位置生成确定性 UUID。"""
    result = deepcopy(nodes)
    counts: dict[str, int] = {}
    for node in result:
        key = str(node.get("key") or "") if isinstance(node, dict) else ""
        counts[key] = counts.get(key, 0) + 1
    changed = False
    for index, node in enumerate(result, start=1):
        if not isinstance(node, dict):
            continue
        key = str(node.get("key") or "")
        try:
            valid = str(UUID(key)) == key.lower() and counts.get(key, 0) == 1
        except ValueError:
            valid = False
        if not valid:
            node["key"] = str(uuid5(NAMESPACE_URL, f"app-auto-test:{case_id}:{node_type}:{index}"))
            changed = True
    return result, changed


async def run(*, dry_run: bool, backup: Path | None) -> dict[str, int]:
    stats = {"cases_changed": 0, "profiles_created": 0, "releases_created": 0}
    backup_rows: list[dict] = []
    async with SessionLocal() as db:
        cases = (await db.execute(select(TestCase).order_by(TestCase.id))).scalars().all()
        for case in cases:
            steps, steps_changed = normalize_nodes(case.id, "step", case.steps or [])
            assertions, assertions_changed = normalize_nodes(
                case.id, "assertion", case.assertions or []
            )
            if not (steps_changed or assertions_changed):
                continue
            stats["cases_changed"] += 1
            backup_rows.append(
                {"case_id": case.id, "steps": case.steps or [], "assertions": case.assertions or []}
            )
            case.steps = steps
            case.assertions = assertions

        projects = (
            await db.execute(
                select(Project).where(
                    Project.deleted_at.is_(None), Project.status == "active"
                )
            )
        ).scalars().all()
        for project in projects:
            profile = (
                await db.execute(
                    select(AppProfile).where(
                        AppProfile.project_id == project.id,
                        AppProfile.name == DEFAULT_PROFILE_NAME,
                        AppProfile.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if profile is None:
                profile = AppProfile(
                    project_id=project.id,
                    name=DEFAULT_PROFILE_NAME,
                    code=f"compat-{uuid5(NAMESPACE_URL, f'app-auto-test-default-profile:{project.id}').hex[:16]}",
                    description="由迁移创建；继承全部公共测试资产，请按实际 APP 能力调整。",
                    inherit_all=True,
                    created_by=project.owner_id,
                    updated_by=project.owner_id,
                )
                db.add(profile)
                await db.flush()
                stats["profiles_created"] += 1
            release = (
                await db.execute(
                    select(AppProfileRelease).where(
                        AppProfileRelease.profile_id == profile.id,
                        AppProfileRelease.version == DEFAULT_RELEASE_VERSION,
                        AppProfileRelease.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if release is None:
                db.add(
                    AppProfileRelease(
                        profile_id=profile.id,
                        version=DEFAULT_RELEASE_VERSION,
                        description="用于兼容迁移前未区分 APP 发布版本的测试资产。",
                        created_by=project.owner_id,
                        updated_by=project.owner_id,
                    )
                )
                stats["releases_created"] += 1

        if dry_run:
            await db.rollback()
        else:
            await db.commit()

    if backup is not None and backup_rows:
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("w", encoding="utf-8") as stream:
            for row in backup_rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="统计并回滚，不修改数据库")
    parser.add_argument("--backup", type=Path, help="写入变更前的用例节点 JSONL 备份")
    args = parser.parse_args()
    print(await run(dry_run=args.dry_run, backup=args.backup))


if __name__ == "__main__":
    asyncio.run(main())
