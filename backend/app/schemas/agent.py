from datetime import datetime

from pydantic import BaseModel, Field


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
    connection_type: str = "usb"
    address: str | None = None
    locked_by_execution: int | None
    last_heartbeat: datetime | None

    model_config = {"from_attributes": True}


class DevicePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[DeviceOut]


# ---------- Windows 方案 §3.2：用户 Key 与 Agent 绑定 ----------


class AgentKeyOut(BaseModel):
    exists: bool
    public_id: str | None = None
    key: str | None = None


class AgentKeyCreateResponse(BaseModel):
    exists: bool = True
    public_id: str
    key: str


class BindRequest(BaseModel):
    user_key: str = Field(min_length=1, description="用户 Key（uak_<public_id>_<secret>）")
    machine_psk: str | None = None  # 首次绑定为空；追加绑定必填
    install_id: str | None = None  # 安装实例 ID（Agent 首次启动生成并持久化）
    hostname: str | None = None
    version: str | None = None
    platform: str | None = None


class BindResponse(BaseModel):
    agent_id: str
    machine_psk: str | None = None  # 仅首次绑定返回一次
    revoke_credential: str  # 该用户绑定的撤销凭据（仅返回一次）
    user_id: int


class AgentBindingOut(BaseModel):
    id: int
    agent_id: int
    user_id: int
    username: str


class DefaultDeviceOut(BaseModel):
    device_id: int | None
    device: DeviceOut | None = None
    available: bool = False
    reason: str = ""


class DefaultDeviceUpdate(BaseModel):
    device_id: int | None = None  # null 表示清除默认设备
