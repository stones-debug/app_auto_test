"""档案审计与幂等（方案 §2.5）。

每个配置命令一条不可变审计记录；批量命令用 `request_id` 幂等，
同一档案重复提交相同 request_id 时直接返回 response_data，不重复写。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppProfileAuditLog
from app.repositories.app_profiles import profiles as profiles_repo


async def find_idempotent_replay(
    db: AsyncSession, profile_id: int, request_id: str
) -> dict | None:
    """若同一档案已有相同 request_id，返回其 response_data（幂等重放）。"""
    return await profiles_repo.find_audit_replay(
        db, profile_id=profile_id, request_id=request_id
    )


async def find_project_idempotent_replay(
    db: AsyncSession,
    project_id: int,
    request_id: str,
    *,
    action: str,
) -> dict | None:
    """创建档案前尚无 profile_id，按项目、动作和 request_id 查找重放。"""
    return await profiles_repo.find_audit_replay(
        db, project_id=project_id, request_id=request_id, action=action
    )


async def write_audit(
    db: AsyncSession,
    *,
    profile_id: int,
    project_id: int,
    action: str,
    actor_id: int | None,
    actor_role: str | None,
    revision_before: int,
    revision_after: int,
    changes: list | None = None,
    response_data: dict | None = None,
    request_id: str | None = None,
    batch_id: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> AppProfileAuditLog:
    return await profiles_repo.add_audit(
        db,
        profile_id=profile_id,
        project_id=project_id,
        action=action,
        actor_id=actor_id,
        actor_role=actor_role,
        revision_before=revision_before,
        revision_after=revision_after,
        changes=changes,
        response_data=response_data,
        request_id=request_id,
        batch_id=batch_id,
        client_ip=client_ip,
        user_agent=user_agent,
    )
