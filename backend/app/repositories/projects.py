"""项目、项目成员和项目资产统计数据库访问。"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectMember, TestCase, TestElement, TestSuite, User


async def touch_asset_revision(db: AsyncSession, project_id: int) -> int:
    result = await db.execute(
        update(Project)
        .where(Project.id == project_id, Project.deleted_at.is_(None))
        .values(
            test_asset_revision=Project.test_asset_revision + 1,
            updated_at=func.now(),
        )
        .returning(Project.test_asset_revision)
    )
    new_revision = result.scalar_one_or_none()
    if new_revision is None:
        raise ValueError(f"项目不存在: {project_id}")
    return new_revision


async def compare_and_bump_asset_revision(
    db: AsyncSession, project_id: int, expected_revision: int, actor_id: int | None = None
) -> int | None:
    """按 expected revision 原子递增项目资产版本。"""
    result = await db.execute(
        update(Project)
        .where(Project.id == project_id, Project.test_asset_revision == expected_revision)
        .values(test_asset_revision=Project.test_asset_revision + 1, updated_at=func.now())
        .returning(Project.test_asset_revision)
    )
    return result.scalar_one_or_none()


async def bump_asset_revision(db: AsyncSession, project_id: int) -> int:
    return await touch_asset_revision(db, project_id)


async def bump_all_asset_revisions(db: AsyncSession) -> None:
    await touch_all_asset_revisions(db)


async def touch_all_asset_revisions(db: AsyncSession) -> None:
    await db.execute(
        update(Project)
        .where(Project.deleted_at.is_(None))
        .values(
            test_asset_revision=Project.test_asset_revision + 1,
            updated_at=func.now(),
        )
    )


async def load_asset_counts(
    db: AsyncSession, project_ids: list[int]
) -> dict[int, dict[str, int]]:
    result = {
        project_id: {"case_count": 0, "element_count": 0, "suite_count": 0}
        for project_id in project_ids
    }
    if not project_ids:
        return result
    for model, key in (
        (TestCase, "case_count"),
        (TestElement, "element_count"),
        (TestSuite, "suite_count"),
    ):
        rows = await db.execute(
            select(model.project_id, func.count())
            .where(model.project_id.in_(project_ids), model.deleted_at.is_(None))
            .group_by(model.project_id)
        )
        for project_id, count in rows.all():
            result[project_id][key] = count
    return result


async def get_by_id(db: AsyncSession, project_id: int) -> Project | None:
    return await db.get(Project, project_id)


async def list_visible(
    db: AsyncSession,
    *,
    user_id: int,
    visibility: str,
    scope: str,
    role: str,
    keyword: str,
    offset: int,
    limit: int,
) -> tuple[int, list[Project]]:
    owned_or_member = select(Project.id).where(
        (Project.owner_id == user_id)
        | (Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)))
    )
    condition = (Project.deleted_at.is_(None)) & (
        (Project.visibility == "public") | (Project.id.in_(owned_or_member))
    )
    effective_scope = scope or visibility
    if effective_scope == "public":
        condition = (Project.deleted_at.is_(None)) & (Project.visibility == "public")
    elif effective_scope == "mine":
        condition = (Project.deleted_at.is_(None)) & (Project.owner_id == user_id)
    if keyword:
        condition &= Project.name.ilike(f"%{keyword.strip()}%")
    if role == "owner":
        condition &= Project.owner_id == user_id
    elif role in {"admin", "member"}:
        condition &= Project.id.in_(
            select(ProjectMember.project_id).where(
                ProjectMember.user_id == user_id, ProjectMember.role == role
            )
        )
    elif role == "viewer":
        all_memberships = select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
        viewer_memberships = select(ProjectMember.project_id).where(
            ProjectMember.user_id == user_id, ProjectMember.role == "viewer"
        )
        condition &= Project.id.in_(viewer_memberships) | (
            (Project.visibility == "public")
            & (Project.owner_id != user_id)
            & Project.id.not_in(all_memberships)
        )
    total = await db.scalar(select(func.count()).select_from(Project).where(condition))
    rows = await db.execute(
        select(Project)
        .where(condition)
        .order_by(Project.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return total or 0, list(rows.scalars().all())


async def get_member(db: AsyncSession, project_id: int, user_id: int) -> ProjectMember | None:
    return (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def list_members(db: AsyncSession, project_id: int) -> list[tuple[ProjectMember, str]]:
    rows = await db.execute(
        select(ProjectMember, User.username)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.id)
    )
    return list(rows.all())


async def list_member_candidates(
    db: AsyncSession, *, project_id: int, owner_id: int, keyword: str, limit: int
) -> list[User]:
    existing_ids = select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)
    condition = (User.id != owner_id) & (User.id.not_in(existing_ids)) & (User.status == "active")
    like = f"%{keyword}%"
    condition &= (User.username.ilike(like)) | (User.email.ilike(like))
    rows = await db.execute(select(User).where(condition).order_by(User.username).limit(limit))
    return list(rows.scalars().all())


async def create(db: AsyncSession, *, name: str, description: str | None, visibility: str, owner_id: int) -> Project:
    project = Project(name=name, description=description, visibility=visibility, owner_id=owner_id)
    db.add(project)
    await db.flush()
    return project


async def update_fields(
    project: Project, *, name: str | None, description: str | None, visibility: str | None, fields: set[str]
) -> Project:
    for field in ("name", "description", "visibility"):
        if field in fields:
            setattr(project, field, locals()[field])
    return project


async def refresh_project(db: AsyncSession, project: Project) -> Project:
    await db.refresh(project)
    return project


async def soft_delete(project: Project, deleted_at: datetime) -> Project:
    project.deleted_at = deleted_at
    return project


async def create_member(
    db: AsyncSession, *, project_id: int, user_id: int, role: str
) -> ProjectMember:
    member = ProjectMember(project_id=project_id, user_id=user_id, role=role)
    db.add(member)
    await db.flush()
    return member


async def update_member_role(member: ProjectMember, role: str) -> ProjectMember:
    member.role = role
    return member


async def refresh_member(db: AsyncSession, member: ProjectMember) -> ProjectMember:
    await db.refresh(member)
    return member


async def get_user(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def delete_member(db: AsyncSession, member: ProjectMember) -> None:
    await db.delete(member)
