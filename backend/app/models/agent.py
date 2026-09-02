from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)  # PSK
    agent_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(255))
    platform: Mapped[str | None] = mapped_column(String(50))  # windows / macos / linux
    ip: Mapped[str | None] = mapped_column(String(45))
    status: Mapped[str] = mapped_column(String(20), default="offline")  # offline / online / busy
    version: Mapped[str | None] = mapped_column(String(50))
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Step 6：软注销。保留 Agent/Device/Execution/Report 行供历史引用；
    # 安装实例可凭有效用户 Agent Key 重新激活（bind_agent 旋转机器 PSK）。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Device(Base, TimestampMixin):
    __tablename__ = "devices"
    __table_args__ = (
        Index("idx_devices_status", "status"),
        Index("idx_devices_agent", "agent_id"),
        # §5.2：同一 Agent 下设备 UDID 唯一（CR-17 建议的唯一键）
        UniqueConstraint("agent_id", "udid", name="uq_devices_agent_udid"),
        CheckConstraint(
            "status IN ('idle', 'busy', 'offline', 'unauthorized', 'error')",
            name="ck_devices_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # android / ios
    platform_version: Mapped[str | None] = mapped_column(String(50))
    udid: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[str] = mapped_column(String(20), default="emulator")  # real / emulator / simulator
    status: Mapped[str] = mapped_column(
        String(20), default="idle"
    )  # idle / busy / offline / unauthorized / error
    # Windows 方案 §3：usb / wifi；无线连接地址（ip:port），USB 可为空
    connection_type: Mapped[str] = mapped_column(String(10), default="usb")
    address: Mapped[str | None] = mapped_column(String(255))
    locked_by_execution: Mapped[int | None] = mapped_column(ForeignKey("executions.id"))
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserAgentKey(Base, TimestampMixin):
    """Windows 方案 §3.1：每用户一条专属 Key（uak_<public_id>_<secret>）。

    - key_hash：Argon2(secret)，用于绑定接口校验；
    - encrypted_secret：Fernet 密文（AGENT_USER_KEY_ENCRYPTION_KEY），数据库不裸存明文；
    - 前端可长期查看明文 Key（GET 时解密）。
    """

    __tablename__ = "user_agent_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    public_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)


class AgentUser(Base, TimestampMixin):
    """Windows 方案 §3.1：Agent 与用户多对多绑定（组合唯一）。

    revoke_credential_hash：该绑定撤销凭据的哈希（机器侧解绑用，明文只返回一次）。
    """

    __tablename__ = "agent_users"
    __table_args__ = (
        UniqueConstraint("agent_id", "user_id", name="uq_agent_users_agent_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    revoke_credential_hash: Mapped[str] = mapped_column(String(255), nullable=False)


class DevicePreference(Base, TimestampMixin):
    """Windows 方案 §3.1：每用户一个默认设备（设备删除时自动清空）。"""

    __tablename__ = "device_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
