"""APP 档案兼容回填的业务编排。"""

from copy import deepcopy
from uuid import NAMESPACE_URL, UUID, uuid5

from app.repositories import cases as cases_repo
from app.repositories import projects as projects_repo
from app.repositories.app_profiles import profiles as profiles_repo
from app.repositories.app_profiles import releases as releases_repo

DEFAULT_PROFILE_NAME = "通用配置（待调整）"
DEFAULT_RELEASE_VERSION = "未标注历史版本"


def normalize_nodes(case_id: int, node_type: str, nodes: list) -> tuple[list, bool]:
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


async def backfill_profiles(db, *, dry_run: bool) -> tuple[dict[str, int], list[dict]]:
    stats = {"cases_changed": 0, "profiles_created": 0, "releases_created": 0}
    backup_rows: list[dict] = []
    for case in await cases_repo.list_all(db):
        steps, steps_changed = normalize_nodes(case.id, "step", case.steps or [])
        old_assertions = getattr(case, "assertions", []) or []
        assertions, assertions_changed = normalize_nodes(case.id, "assertion", old_assertions)
        if steps_changed or assertions_changed:
            stats["cases_changed"] += 1
            backup_rows.append({"case_id": case.id, "steps": case.steps or [], "assertions": old_assertions})
            case.steps = steps
            case.__dict__["assertions"] = assertions
    for project in await projects_repo.list_active(db):
        profile = await profiles_repo.find_named(db, project.id, DEFAULT_PROFILE_NAME)
        if profile is None:
            profile = await profiles_repo.create(db, project_id=project.id, name=DEFAULT_PROFILE_NAME, code=f"compat-{uuid5(NAMESPACE_URL, f'app-auto-test-default-profile:{project.id}').hex[:16]}", description="由迁移创建；继承全部公共测试资产，请按实际 APP 能力调整。", inherit_all=True, user_id=project.owner_id)
            stats["profiles_created"] += 1
        release = await releases_repo.find_version(db, profile.id, DEFAULT_RELEASE_VERSION)
        if release is None:
            await releases_repo.create(db, profile_id=profile.id, version=DEFAULT_RELEASE_VERSION, build_number=None, description="用于兼容迁移前未区分 APP 发布版本的测试资产。", user_id=project.owner_id)
            stats["releases_created"] += 1
    if dry_run:
        await db.rollback()
    else:
        await db.commit()
    return stats, backup_rows
