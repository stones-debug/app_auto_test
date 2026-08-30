"""统一资源可见范围 Service。

Service 返回具体 ID 集合，不把 SQLAlchemy expression 泄漏给 API；查询细节由
``repositories.access`` 负责。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import access as access_repo


async def visible_project_ids(db: AsyncSession, user_id: int) -> tuple[int, ...]:
    """返回用户可见项目 ID；空元组表示当前没有可见项目。"""
    return await access_repo.list_visible_project_ids(db, user_id)


async def list_bound_agent_ids(db: AsyncSession, user_id: int) -> tuple[int, ...]:
    """返回用户绑定且仍存活的 Agent ID。"""
    return await access_repo.list_bound_agent_ids(db, user_id)


async def user_bound_to_agent(db: AsyncSession, agent_id: int, user_id: int) -> bool:
    return await access_repo.is_user_bound_to_agent(db, agent_id, user_id)
