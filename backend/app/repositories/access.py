"""项目、Agent 和设备访问范围查询。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentUser, Device, Project, ProjectMember


async def get_project(db: AsyncSession, project_id: int) -> Project | None:
    return await db.get(Project, project_id)


async def get_project_member(
    db: AsyncSession, project_id: int, user_id: int
) -> ProjectMember | None:
    return (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def get_agent(db: AsyncSession, agent_id: int) -> Agent | None:
    return await db.get(Agent, agent_id)


async def get_device(db: AsyncSession, device_id: int) -> Device | None:
    return await db.get(Device, device_id)


async def get_device_agent(db: AsyncSession, agent_id: int) -> Agent | None:
    return await db.get(Agent, agent_id)


async def is_user_bound_to_agent(db: AsyncSession, agent_id: int, user_id: int) -> bool:
    return (
        await db.execute(
            select(AgentUser.id).where(
                AgentUser.agent_id == agent_id,
                AgentUser.user_id == user_id,
            )
        )
    ).scalar_one_or_none() is not None


async def list_bound_agent_ids(db: AsyncSession, user_id: int) -> tuple[int, ...]:
    rows = await db.execute(
        select(AgentUser.agent_id)
        .join(Agent, Agent.id == AgentUser.agent_id)
        .where(AgentUser.user_id == user_id, Agent.deleted_at.is_(None))
    )
    return tuple(agent_id for (agent_id,) in rows.all())


async def list_visible_project_ids(db: AsyncSession, user_id: int) -> tuple[int, ...]:
    rows = await db.execute(
        select(Project.id)
        .outerjoin(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            Project.deleted_at.is_(None),
            (Project.owner_id == user_id)
            | (ProjectMember.user_id == user_id)
            | (Project.visibility == "public"),
        )
        .distinct()
    )
    return tuple(project_id for (project_id,) in rows.all())
