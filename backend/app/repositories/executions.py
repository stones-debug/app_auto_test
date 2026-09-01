"""内部执行状态和 Agent 执行绑定查询。"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Device,
    Execution,
    ExecutionExclusion,
    ExecutionLog,
    ExecutionQueue,
    Project,
    TestCase,
    TestSuite,
    User,
)
from app.repositories import execution_tree

if TYPE_CHECKING:
    from app.models import ExecutionNode, ExecutionStep


async def get_by_id(
    db: AsyncSession, execution_id: int, *, populate_existing: bool = False
) -> Execution | None:
    return await db.get(Execution, execution_id, populate_existing=populate_existing)


async def update_state(
    execution: Execution, *, status: str, now: datetime
) -> Execution:
    execution.status = status
    if status == "running" and execution.started_at is None:
        execution.started_at = now
    if status in {"passed", "failed", "error", "stopped", "cancelled"}:
        execution.finished_at = now
    return execution


async def get_device_for_execution(
    db: AsyncSession, execution: Execution
) -> Device | None:
    return await db.get(Device, execution.device_id) if execution.device_id else None


async def create(db: AsyncSession, *, fields: dict) -> Execution:
    execution = Execution(**fields)
    db.add(execution)
    await db.flush()
    return execution


async def refresh(db: AsyncSession, execution: Execution) -> Execution:
    await db.refresh(execution)
    return execution


async def enqueue(db: AsyncSession, execution_id: int) -> ExecutionQueue:
    queue = ExecutionQueue(execution_id=execution_id)
    db.add(queue)
    await db.flush()
    return queue


async def create_profiled(
    db: AsyncSession, *, fields: dict, result, app_profile_id: int
) -> Execution:
    execution = await create(db, fields=fields)
    await execution_tree.materialize_snapshot(db, execution, result)
    for exclusion in result.exclusions:
        display = exclusion.display_snapshot or {}
        if exclusion.phase is not None:
            display = {**display, "phase": exclusion.phase}
        db.add(ExecutionExclusion(execution_id=execution.id, app_profile_id=app_profile_id, target_type=exclusion.target_type, suite_id_snapshot=exclusion.suite_id, suite_name_snapshot=display.get("suite_name"), case_id_snapshot=exclusion.case_id, case_name_snapshot=display.get("case_name"), node_key=exclusion.node_key, node_name_snapshot=display.get("node_name"), source_type=exclusion.source_type, reason_code=exclusion.reason_code, reason_note=exclusion.reason_note, details=display))
    await enqueue(db, execution.id)
    return execution


async def list_page(
    db: AsyncSession, *, project_ids: list[int], project_id: int | None,
    status: str, type_: str, keyword: str, device_id: int | None,
    created_from, created_to, offset: int, limit: int,
) -> tuple[int, list[Execution], dict[str, dict[int, str]]]:
    query = select(Execution)
    query = query.where(Execution.project_id == project_id) if project_id is not None else query.where(Execution.project_id.in_(project_ids))
    if status:
        query = query.where(Execution.status == status)
    if type_:
        query = query.where(Execution.type == type_)
    if device_id is not None:
        query = query.where(Execution.device_id == device_id)
    if created_from is not None:
        query = query.where(Execution.created_at >= created_from)
    if created_to is not None:
        query = query.where(Execution.created_at <= created_to)
    if keyword:
        like = f"%{keyword}%"
        name_cond = Execution.case_id.in_(select(TestCase.id).where(TestCase.name.ilike(like))) | Execution.suite_id.in_(select(TestSuite.id).where(TestSuite.name.ilike(like)))
        query = query.where((Execution.id == int(keyword)) | name_cond if keyword.isdigit() else name_cond)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = list((await db.execute(query.order_by(Execution.id.desc()).offset(offset).limit(limit))).scalars().all())
    ids = {"case": {r.case_id for r in rows if r.case_id}, "suite": {r.suite_id for r in rows if r.suite_id}, "project": {r.project_id for r in rows}, "device": {r.device_id for r in rows if r.device_id}, "creator": {r.created_by for r in rows if r.created_by}}
    names = {
        "case": {x.id: x.name for x in (await db.execute(select(TestCase).where(TestCase.id.in_(ids["case"])))) .scalars().all()} if ids["case"] else {},
        "suite": {x.id: x.name for x in (await db.execute(select(TestSuite).where(TestSuite.id.in_(ids["suite"])))) .scalars().all()} if ids["suite"] else {},
        "project": {x.id: x.name for x in (await db.execute(select(Project).where(Project.id.in_(ids["project"])))) .scalars().all()} if ids["project"] else {},
        "device": {x.id: x.name for x in (await db.execute(select(Device).where(Device.id.in_(ids["device"])))) .scalars().all()} if ids["device"] else {},
        "creator": {x.id: x.username for x in (await db.execute(select(User).where(User.id.in_(ids["creator"])))) .scalars().all()} if ids["creator"] else {},
    }
    return total or 0, rows, names


async def list_logs(db: AsyncSession, execution_id: int, after_timestamp, offset: int, limit: int) -> tuple[list[ExecutionLog], int]:
    query = select(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
    count_query = select(func.count()).select_from(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
    if after_timestamp is not None:
        query = query.where(ExecutionLog.created_at > after_timestamp)
        count_query = count_query.where(ExecutionLog.created_at > after_timestamp)
    total = await db.scalar(count_query)
    rows = list((await db.execute(query.order_by(ExecutionLog.created_at.asc(), ExecutionLog.id.asc()).offset(offset).limit(limit))).scalars().all())
    return rows, total or 0


def parse_artifact_reference(reference: str) -> tuple[str, int] | None:
    """解析带类型的执行附件标识（``step:123`` / ``node:456``）。"""
    kind, separator, raw_id = str(reference or "").partition(":")
    if not separator or kind not in {"step", "node"}:
        return None
    try:
        artifact_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    return (kind, artifact_id) if artifact_id > 0 else None


async def get_artifact(
    db: AsyncSession, execution_id: int, reference: str
) -> "ExecutionStep | ExecutionNode | None":
    """按带类型标识读取属于当前执行的步骤或统一节点附件。"""
    from app.models import ExecutionCase, ExecutionNode, ExecutionStep, ExecutionSuite

    parsed = parse_artifact_reference(reference)
    if parsed is None:
        return None
    kind, artifact_id = parsed
    model = ExecutionStep if kind == "step" else ExecutionNode
    artifact = await db.get(model, artifact_id)
    if artifact is None:
        return None
    if artifact.execution_case_id is not None:
        parent = await db.get(ExecutionCase, artifact.execution_case_id)
    elif artifact.execution_suite_id is not None:
        parent = await db.get(ExecutionSuite, artifact.execution_suite_id)
    else:
        return None
    return artifact if parent is not None and parent.execution_id == execution_id else None


async def stop_queued(db: AsyncSession, execution_id: int, now) -> int:
    result = await db.execute(update(Execution).where(Execution.id == execution_id, Execution.status == "queued").values(status="cancelled", finished_at=now, stop_requested_at=now, finalized_at=now))
    await db.execute(update(ExecutionQueue).where(ExecutionQueue.execution_id == execution_id).values(status="done"))
    return int(getattr(result, "rowcount", 0))


async def stop_running(db: AsyncSession, execution_id: int, now) -> int:
    result = await db.execute(update(Execution).where(Execution.id == execution_id, Execution.status == "running").values(status="stopping", stop_requested_at=now))
    return int(getattr(result, "rowcount", 0))


async def claim_running(db: AsyncSession, execution_id: int, session_token: str, now) -> bool:
    result = await db.execute(update(Execution).where(Execution.id == execution_id, Execution.status == "queued").values(status="running", session_token=session_token, started_at=now).returning(Execution.id))
    return result.scalar_one_or_none() is not None


async def mark_queue_done(db: AsyncSession, execution_id: int) -> None:
    await db.execute(update(ExecutionQueue).where(ExecutionQueue.execution_id == execution_id).values(status="done"))


async def delete_logs_before(db: AsyncSession, cutoff) -> int:
    result = await db.execute(delete(ExecutionLog).where(ExecutionLog.created_at < cutoff))
    return int(getattr(result, "rowcount", 0))
