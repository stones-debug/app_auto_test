from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_project_permission,
    require_project_role,
)
from app.core.database import get_db
from app.models import Project, ProjectMember, User
from app.schemas.project import (
    PageResult,
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdate,
)
from app.utils.pagination import get_pagination, paginate

router = APIRouter(prefix="/projects", tags=["项目管理"])


async def _project_out(project: Project, role: str | None = None) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    out.role = role
    return out


async def _resolve_role(project: Project, user: User, db: AsyncSession) -> str:
    if project.owner_id == user.id:
        return "owner"
    row = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    )
    member = row.scalar_one_or_none()
    if member is not None:
        return member.role
    return "viewer" if project.visibility == "public" else "none"


@router.get("", response_model=PageResult)
async def list_projects(
    pagination=Depends(get_pagination),
    visibility: str = "all",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回我拥有的私有项目 + 我是成员的项目 + 所有公开项目。"""
    owned_or_member = select(Project.id).where(
        (Project.owner_id == user.id)
        | (
            Project.id.in_(
                select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
            )
        )
    )
    cond = (Project.deleted_at.is_(None)) & (
        (Project.visibility == "public") | (Project.id.in_(owned_or_member))
    )
    if visibility == "public":
        cond = (Project.deleted_at.is_(None)) & (Project.visibility == "public")
    elif visibility == "mine":
        cond = (Project.deleted_at.is_(None)) & (Project.owner_id == user.id)

    total = await db.scalar(select(func.count()).select_from(Project).where(cond))
    rows = (
        await db.execute(
            select(Project)
            .where(cond)
            .order_by(Project.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.limit)
        )
    ).scalars().all()

    items = []
    for project in rows:
        role = await _resolve_role(project, user, db)
        if role == "none":
            continue
        items.append(await _project_out(project, role))
    return await paginate(items, total or 0, pagination)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = Project(
        name=body.name,
        description=body.description,
        visibility=body.visibility,
        owner_id=user.id,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return await _project_out(project, "owner")


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    perm: tuple[Project, str | None] = Depends(get_project_permission),
):
    project, role = perm
    return await _project_out(project, role)


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    project, role = perm
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.visibility is not None:
        project.visibility = body.visibility
    await db.commit()
    await db.refresh(project)
    return await _project_out(project, role)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    project, _role = perm
    project.deleted_at = datetime.now(UTC)
    await db.commit()


@router.get("/{project_id}/members", response_model=list[ProjectMemberOut])
async def list_members(
    project_id: int,
    _perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin", "member")),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(ProjectMember, User.username)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
    )
    members = []
    for member, username in rows.all():
        out = ProjectMemberOut.model_validate(member)
        out.username = username
        members.append(out)
    return members


@router.post("/{project_id}/members", response_model=ProjectMemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    project_id: int,
    body: ProjectMemberCreate,
    _project: Project = Depends(require_project_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, body.user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == body.user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该用户已是项目成员")

    member = ProjectMember(project_id=project_id, user_id=body.user_id, role=body.role)
    db.add(member)
    await db.commit()
    await db.refresh(member)
    out = ProjectMemberOut.model_validate(member)
    out.username = target.username
    return out
