import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_platform_admin
from app.core.database import get_db
from app.core.security import hash_psk
from app.models import Agent, Device, User
from app.schemas.agent import AgentCreate, AgentCreateResponse, AgentListItem, DeviceOut, DevicePage
from app.utils.pagination import get_pagination

router = APIRouter(tags=["设备与 Agent 管理"])


async def _get_agent_or_404(agent_id: int, db: AsyncSession) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 不存在")
    return agent


async def _get_device_or_404(device_id: int, db: AsyncSession) -> Device:
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")
    return device


@router.get("/agents", response_model=list[AgentListItem])
async def list_agents(
    _admin: User = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
):
    agents = (await db.execute(select(Agent).order_by(Agent.id))).scalars().all()
    counts: dict[int, int] = {}
    if agents:
        rows = (
            await db.execute(
                select(Device.agent_id, func.count()).group_by(Device.agent_id)
            )
        ).all()
        counts = {agent_id: count for agent_id, count in rows}
    items = [AgentListItem.model_validate(a) for a in agents]
    for item in items:
        item.device_count = counts.get(item.id, 0)
    return items


@router.post("/agents", response_model=AgentCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    _admin: User = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
):
    psk = f"sk-{secrets.token_hex(24)}"
    agent = Agent(
        agent_id=f"agent-{secrets.token_hex(4)}",
        agent_key=hash_psk(psk),
        hostname=body.hostname,
        platform=body.platform,
        status="offline",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    resp = AgentCreateResponse.model_validate(agent)
    resp.agent_key = psk  # 仅创建时返回一次明文 PSK
    return resp


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: int,
    _admin: User = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
):
    agent = await _get_agent_or_404(agent_id, db)
    await db.execute(delete(Device).where(Device.agent_id == agent_id))
    await db.delete(agent)
    await db.commit()


@router.get("/agents/{agent_id}/devices", response_model=list[DeviceOut])
async def agent_devices(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_agent_or_404(agent_id, db)
    devices = (
        await db.execute(select(Device).where(Device.agent_id == agent_id).order_by(Device.id))
    ).scalars().all()
    return [DeviceOut.model_validate(d) for d in devices]


async def _fill_agent_names(items: list[DeviceOut], db: AsyncSession) -> None:
    agent_ids = {d.agent_id for d in items}
    if not agent_ids:
        return
    agents = (await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))).scalars().all()
    names = {a.id: a.agent_id for a in agents}
    for item in items:
        item.agent_name = names.get(item.agent_id)


@router.get("/devices", response_model=DevicePage)
async def list_devices(
    platform: str = "",
    device_status: str = "",
    pagination=Depends(get_pagination),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Device)
    count_query = select(func.count()).select_from(Device)
    if platform:
        query = query.where(Device.platform == platform)
        count_query = count_query.where(Device.platform == platform)
    if device_status:
        query = query.where(Device.status == device_status)
        count_query = count_query.where(Device.status == device_status)
    total = await db.scalar(count_query)
    rows = (
        await db.execute(query.order_by(Device.id).offset(pagination.offset).limit(pagination.limit))
    ).scalars().all()
    items = [DeviceOut.model_validate(d) for d in rows]
    await _fill_agent_names(items, db)
    return {"total": total or 0, "page": pagination.page, "page_size": pagination.page_size, "items": items}


@router.get("/devices/{device_id}", response_model=DeviceOut)
async def get_device(
    device_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    device = await _get_device_or_404(device_id, db)
    return DeviceOut.model_validate(device)


@router.post("/devices/{device_id}/release", response_model=DeviceOut)
async def release_device(
    device_id: int,
    _admin: User = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
):
    """强制释放设备锁（应急）。执行状态由 Worker 超时扫描兜底。"""
    device = await _get_device_or_404(device_id, db)
    device.status = "idle"
    device.locked_by_execution = None
    device.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(device)
    return device
