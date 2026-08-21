from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models import Project, ProjectMember, User

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭据",
        )
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的 Token",
        )
    user = await db.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
        )
    return user


async def _get_project_or_404(project_id: int, db: AsyncSession) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


async def get_project_permission(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> tuple[Project, str | None]:
    """返回 (project, 用户在该项目的角色)。

    角色: owner(创建者) / admin / member / viewer / None(公开项目访客)
    """
    project = await _get_project_or_404(project_id, db)
    if project.owner_id == user.id:
        return project, "owner"

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    if member is not None:
        return project, member.role

    if project.visibility == "public":
        return project, "viewer"
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该项目")


def require_project_role(*roles: str):
    """要求用户在项目中具备指定角色之一。

    用法: Depends(require_project_role("owner", "admin"))
    """

    async def _checker(
        perm: tuple[Project, str | None] = Depends(get_project_permission),
    ) -> tuple[Project, str | None]:
        _project, role = perm
        if role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return perm

    return _checker


def require_platform_admin():
    """要求当前用户为平台管理员（设备/Agent 管理与 global 变量写等平台级资源）。"""

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限")
        return user

    return _checker


async def require_project_write(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> tuple[Project, str | None]:
    """要求用户在项目中具备 owner/admin/member 角色（写操作，CR-02/CR-04）。

    与 require_project_role 等价，但可直接以参数形式调用。
    """
    project, role = await get_project_permission(project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    return project, role


async def get_editable_project(
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin", "member")),
) -> Project:
    project, _role = perm
    return project
