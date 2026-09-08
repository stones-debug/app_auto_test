"""Short-lived, one-time execution preview snapshots."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ExecutionPrepare(Base):
    __tablename__ = "execution_prepares"
    __table_args__ = (
        Index("idx_execution_prepares_user_project", "user_id", "project_id"),
        Index("idx_execution_prepares_expires", "expires_at"),
        Index("idx_execution_prepares_active", "expires_at", "consumed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    app_profile_id: Mapped[int] = mapped_column(ForeignKey("app_profiles.id"), nullable=False)
    app_release_id: Mapped[int] = mapped_column(ForeignKey("app_profile_releases.id"), nullable=False)
    app_release_version: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    test_asset_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    resolution_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
