"""设备、默认设备偏好和设备释放相关数据库访问。"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Device, DevicePreference, Execution


async def get_by_id(db: AsyncSession, device_id: int) -> Device | None:
    return await db.get(Device, device_id)


async def list_for_agent(db: AsyncSession, agent_id: int) -> list[Device]:
    return list(
        (
            await db.execute(
                select(Device).where(Device.agent_id == agent_id).order_by(Device.id)
            )
        )
        .scalars()
        .all()
    )


async def list_with_agent_names(
    db: AsyncSession, devices: list[Device]
) -> dict[int, str]:
    agent_ids = {device.agent_id for device in devices}
    if not agent_ids:
        return {}
    rows = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
    return {agent.id: agent.agent_id for agent in rows.scalars().all()}


async def list_page(
    db: AsyncSession,
    *,
    agent_ids: tuple[int, ...] | None,
    platform: str,
    status: str,
    offset: int,
    limit: int,
) -> tuple[int, list[Device]]:
    query = select(Device)
    count_query = select(func.count()).select_from(Device)
    if agent_ids is not None:
        query = query.where(Device.agent_id.in_(agent_ids))
        count_query = count_query.where(Device.agent_id.in_(agent_ids))
    if platform:
        query = query.where(Device.platform == platform)
        count_query = count_query.where(Device.platform == platform)
    if status:
        query = query.where(Device.status == status)
        count_query = count_query.where(Device.status == status)
    total = await db.scalar(count_query)
    rows = await db.execute(query.order_by(Device.id).offset(offset).limit(limit))
    return total or 0, list(rows.scalars().all())


async def get_preference(db: AsyncSession, user_id: int) -> DevicePreference | None:
    return (
        await db.execute(select(DevicePreference).where(DevicePreference.user_id == user_id))
    ).scalar_one_or_none()


async def create_preference(
    db: AsyncSession, *, user_id: int, device_id: int
) -> DevicePreference:
    preference = DevicePreference(user_id=user_id, device_id=device_id)
    db.add(preference)
    return preference


async def delete_preference(db: AsyncSession, preference: DevicePreference) -> None:
    await db.delete(preference)


async def update_preference(preference: DevicePreference, *, device_id: int) -> DevicePreference:
    preference.device_id = device_id
    return preference


async def get_locked_for_update(
    db: AsyncSession, device_id: int
) -> Device | None:
    return (
        await db.execute(select(Device).where(Device.id == device_id).with_for_update())
    ).scalar_one_or_none()


async def get_execution(db: AsyncSession, execution_id: int) -> Execution | None:
    return await db.get(Execution, execution_id)


async def get_locked_execution(db: AsyncSession, execution_id: int) -> Execution | None:
    return (
        await db.execute(select(Execution).where(Execution.id == execution_id).with_for_update())
    ).scalar_one_or_none()


async def get_agent(db: AsyncSession, agent_id: int) -> Agent | None:
    return await db.get(Agent, agent_id)


async def refresh(device: Device, db: AsyncSession) -> Device:
    await db.refresh(device)
    return device


async def cancel_queued_execution(db: AsyncSession, execution_id: int, now: datetime) -> bool:
    result = await db.execute(
        update(Execution)
        .where(Execution.id == execution_id, Execution.status == "queued")
        .values(
            status="cancelled",
            stop_requested_at=now,
            finished_at=now,
            finalized_at=now,
        )
    )
    return int(getattr(result, "rowcount", 0)) == 1


async def release_device(device: Device, *, available: bool, now: datetime) -> Device:
    device.status = "idle" if available else "offline"
    device.locked_by_execution = None
    device.updated_at = now
    return device


async def mark_devices_offline(devices: list[Device], now: datetime) -> None:
    for device in devices:
        device.status = "offline"
        device.locked_by_execution = None
        device.updated_at = now
