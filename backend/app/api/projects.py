from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_project_permission, require_project_role
from app.core.database import get_db
from app.models import Project, User
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
from app.services import project_service
from app.utils.pagination import get_pagination

router = APIRouter(prefix="/projects", tags=["项目管理"])


@router.get("", response_model=PageResult)
async def list_projects(
    pagination=Depends(get_pagination),
    visibility: str = "all",
    scope: str = "",
    role: str = "",
    keyword: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await project_service.list_projects(
        db,
        user,
        pagination=pagination,
        visibility=visibility,
        scope=scope,
        role=role,
        keyword=keyword,
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await project_service.create_project(db, body, user.id)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    project, role = perm
    return await project_service.get_project(db, project, role)


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    project, role = perm
    return await project_service.update_project(db, project, body, role)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    project, _role = perm
    await project_service.delete_project(db, project)


@router.get("/{project_id}/members", response_model=list[ProjectMemberOut])
async def list_members(
    project_id: int,
    perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    project, _role = perm
    return await project_service.list_members(db, project)


@router.get("/{project_id}/member-candidates", response_model=list[UserCandidateOut])
async def member_candidates(
    project_id: int,
    keyword: str = "",
    limit: int = Query(10, ge=1, le=20),
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    project, _role = perm
    return await project_service.member_candidates(db, project, keyword, limit)


@router.post("/{project_id}/members", response_model=ProjectMemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    project_id: int,
    body: ProjectMemberCreate,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    project, _role = perm
    return await project_service.add_member(db, project, body)


@router.patch("/{project_id}/members/{user_id}", response_model=ProjectMemberOut)
async def update_member(
    project_id: int,
    user_id: int,
    body: ProjectMemberUpdate,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project, caller_role = perm
    return await project_service.update_member(db, project, user, user_id, body, caller_role)


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: int,
    user_id: int,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project, caller_role = perm
    await project_service.remove_member(db, project, user, user_id, caller_role)
