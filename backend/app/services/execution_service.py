from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Execution,
    ExecutionLog,
    ExecutionQueue,
    TestCase,
    TestSuite,
    User,
)


async def _create_and_enqueue(
    db: AsyncSession,
    project_id: int,
    type_: str,
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
    *,
    suite_id: int | None = None,
    case_id: int | None = None,
    retry_of: int | None = None,
) -> Execution:
    execution = Execution(
        project_id=project_id,
        type=type_,
        suite_id=suite_id,
        case_id=case_id,
        device_id=device_id,
        status="queued",
        parameters=parameters or {},
        timeout_seconds=timeout_seconds or settings.default_execution_timeout,
        created_by=user.id,
        retry_of=retry_of,
    )
    db.add(execution)
    await db.flush()
    db.add(ExecutionQueue(execution_id=execution.id))
    await db.commit()
    await db.refresh(execution)
    return execution


async def create_case_execution(
    db: AsyncSession,
    case: TestCase,
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
) -> Execution:
    return await _create_and_enqueue(
        db,
        project_id=case.project_id,
        type_="case",
        user=user,
        device_id=device_id,
        parameters=parameters,
        timeout_seconds=timeout_seconds,
        case_id=case.id,
    )


async def create_suite_execution(
    db: AsyncSession,
    suite: TestSuite,
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
) -> Execution:
    return await _create_and_enqueue(
        db,
        project_id=suite.project_id,
        type_="suite",
        user=user,
        device_id=device_id,
        parameters=parameters,
        timeout_seconds=timeout_seconds,
        suite_id=suite.id,
    )


async def create_batch_execution(
    db: AsyncSession,
    suites: list[TestSuite],
    user: User,
    device_id: int | None,
    parameters: dict,
    timeout_seconds: int | None,
) -> Execution:
    project_id = suites[0].project_id
    parameters = dict(parameters or {})
    suite_ids = parameters.get("suite_ids") or []
    suite_ids = list(dict.fromkeys([*suite_ids, *[s.id for s in suites]]))
    parameters["suite_ids"] = suite_ids
    return await _create_and_enqueue(
        db,
        project_id=project_id,
        type_="batch",
        user=user,
        device_id=device_id,
        parameters=parameters,
        timeout_seconds=timeout_seconds,
    )


async def stop_execution(db: AsyncSession, execution: Execution) -> str:
    """queued → cancelled；running → stopping；其他状态拒绝。

    CR-12：queued 取消使用条件更新，与 Worker 原子认领竞争时只有一个赢家。
    """
    if execution.status == "queued":
        now = datetime.now(UTC)
        result = await db.execute(
            update(Execution)
            .where(Execution.id == execution.id, Execution.status == "queued")
            .values(status="cancelled", finished_at=now, stop_requested_at=now, finalized_at=now)
        )
        await db.execute(
            update(ExecutionQueue)
            .where(ExecutionQueue.execution_id == execution.id)
            .values(status="done")
        )
        await db.commit()
        if result.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="执行已非排队态，无法取消")
        return "cancelled"
    if execution.status == "running":
        result = await db.execute(
            update(Execution)
            .where(Execution.id == execution.id, Execution.status == "running")
            .values(status="stopping", stop_requested_at=datetime.now(UTC))
        )
        await db.commit()
        if result.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="执行已非运行态，无需停止")
        return "stopping"
    if execution.status == "stopping":
        return "stopping"
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"执行状态为 {execution.status}，无法停止")


async def retry_execution(
    db: AsyncSession,
    execution: Execution,
    user: User,
    device_id: int,
    timeout_seconds: int | None = None,
) -> Execution:
    return await _create_and_enqueue(
        db,
        project_id=execution.project_id,
        type_=execution.type,
        user=user,
        device_id=device_id,
        parameters=execution.parameters or {},
        timeout_seconds=timeout_seconds or execution.timeout_seconds,
        suite_id=execution.suite_id,
        case_id=execution.case_id,
        retry_of=execution.id,
    )


async def get_execution_logs(
    db: AsyncSession,
    execution_id: int,
    after_timestamp: datetime | None,
    offset: int,
    limit: int,
) -> tuple[list[ExecutionLog], int]:
    query = select(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
    count_query = select(ExecutionLog.id).where(ExecutionLog.execution_id == execution_id)
    if after_timestamp is not None:
        query = query.where(ExecutionLog.created_at > after_timestamp)
        count_query = count_query.where(ExecutionLog.created_at > after_timestamp)
    total = len((await db.execute(count_query)).scalars().all())
    rows = (
        await db.execute(query.order_by(ExecutionLog.created_at.asc()).offset(offset).limit(limit))
    ).scalars().all()
    return rows, total
