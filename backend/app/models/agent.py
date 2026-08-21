from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
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


class Device(Base, TimestampMixin):
    __tablename__ = "devices"
    __table_args__ = (
        Index("idx_devices_status", "status"),
        Index("idx_devices_agent", "agent_id"),
        # §5.2：同一 Agent 下设备 UDID 唯一（CR-17 建议的唯一键）
        UniqueConstraint("agent_id", "udid", name="uq_devices_agent_udid"),
        CheckConstraint("status IN ('idle', 'busy', 'offline', 'error')", name="ck_devices_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # android / ios
    platform_version: Mapped[str | None] = mapped_column(String(50))
    udid: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[str] = mapped_column(String(20), default="emulator")  # real / emulator / simulator
    status: Mapped[str] = mapped_column(String(20), default="idle")  # idle / busy / offline / error
    locked_by_execution: Mapped[int | None] = mapped_column(ForeignKey("executions.id"))
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
