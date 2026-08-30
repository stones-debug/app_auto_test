"""项目与项目成员用例。"""

from datetime import UTC, datetime

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import api_error
from app.models import Project, User
from app.repositories import projects as projects_repo
from app.schemas.project import (
    PageResult,
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberOut,
    ProjectMemberUpdate,
    ProjectOut,
    ProjectUpdate,
    UserCandidateOut,
)
from app.utils.pagination import Pagination, paginate


def _project_out(
    project: Project, role: str | None = None, counts: dict | None = None
) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    out.role = role
    if counts:
        out.case_count = counts.get("case_count", 0)
        out.element_count = counts.get("element_count", 0)
        out.suite_count = counts.get("suite_count", 0)
    return out


async def list_projects(
    db: AsyncSession,
    user: User,
    *,
    pagination: Pagination,
    visibility: str,
    scope: str,
    role: str,
    keyword: str,
) -> PageResult:
    total, projects = await projects_repo.list_visible(
        db,
        user_id=user.id,
        visibility=visibility,
        scope=scope,
        role=role,
        keyword=keyword,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    items = []
    for project in projects:
        resolved_role = await resolve_role(db, project, user)
        if resolved_role != "none":
            items.append((project, resolved_role))
    counts = await projects_repo.load_asset_counts(db, [project.id for project, _ in items])
    output = [_project_out(project, role_, counts.get(project.id)) for project, role_ in items]
    return await paginate(output, total, pagination)


async def resolve_role(db: AsyncSession, project: Project, user: User) -> str:
    if project.owner_id == user.id:
        return "owner"
    member = await projects_repo.get_member(db, project.id, user.id)
    if member is not None:
        return member.role
    return "viewer" if project.visibility == "public" else "none"


async def create_project(db: AsyncSession, body: ProjectCreate, user_id: int) -> ProjectOut:
    project = await projects_repo.create(
        db,
        name=body.name,
        description=body.description,
        visibility=body.visibility,
        owner_id=user_id,
    )
    await db.commit()
    await projects_repo.refresh_project(db, project)
    return _project_out(project, "owner")


async def get_project(
    db: AsyncSession, project: Project, role: str | None
) -> ProjectOut:
    counts = (await projects_repo.load_asset_counts(db, [project.id])).get(project.id)
    return _project_out(project, role, counts)


async def update_project(
    db: AsyncSession, project: Project, body: ProjectUpdate, role: str | None
) -> ProjectOut:
    await projects_repo.update_fields(
        project,
        name=body.name,
        description=body.description,
        visibility=body.visibility,
        fields=body.model_fields_set,
    )
    await db.commit()
    await projects_repo.refresh_project(db, project)
    return _project_out(project, role)


async def delete_project(db: AsyncSession, project: Project) -> None:
    await projects_repo.soft_delete(project, datetime.now(UTC))
    await db.commit()


async def list_members(db: AsyncSession, project: Project) -> list[ProjectMemberOut]:
    owner = await projects_repo.get_user(db, project.owner_id)
    items = []
    if owner is not None:
        items.append(ProjectMemberOut(membership_id=None, user_id=owner.id, username=owner.username, role="owner"))
    for member, username in await projects_repo.list_members(db, project.id):
        items.append(
            ProjectMemberOut(
                membership_id=member.id,
                user_id=member.user_id,
                username=username,
                role=member.role,
            )
        )
    return items


async def member_candidates(
    db: AsyncSession, project: Project, keyword: str, limit: int
) -> list[UserCandidateOut]:
    keyword = keyword.strip()
    if len(keyword) < 2:
        return []
    users = await projects_repo.list_member_candidates(
        db, project_id=project.id, owner_id=project.owner_id, keyword=keyword, limit=limit
    )
    return [UserCandidateOut(id=user.id, username=user.username, email=user.email) for user in users]


async def add_member(
    db: AsyncSession, project: Project, body: ProjectMemberCreate
) -> ProjectMemberOut:
    if body.user_id == project.owner_id:
        raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "项目 owner 不可添加为成员")
    target = await projects_repo.get_user(db, body.user_id)
    if target is None or target.status != "active":
        raise api_error(status.HTTP_404_NOT_FOUND, "USER_NOT_FOUND", "用户不存在")
    if await projects_repo.get_member(db, project.id, body.user_id) is not None:
        raise api_error(409, "MEMBER_ALREADY_EXISTS", "该用户已是项目成员")
    try:
        member = await projects_repo.create_member(
            db, project_id=project.id, user_id=body.user_id, role=body.role
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise api_error(409, "MEMBER_ALREADY_EXISTS", "该用户已是项目成员") from None
    except Exception:
        await db.rollback()
        raise
    await projects_repo.refresh_member(db, member)
    return ProjectMemberOut(
        membership_id=member.id, user_id=member.user_id, username=target.username, role=member.role
    )


async def update_member(
    db: AsyncSession,
    project: Project,
    user: User,
    user_id: int,
    body: ProjectMemberUpdate,
    caller_role: str | None,
) -> ProjectMemberOut:
    if user_id == project.owner_id:
        raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "项目 owner 角色不可修改")
    member = await projects_repo.get_member(db, project.id, user_id)
    if member is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "PROJECT_MEMBER_NOT_FOUND", "成员不存在")
    if caller_role == "admin":
        if user_id == user.id:
            raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "admin 不能修改自己的角色")
        if member.role == "admin" or body.role == "admin":
            raise api_error(403, "PROJECT_FORBIDDEN", "admin 只能管理 member/viewer")
    await projects_repo.update_member_role(member, body.role)
    target = await projects_repo.get_user(db, user_id)
    await db.commit()
    await projects_repo.refresh_member(db, member)
    return ProjectMemberOut(
        membership_id=member.id,
        user_id=member.user_id,
        username=target.username if target else None,
        role=member.role,
    )


async def remove_member(
    db: AsyncSession, project: Project, user: User, user_id: int, caller_role: str | None
) -> None:
    if user_id == project.owner_id:
        raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "项目 owner 不可移除")
    member = await projects_repo.get_member(db, project.id, user_id)
    if member is None:
        return
    if caller_role == "admin":
        if user_id == user.id:
            raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "admin 不能移除自己")
        if member.role == "admin":
            raise api_error(403, "PROJECT_FORBIDDEN", "admin 只能管理 member/viewer")
    await projects_repo.delete_member(db, member)
    await db.commit()
