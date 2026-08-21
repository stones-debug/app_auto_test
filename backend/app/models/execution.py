from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
    session_token: Mapped[str | None] = mapped_column(String(128))
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Windows 方案 §2：停止宽限期从用户请求停止时刻起算
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Windows 方案 §2：唯一终态汇总完成时刻（_mark_terminal 写入）
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    retry_of: Mapped[int | None] = mapped_column(ForeignKey("executions.id"))


class ExecutionCase(Base, TimestampMixin):
    __tablename__ = "execution_cases"
    __table_args__ = (
        Index("idx_exec_cases_execution", "execution_id"),
        # §5.2：批量去重语义下同一执行内用例唯一
        UniqueConstraint("execution_id", "case_id", name="uq_execution_cases_execution_case"),
    )

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
    __table_args__ = (
        # CR-11（§5.2）：同用例步骤顺序唯一
        UniqueConstraint("execution_case_id", "step_order", name="uq_execution_steps_case_order"),
    )

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
    __table_args__ = (
        # CR-11（§5.2）：每个执行最多一份报告（幂等汇总）
        UniqueConstraint("execution_id", name="uq_reports_execution_id"),
        CheckConstraint(
            "total >= 0 AND passed >= 0 AND failed >= 0 AND error_count >= 0 AND skipped >= 0",
            name="ck_reports_counts_nonneg",
        ),
        CheckConstraint("success_rate >= 0 AND success_rate <= 100", name="ck_reports_success_rate_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id"), nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    # CR-10：权威设计为 DECIMAL(5,2)，如 66.67
    success_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
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
        # §5.2：一个执行至多一个队列项；状态机约束
        UniqueConstraint("execution_id", name="uq_execution_queue_execution_id"),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'done', 'failed')",
            name="ck_execution_queue_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / claimed / done / failed
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
