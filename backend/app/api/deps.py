from fastapi import Depends, Header, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import ErrorCode, api_error
from app.core.security import constant_time_equals, decode_token
from app.models import Agent, Device, Project, User
from app.repositories import access as access_repo
from app.repositories import auth as auth_repo

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_REQUIRED, "未提供认证凭据")
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_TOKEN_INVALID, "无效或过期的 Token")
    user = await auth_repo.get_user_by_id(db, int(payload["sub"]))
    if user is None or user.status != "active":
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.AUTH_USER_DISABLED, "用户不存在或已禁用")
    return user


async def _get_project_or_404(project_id: int, db: AsyncSession) -> Project:
    project = await access_repo.get_project(db, project_id)
    if project is None or project.deleted_at is not None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.PROJECT_NOT_FOUND, "项目不存在")
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

    member = await access_repo.get_project_member(db, project_id, user.id)
    if member is not None:
        return project, member.role

    if project.visibility == "public":
        return project, "viewer"
    raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.PROJECT_FORBIDDEN, "无权访问该项目")


def require_project_role(*roles: str):
    """要求用户在项目中具备指定角色之一。

    用法: Depends(require_project_role("owner", "admin"))
    """

    async def _checker(
        perm: tuple[Project, str | None] = Depends(get_project_permission),
    ) -> tuple[Project, str | None]:
        _project, role = perm
        if role not in roles:
            raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.PROJECT_FORBIDDEN, "权限不足")
        return perm

    return _checker


def require_platform_admin():
    """要求当前用户为平台管理员（设备/Agent 管理与 global 变量写等平台级资源）。"""

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if not user.is_admin:
            raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.PLATFORM_ADMIN_REQUIRED, "需要平台管理员权限")
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
        raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.PROJECT_FORBIDDEN, "权限不足")
    return project, role


async def get_editable_project(
    perm: tuple[Project, str | None] = Depends(require_project_role("owner", "admin", "member")),
) -> Project:
    project, _role = perm
    return project


# ---------- Windows 方案 §3.3：Agent/设备访问校验 ----------


async def require_agent_access(agent_id: int, user: User, db: AsyncSession) -> Agent:
    """Agent 访问校验：平台管理员或已绑定该 Agent 的用户；软注销 Agent 视为不存在。"""
    agent = await access_repo.get_agent(db, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.AGENT_NOT_FOUND, "Agent 不存在")
    if user.is_admin:
        return agent
    if not await access_repo.is_user_bound_to_agent(db, agent_id, user.id):
        raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.AGENT_FORBIDDEN, "无权访问该 Agent")
    return agent


async def require_device_access(device_id: int, user: User, db: AsyncSession) -> Device:
    """设备访问校验：平台管理员或设备所属 Agent 已绑定该用户（CR-… 防猜测 ID 用他人设备）。

    Step 6：设备需联查所属 Agent；所属 Agent 已软注销时设备同样视为不存在。
    """
    device = await access_repo.get_device(db, device_id)
    if device is None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.DEVICE_NOT_FOUND, "设备不存在")
    agent = await access_repo.get_device_agent(db, device.agent_id)
    if agent is None or agent.deleted_at is not None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.DEVICE_NOT_FOUND, "设备不存在")
    if user.is_admin:
        return device
    if not await access_repo.is_user_bound_to_agent(db, device.agent_id, user.id):
        raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.DEVICE_FORBIDDEN, "无权使用该设备")
    return device


# ---------- Step 5：内部接口令牌（Worker → FastAPI） ----------


def require_internal_token(x_internal_token: str | None = Header(default=None)) -> str:
    """校验 X-Internal-Token（恒定时间比较，避免计时侧信道）。

    原先该校验内联在 api/internal.py，/metrics 复用不到；提取到 deps 供内部接口与
    指标端点共用同一套判定逻辑。

    缺省值用 None 而非 `Header(...)`：后者在头部缺失时返回 422（参数校验失败），
    对鉴权端点而言 401 才是正确语义，也让"缺头"与"令牌错误"行为一致。
    """
    if x_internal_token is None or not constant_time_equals(
        x_internal_token, settings.internal_token
    ):
        raise api_error(status.HTTP_401_UNAUTHORIZED, ErrorCode.INTERNAL_TOKEN_INVALID, "内部令牌无效")
    return x_internal_token
