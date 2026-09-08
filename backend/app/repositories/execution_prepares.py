"""Persistence for short-lived execution preview snapshots."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExecutionPrepare


async def create(db: AsyncSession, prepare: ExecutionPrepare) -> ExecutionPrepare:
    db.add(prepare)
    await db.flush()
    return prepare


async def get_for_update(db: AsyncSession, token_hash: str) -> ExecutionPrepare | None:
    return await db.scalar(
        select(ExecutionPrepare)
        .where(ExecutionPrepare.token_hash == token_hash)
        .with_for_update()
    )


async def get(db: AsyncSession, token_hash: str) -> ExecutionPrepare | None:
    return await db.scalar(select(ExecutionPrepare).where(ExecutionPrepare.token_hash == token_hash))


async def delete_expired(db: AsyncSession, *, limit: int = 100) -> int:
    ids = select(ExecutionPrepare.id).where(
        ExecutionPrepare.expires_at <= datetime.now(UTC)
    ).order_by(ExecutionPrepare.id).limit(limit)
    result = await db.execute(delete(ExecutionPrepare).where(ExecutionPrepare.id.in_(ids)))
    return int(getattr(result, "rowcount", 0) or 0)
