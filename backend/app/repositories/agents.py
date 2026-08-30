"""Agent、AgentUser 绑定和 Agent 管理命令。"""

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentUser, Device, DevicePreference, Execution, User


async def get_by_id(db: AsyncSession, agent_id: int) -> Agent | None:
    return await db.get(Agent, agent_id)


async def get_by_install_id(db: AsyncSession, install_id: str) -> Agent | None:
    return (
        await db.execute(select(Agent).where(Agent.agent_id == install_id))
    ).scalar_one_or_none()


async def create(
    db: AsyncSession,
    *,
    agent_id: str,
    agent_key: str,
    hostname: str | None,
    platform: str | None,
    version: str | None = None,
    status: str = "offline",
) -> Agent:
    agent = Agent(
        agent_id=agent_id,
        agent_key=agent_key,
        hostname=hostname,
        platform=platform,
        version=version,
        status=status,
    )
    db.add(agent)
    await db.flush()
    return agent


async def refresh(db: AsyncSession, agent: Agent) -> Agent:
    await db.refresh(agent)
    return agent


async def reactivate(
    agent: Agent,
    *,
    agent_key: str,
    hostname: str | None,
    platform: str | None,
    version: str | None,
) -> Agent:
    agent.agent_key = agent_key
    agent.deleted_at = None
    agent.status = "offline"
    if hostname:
        agent.hostname = hostname
    if platform:
        agent.platform = platform
    if version:
        agent.version = version
    return agent


async def get_binding(
    db: AsyncSession, *, agent_id: int, user_id: int
) -> AgentUser | None:
    return (
        await db.execute(
            select(AgentUser).where(
                AgentUser.agent_id == agent_id,
                AgentUser.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def get_binding_for_agent(
    db: AsyncSession, *, binding_id: int, agent_id: int
) -> AgentUser | None:
    return (
        await db.execute(
            select(AgentUser).where(
                AgentUser.id == binding_id,
                AgentUser.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()


async def list_bindings(db: AsyncSession, agent_id: int) -> list[tuple[AgentUser, str]]:
    rows = await db.execute(
        select(AgentUser, User.username)
        .join(User, AgentUser.user_id == User.id)
        .where(AgentUser.agent_id == agent_id)
        .order_by(AgentUser.id)
    )
    return list(rows.all())


async def create_binding(
    db: AsyncSession, *, agent_id: int, user_id: int, revoke_credential_hash: str
) -> AgentUser:
    binding = AgentUser(
        agent_id=agent_id,
        user_id=user_id,
        revoke_credential_hash=revoke_credential_hash,
    )
    db.add(binding)
    return binding


async def delete_binding(db: AsyncSession, binding: AgentUser) -> None:
    await db.delete(binding)


async def list_for_user(
    db: AsyncSession, *, user_id: int, is_admin: bool
) -> list[tuple[Agent, int]]:
    query = (
        select(Agent, func.count(Device.id))
        .outerjoin(Device, Device.agent_id == Agent.id)
        .where(Agent.deleted_at.is_(None))
        .group_by(Agent.id)
        .order_by(Agent.id)
    )
    if not is_admin:
        query = query.join(AgentUser, AgentUser.agent_id == Agent.id).where(
            AgentUser.user_id == user_id
        )
    return list((await db.execute(query)).all())


async def lock_agent(db: AsyncSession, agent_id: int) -> Agent | None:
    return (
        await db.execute(select(Agent).where(Agent.id == agent_id).with_for_update())
    ).scalar_one_or_none()


async def lock_devices(db: AsyncSession, agent_id: int) -> list[Device]:
    return list(
        (
            await db.execute(
                select(Device).where(Device.agent_id == agent_id).with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def find_active_execution_id(
    db: AsyncSession, device_ids: list[int]
) -> int | None:
    if not device_ids:
        return None
    return (
        await db.execute(
            select(Execution.id)
            .where(
                Execution.device_id.in_(device_ids),
                Execution.status.in_(("queued", "running", "stopping")),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def mark_deleted(agent: Agent, deleted_at: datetime) -> Agent:
    agent.deleted_at = deleted_at
    agent.status = "offline"
    return agent


async def clear_bindings(db: AsyncSession, agent_id: int) -> None:
    await db.execute(delete(AgentUser).where(AgentUser.agent_id == agent_id))


async def clear_device_preferences(db: AsyncSession, device_ids: list[int]) -> None:
    if device_ids:
        await db.execute(delete(DevicePreference).where(DevicePreference.device_id.in_(device_ids)))
