from datetime import datetime

from pydantic import BaseModel


class AgentCreate(BaseModel):
    hostname: str | None = None
    platform: str | None = None


class AgentListItem(BaseModel):
    id: int
    agent_id: str
    hostname: str | None
    platform: str | None
    ip: str | None
    status: str
    version: str | None
    last_heartbeat: datetime | None
    created_at: datetime
    device_count: int = 0

    model_config = {"from_attributes": True}


class AgentCreateResponse(BaseModel):
    id: int
    agent_id: str
    agent_key: str  # 仅创建时一次性返回
    hostname: str | None
    platform: str | None
    status: str

    model_config = {"from_attributes": True}


class DeviceOut(BaseModel):
    id: int
    agent_id: int
    agent_name: str | None = None
    name: str
    platform: str
    platform_version: str | None
    udid: str
    device_type: str
    status: str
    locked_by_execution: int | None
    last_heartbeat: datetime | None

    model_config = {"from_attributes": True}


class DevicePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[DeviceOut]
