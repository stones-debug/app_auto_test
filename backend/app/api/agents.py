from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_platform_admin
from app.core.database import get_db
from app.models import User
from app.schemas.agent import (
    AgentCreate,
    AgentCreateResponse,
    AgentListItem,
    DefaultDeviceOut,
    DefaultDeviceUpdate,
    DeviceOut,
    DevicePage,
    DeviceReleaseResponse,
)
from app.services import agent_service
from app.utils.pagination import get_pagination

router = APIRouter(tags=["设备与 Agent 管理"])


@router.get("/agents", response_model=list[AgentListItem])
async def list_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.list_agents(db, user)


@router.post("/agents", response_model=AgentCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    _admin: User = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.create_agent(db, body)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: int,
    _admin: User = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
):
    await agent_service.delete_agent(db, agent_id)


@router.delete("/agents/{agent_id}/bindings/me", status_code=status.HTTP_204_NO_CONTENT)
async def unbind_my_agent(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await agent_service.unbind_my_agent(db, user, agent_id)


@router.get("/agents/{agent_id}/devices", response_model=list[DeviceOut])
async def agent_devices(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.list_agent_devices(db, user, agent_id)


@router.get("/devices", response_model=DevicePage)
async def list_devices(
    platform: str = "",
    status_: str = Query(default="", alias="status"),
    pagination=Depends(get_pagination),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.list_devices(
        db, user, platform=platform, status_=status_, pagination=pagination
    )


@router.get("/devices/default", response_model=DefaultDeviceOut)
async def get_default_device(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.get_default_device(db, user)


@router.put("/devices/default", response_model=DefaultDeviceOut)
async def set_default_device(
    body: DefaultDeviceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.set_default_device(db, user, body)


@router.get("/devices/{device_id}", response_model=DeviceOut)
async def get_device(
    device_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.get_device(db, user, device_id)


@router.post("/devices/{device_id}/release", response_model=DeviceReleaseResponse)
async def release_device(
    device_id: int,
    _admin: User = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.release_device(db, device_id)
