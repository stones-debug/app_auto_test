from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_project_permission,
    require_project_role,
)
from app.core.database import get_db
from app.core.errors import api_error
from app.models import Project, ProjectMember, TestCase, TestElement, TestSuite, User
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
from app.utils.pagination import get_pagination, paginate

router = APIRouter(prefix="/projects", tags=["项目管理"])


async def _project_counts(db: AsyncSession, project_ids: list[int]) -> dict[int, dict]:
    """批量统计项目下的用例/元素/套件数量（含软删除过滤）。"""
    result: dict[int, dict] = {
        pid: {"case_count": 0, "element_count": 0, "suite_count": 0} for pid in project_ids
    }
    if not project_ids:
        return result
    for model, key in (
        (TestCase, "case_count"),
        (TestElement, "element_count"),
        (TestSuite, "suite_count"),
    ):
        rows = (
            await db.execute(
                select(model.project_id, func.count())
                .where(model.project_id.in_(project_ids), model.deleted_at.is_(None))
                .group_by(model.project_id)
            )
        ).all()
        for pid, count in rows:
            result[pid][key] = count
    return result


async def _project_out(
    project: Project,
    role: str | None = None,
    counts: dict | None = None,
) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    out.role = role
    if counts:
        out.case_count = counts.get("case_count", 0)
        out.element_count = counts.get("element_count", 0)
        out.suite_count = counts.get("suite_count", 0)
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
    scope: str = "",
    role: str = "",
    keyword: str = "",
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
    effective_scope = scope or visibility
    if effective_scope == "public":
        cond = (Project.deleted_at.is_(None)) & (Project.visibility == "public")
    elif effective_scope == "mine":
        cond = (Project.deleted_at.is_(None)) & (Project.owner_id == user.id)

    if keyword:
        cond &= Project.name.ilike(f"%{keyword.strip()}%")

    if role == "owner":
        cond &= Project.owner_id == user.id
    elif role in {"admin", "member"}:
        cond &= Project.id.in_(
            select(ProjectMember.project_id).where(
                ProjectMember.user_id == user.id,
                ProjectMember.role == role,
            )
        )
    elif role == "viewer":
        all_memberships = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        viewer_memberships = select(ProjectMember.project_id).where(
            ProjectMember.user_id == user.id,
            ProjectMember.role == "viewer",
        )
        cond &= Project.id.in_(viewer_memberships) | (
            (Project.visibility == "public")
            & (Project.owner_id != user.id)
            & Project.id.not_in(all_memberships)
        )

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
    kept: list[tuple[Project, str]] = []
    for project in rows:
        role = await _resolve_role(project, user, db)
        if role != "none":
            kept.append((project, role))
    counts_map = await _project_counts(db, [p.id for p, _r in kept])
    for project, role in kept:
        items.append(await _project_out(project, role, counts_map.get(project.id)))
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
    db: AsyncSession = Depends(get_db),
):
    project, role = perm
    counts = (await _project_counts(db, [project.id])).get(project.id)
    return await _project_out(project, role, counts)


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    project, role = perm
    # CR-25：model_fields_set 区分“未提交”与“显式 null”，支持清空可选字段
    for field in ("name", "description", "visibility"):
        if field in body.model_fields_set:
            setattr(project, field, getattr(body, field))
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
    perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    project, _role = perm
    # owner 虚拟行（owner 不在 project_members 表中）
    owner = await db.get(User, project.owner_id)
    items: list[ProjectMemberOut] = []
    if owner is not None:
        items.append(
            ProjectMemberOut(membership_id=None, user_id=owner.id, username=owner.username, role="owner")
        )
    rows = await db.execute(
        select(ProjectMember, User.username)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.id)
    )
    for member, username in rows.all():
        out = ProjectMemberOut(
            membership_id=member.id,
            user_id=member.user_id,
            username=username,
            role=member.role,
        )
        items.append(out)
    return items


@router.get("/{project_id}/member-candidates", response_model=list[UserCandidateOut])
async def member_candidates(
    project_id: int,
    keyword: str = "",
    limit: int = Query(10, ge=1, le=20),
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """候选用户：排除 owner 与已有成员，按用户名/邮箱模糊搜索。"""
    project, _role = perm
    keyword = keyword.strip()
    if len(keyword) < 2:
        return []
    existing_ids = select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)
    cond = (User.id != project.owner_id) & (User.id.not_in(existing_ids)) & (User.status == "active")
    like = f"%{keyword}%"
    cond = cond & ((User.username.ilike(like)) | (User.email.ilike(like)))
    rows = (
        await db.execute(
            select(User).where(cond).order_by(User.username).limit(limit)
        )
    ).scalars().all()
    return [UserCandidateOut(id=u.id, username=u.username, email=u.email) for u in rows]


@router.post("/{project_id}/members", response_model=ProjectMemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    project_id: int,
    body: ProjectMemberCreate,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    project, _role = perm
    if body.user_id == project.owner_id:
        raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "项目 owner 不可添加为成员")
    target = await db.get(User, body.user_id)
    if target is None or target.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == body.user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise api_error(409, "MEMBER_ALREADY_EXISTS", "该用户已是项目成员")

    member = ProjectMember(project_id=project_id, user_id=body.user_id, role=body.role)
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return ProjectMemberOut(
        membership_id=member.id,
        user_id=member.user_id,
        username=target.username,
        role=member.role,
    )


@router.patch("/{project_id}/members/{user_id}", response_model=ProjectMemberOut)
async def update_member(
    project_id: int,
    user_id: int,
    body: ProjectMemberUpdate,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改成员角色。

    - admin 只能管理 member/viewer（不能给 user 授予 admin/owner，不能改 admin）；
    - admin 不得修改自己；owner 可管理所有成员（除 owner 自身虚拟行）。
    """
    project, caller_role = perm
    if user_id == project.owner_id:
        raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "项目 owner 角色不可修改")
    member = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = member.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="成员不存在")
    if caller_role == "admin":
        if user_id == user.id:
            raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "admin 不能修改自己的角色")
        if member.role == "admin" or body.role == "admin":
            raise api_error(403, "PROJECT_FORBIDDEN", "admin 只能管理 member/viewer")
    member.role = body.role
    target = await db.get(User, user_id)
    await db.commit()
    await db.refresh(member)
    return ProjectMemberOut(
        membership_id=member.id,
        user_id=member.user_id,
        username=target.username if target else None,
        role=member.role,
    )


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: int,
    user_id: int,
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project, caller_role = perm
    if user_id == project.owner_id:
        raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "项目 owner 不可移除")
    member = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = member.scalar_one_or_none()
    if member is None:
        return
    if caller_role == "admin":
        if user_id == user.id:
            raise api_error(409, "PROJECT_OWNER_IMMUTABLE", "admin 不能移除自己")
        if member.role == "admin":
            raise api_error(403, "PROJECT_FORBIDDEN", "admin 只能管理 member/viewer")
    await db.delete(member)
    await db.commit()
