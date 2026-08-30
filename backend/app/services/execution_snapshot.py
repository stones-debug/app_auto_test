"""执行快照持久化用例；具体 ORM 写入由 execution_tree Repository 负责。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import execution_tree


async def materialize_snapshot(db: AsyncSession, execution, result) -> None:
    await execution_tree.materialize_snapshot(db, execution, result)
