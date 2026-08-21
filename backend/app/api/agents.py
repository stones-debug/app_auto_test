import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    bound_agent_ids_subquery,
    get_current_user,
    require_agent_access,
    require_device_access,
    require_platform_admin,
)
from app.core.database import get_db
from app.core.security import hash_psk
from app.models import Agent, AgentUser, Device, DevicePreference, User
from app.schemas.agent import (
    AgentCreate,
    AgentCreateResponse,
    AgentListItem,
    DefaultDeviceOut,
    DefaultDeviceUpdate,
    DeviceOut,
    DevicePage,
)
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Windows 方案 §3.3：普通用户只返回其绑定 Agent；平台管理员返回全部。"""
    query = select(Agent)
    if not user.is_admin:
        query = query.where(Agent.id.in_(bound_agent_ids_subquery(user.id)))
    agents = (await db.execute(query.order_by(Agent.id))).scalars().all()
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
    # agent_users / device_preferences 由外键 CASCADE 一并清理
    await db.execute(delete(Device).where(Device.agent_id == agent_id))
    await db.delete(agent)
    await db.commit()


@router.delete("/agents/{agent_id}/bindings/me", status_code=status.HTTP_204_NO_CONTENT)
async def unbind_my_agent(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Windows 方案 §3.2：用户从前端撤销自己对该 Agent 的授权。"""
    await require_agent_access(agent_id, user, db)
    binding = (
        await db.execute(
            select(AgentUser).where(
                AgentUser.agent_id == agent_id,
                AgentUser.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if binding is not None:
        await db.delete(binding)
        await db.commit()


@router.get("/agents/{agent_id}/devices", response_model=list[DeviceOut])
async def agent_devices(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_agent_access(agent_id, user, db)
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
    status: str = "",
    pagination=Depends(get_pagination),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Windows 方案 §3.3：普通用户只返回其绑定 Agent 下的设备。"""
    query = select(Device)
    count_query = select(func.count()).select_from(Device)
    if not user.is_admin:
        query = query.where(Device.agent_id.in_(bound_agent_ids_subquery(user.id)))
        count_query = count_query.where(Device.agent_id.in_(bound_agent_ids_subquery(user.id)))
    if platform:
        query = query.where(Device.platform == platform)
        count_query = count_query.where(Device.platform == platform)
    if status:
        query = query.where(Device.status == status)
        count_query = count_query.where(Device.status == status)
    total = await db.scalar(count_query)
    rows = (
        await db.execute(query.order_by(Device.id).offset(pagination.offset).limit(pagination.limit))
    ).scalars().all()
    items = [DeviceOut.model_validate(d) for d in rows]
    await _fill_agent_names(items, db)
    return {"total": total or 0, "page": pagination.page, "page_size": pagination.page_size, "items": items}


@router.get("/devices/default", response_model=DefaultDeviceOut)
async def get_default_device(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Windows 方案 §4.2：返回当前默认设备及实时可用性。

    注意：本路由必须注册在 /devices/{device_id} 之前，否则 "default" 会被当作设备 ID。
    """
    pref = (
        await db.execute(select(DevicePreference).where(DevicePreference.user_id == user.id))
    ).scalar_one_or_none()
    if pref is None:
        return DefaultDeviceOut(device_id=None, reason="未设置默认设备")

    device = await db.get(Device, pref.device_id)
    if device is None:
        # 设备已被删除（外键 CASCADE 应已清空偏好，防御性处理）
        await db.delete(pref)
        await db.commit()
        return DefaultDeviceOut(device_id=None, reason="默认设备已不存在")

    out = DeviceOut.model_validate(device)
    if not (user.is_admin or await _user_bound_to_agent(db, device.agent_id, user.id)):
        return DefaultDeviceOut(device_id=device.id, device=out, available=False, reason="无权限")
    agent = await db.get(Agent, device.agent_id)
    if agent is None or agent.status != "online":
        return DefaultDeviceOut(device_id=device.id, device=out, available=False, reason="Agent 离线")
    if device.status != "idle" or device.locked_by_execution is not None:
        return DefaultDeviceOut(device_id=device.id, device=out, available=False, reason="设备忙或已被占用")
    return DefaultDeviceOut(device_id=device.id, device=out, available=True, reason="")


@router.put("/devices/default", response_model=DefaultDeviceOut)
async def set_default_device(
    body: DefaultDeviceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Windows 方案 §4.2：设置或清除默认设备；只能设置用户有权限的设备。"""
    pref = (
        await db.execute(select(DevicePreference).where(DevicePreference.user_id == user.id))
    ).scalar_one_or_none()
    if body.device_id is None:
        if pref is not None:
            await db.delete(pref)
            await db.commit()
        return DefaultDeviceOut(device_id=None, reason="已清除默认设备")

    device = await require_device_access(body.device_id, user, db)
    if pref is None:
        db.add(DevicePreference(user_id=user.id, device_id=device.id))
    else:
        pref.device_id = device.id
    await db.commit()
    return DefaultDeviceOut(device_id=device.id, device=DeviceOut.model_validate(device), reason="已设置默认设备")


@router.get("/devices/{device_id}", response_model=DeviceOut)
async def get_device(
    device_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    device = await require_device_access(device_id, user, db)
    return DeviceOut.model_validate(device)


async def _user_bound_to_agent(db: AsyncSession, agent_id: int, user_id: int) -> bool:
    row = await db.execute(
        select(AgentUser).where(AgentUser.agent_id == agent_id, AgentUser.user_id == user_id)
    )
    return row.scalar_one_or_none() is not None


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
