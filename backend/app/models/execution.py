from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class Execution(Base, TimestampMixin):
    __tablename__ = "executions"
    __table_args__ = (
        Index("idx_executions_status", "status"),
        Index("idx_executions_project", "project_id"),
        Index("idx_executions_retry", "retry_of"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # case / suite / batch
    suite_id: Mapped[int | None] = mapped_column(ForeignKey("test_suites.id"))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("test_cases.id"))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    retry_of: Mapped[int | None] = mapped_column(ForeignKey("executions.id"))


class ExecutionCase(Base, TimestampMixin):
    __tablename__ = "execution_cases"
    __table_args__ = (Index("idx_exec_cases_execution", "execution_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id"), nullable=False)
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_name: Mapped[str] = mapped_column(String(255), nullable=False)  # 快照
    module_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    steps_snapshot: Mapped[list] = mapped_column(JSON, nullable=False)
    assertions_snapshot: Mapped[list] = mapped_column(JSON, nullable=False)
    elements_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # V1.1 §10.3
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)


class ExecutionStep(Base, TimestampMixin):
    __tablename__ = "execution_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_case_id: Mapped[int] = mapped_column(ForeignKey("execution_cases.id"), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int | None] = mapped_column(Integer)
    actual_value: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(Text)


class ExecutionAssertion(Base, TimestampMixin):
    __tablename__ = "execution_assertions"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_step_id: Mapped[int] = mapped_column(ForeignKey("execution_steps.id"), nullable=False)
    assertion_type: Mapped[str] = mapped_column(String(50), nullable=False)
    expected_value: Mapped[str | None] = mapped_column(Text)
    actual_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pass / fail
    error_message: Mapped[str | None] = mapped_column(Text)


class ExecutionLog(Base, TimestampMixin):
    __tablename__ = "execution_logs"
    __table_args__ = (
        Index("idx_logs_execution", "execution_id"),
        Index("idx_logs_timestamp", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)  # DEBUG / INFO / WARN / ERROR
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="worker")  # worker / agent / appium


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id"), nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    success_rate: Mapped[float] = mapped_column(Integer, default=0)
    duration: Mapped[int | None] = mapped_column(Integer)
    report_path: Mapped[str | None] = mapped_column(Text)


class ExecutionQueue(Base, TimestampMixin):
    __tablename__ = "execution_queue"
    __table_args__ = (
        Index(
            "idx_queue_pending",
            "status",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / claimed / done / failed
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)