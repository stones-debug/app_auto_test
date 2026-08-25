"""档案审计与幂等（方案 §2.5）。

每个配置命令一条不可变审计记录；批量命令用 `request_id` 幂等，
同一档案重复提交相同 request_id 时直接返回 response_data，不重复写。
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppProfileAuditLog

AUDIT_ACTIONS = frozenset(
    {
        "profile_create",
        "profile_update",
        "profile_disable",
        "release_create",
        "release_update",
        "release_disable",
        "skip_batch",
        "restore_batch",
        "element_override_upsert",
        "element_override_restore",
        "variable_override_upsert",
        "variable_override_restore",
        "node_override_upsert",
        "node_override_restore",
    }
)


async def find_idempotent_replay(
    db: AsyncSession, profile_id: int, request_id: str
) -> dict | None:
    """若同一档案已有相同 request_id，返回其 response_data（幂等重放）。"""
    if not request_id:
        return None
    row = (
        await db.execute(
            select(AppProfileAuditLog).where(
                AppProfileAuditLog.profile_id == profile_id,
                AppProfileAuditLog.request_id == request_id,
            )
        )
    ).scalar_one_or_none()
    return row.response_data if row is not None else None


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
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"未知审计动作: {action}")
    if request_id:
        try:
            req_id = str(uuid.UUID(request_id))
        except ValueError:
            raise ValueError(f"request_id 必须为 UUID: {request_id}") from None
    else:
        req_id = str(uuid.uuid4())
    log = AppProfileAuditLog(
        profile_id=profile_id,
        project_id=project_id,
        request_id=req_id,
        batch_id=batch_id,
        action=action,
        actor_id=actor_id,
        actor_role=actor_role,
        revision_before=revision_before,
        revision_after=revision_after,
        changes=changes or [],
        response_data=response_data or {},
        client_ip=client_ip,
        user_agent=user_agent,
    )
    db.add(log)
    await db.flush()
    return log
