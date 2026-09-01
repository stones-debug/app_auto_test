from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin

# 执行步骤阶段（§1 分层结构）：套件前后置 + 用例三阶段
STEP_PHASES = ("suite_setup", "case_setup", "case_main", "case_teardown", "suite_teardown")


class Execution(Base, TimestampMixin):
    __tablename__ = "executions"
    __table_args__ = (
        Index("idx_executions_status", "status"),
        Index("idx_executions_project", "project_id"),
        Index("idx_executions_retry", "retry_of"),
        Index("idx_executions_profile_created", "app_profile_id", text("created_at DESC")),
        Index("idx_executions_release_created", "app_release_id", text("created_at DESC")),
        CheckConstraint(
            "profile_revision IS NULL OR profile_revision >= 1", name="ck_executions_profile_revision"
        ),
        CheckConstraint(
            "test_asset_revision IS NULL OR test_asset_revision >= 1",
            name="ck_executions_asset_revision",
        ),
        CheckConstraint(
            "jsonb_typeof(profile_resolution_summary) = 'object'",
            name="ck_executions_resolution_summary",
        ),
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
    # 方案 §2.7：APP 档案与快照来源
    app_profile_id: Mapped[int | None] = mapped_column(ForeignKey("app_profiles.id"))
    app_profile_name_snapshot: Mapped[str | None] = mapped_column(String(100))
    app_release_id: Mapped[int | None] = mapped_column(ForeignKey("app_profile_releases.id"))
    app_release_version_snapshot: Mapped[str | None] = mapped_column(String(64))
    profile_revision: Mapped[int | None] = mapped_column(BigInteger)
    test_asset_revision: Mapped[int | None] = mapped_column(BigInteger)
    profile_resolution_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class ExecutionSuite(Base, TimestampMixin):
    """执行内套件（§1 执行与结果汇总单位）。虚拟套件（单用例无上下文）时 suite_id 为空。"""

    __tablename__ = "execution_suites"
    __table_args__ = (
        UniqueConstraint("execution_id", "suite_order", name="uq_execution_suites_execution_order"),
        Index(
            "uq_execution_suites_execution_suite",
            "execution_id",
            "suite_id",
            unique=True,
            postgresql_where=text("suite_id IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('pending','running','stopping','passed','failed','error','stopped','skipped')",
            name="ck_execution_suites_status",
        ),
        CheckConstraint(
            "jsonb_typeof(setup_steps_snapshot) = 'array'", name="ck_execution_suites_setup_steps"
        ),
        CheckConstraint(
            "jsonb_typeof(teardown_steps_snapshot) = 'array'", name="ck_execution_suites_teardown_steps"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id"), nullable=False)
    suite_id: Mapped[int | None] = mapped_column(ForeignKey("test_suites.id"))
    suite_name: Mapped[str] = mapped_column(String(255), nullable=False)  # 快照
    suite_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_virtual: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    setup_steps_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
    teardown_steps_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
    elements_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)


class ExecutionCase(Base, TimestampMixin):
    """执行内用例（§1 分层：隶属于 ExecutionSuite，可在多套件中重复出现）。"""

    __tablename__ = "execution_cases"
    __table_args__ = (
        Index("idx_exec_cases_suite", "execution_suite_id"),
        UniqueConstraint("execution_suite_id", "case_id", name="uq_execution_cases_suite_case"),
        UniqueConstraint("execution_suite_id", "case_order", name="uq_execution_cases_suite_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id"), nullable=False)
    execution_suite_id: Mapped[int] = mapped_column(ForeignKey("execution_suites.id"), nullable=False)
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_name: Mapped[str] = mapped_column(String(255), nullable=False)  # 快照
    module_name: Mapped[str | None] = mapped_column(String(255))
    case_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    steps_snapshot: Mapped[list] = mapped_column(JSON, nullable=False)
    # V3：动作和断言按同一个顺序固化。
    flow_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    elements_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)


class ExecutionNode(Base, TimestampMixin):
    """执行快照中的统一节点；一个节点只属于套件或用例。"""

    __tablename__ = "execution_nodes"
    __table_args__ = (
        Index(
            "uq_execution_nodes_suite_order",
            "execution_suite_id", "phase", "node_order", unique=True,
            postgresql_where=text("execution_suite_id IS NOT NULL AND execution_case_id IS NULL"),
        ),
        Index(
            "uq_execution_nodes_case_order",
            "execution_case_id", "phase", "node_order", unique=True,
            postgresql_where=text("execution_suite_id IS NULL AND execution_case_id IS NOT NULL"),
        ),
        CheckConstraint("kind IN ('action','assertion')", name="ck_execution_nodes_kind"),
        CheckConstraint(
            "(kind = 'action' AND action IS NOT NULL AND assertion_type IS NULL)"
            " OR (kind = 'assertion' AND action IS NULL AND assertion_type IS NOT NULL)",
            name="ck_execution_nodes_payload",
        ),
        CheckConstraint(
            "max_wait_seconds IS NULL OR max_wait_seconds BETWEEN 0 AND 300",
            name="ck_execution_nodes_max_wait",
        ),
        CheckConstraint(
            "phase IN ('suite_setup','case_setup','case_main','case_teardown','suite_teardown')",
            name="ck_execution_nodes_phase",
        ),
        CheckConstraint(
            "(phase IN ('suite_setup','suite_teardown') AND execution_suite_id IS NOT NULL AND execution_case_id IS NULL)"
            " OR (phase IN ('case_setup','case_main','case_teardown') AND execution_suite_id IS NULL AND execution_case_id IS NOT NULL)",
            name="ck_execution_nodes_parent_phase",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_suite_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_suites.id", ondelete="CASCADE")
    )
    execution_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_cases.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    node_order: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False, default="case_main")
    node_key: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str | None] = mapped_column(String(50))
    assertion_type: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    element_id: Mapped[int | None] = mapped_column(Integer)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    max_wait_seconds: Mapped[float | None] = mapped_column(Numeric(6, 2))
    continue_on_failure: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int | None] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    actual_value: Mapped[str | None] = mapped_column(Text)
    expected_value: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(Text)


class ExecutionStep(Base, TimestampMixin):
    """执行步骤，两种互斥父节点：套件步骤(suite)或用例步骤(case)，由阶段约束保证一致。"""

    __tablename__ = "execution_steps"
    __table_args__ = (
        # CR-11：套件步骤、用例步骤各自按阶段保持顺序唯一
        Index(
            "uq_execution_steps_suite_order",
            "execution_suite_id",
            "phase",
            "step_order",
            unique=True,
            postgresql_where=text("execution_suite_id IS NOT NULL AND execution_case_id IS NULL"),
        ),
        Index(
            "uq_execution_steps_case_order",
            "execution_case_id",
            "phase",
            "step_order",
            unique=True,
            postgresql_where=text("execution_suite_id IS NULL AND execution_case_id IS NOT NULL"),
        ),
        CheckConstraint(
            "phase IN ('suite_setup','case_setup','case_main','case_teardown','suite_teardown')",
            name="ck_execution_steps_phase",
        ),
        # 阶段与父节点一致性：套件阶段只能挂在套件，用例阶段只能挂在用例
        CheckConstraint(
            "(phase IN ('suite_setup','suite_teardown') AND execution_suite_id IS NOT NULL AND execution_case_id IS NULL)"
            " OR (phase IN ('case_setup','case_main','case_teardown') AND execution_suite_id IS NULL AND execution_case_id IS NOT NULL)",
            name="ck_execution_steps_parent_phase",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_suite_id: Mapped[int | None] = mapped_column(ForeignKey("execution_suites.id"))
    execution_case_id: Mapped[int | None] = mapped_column(ForeignKey("execution_cases.id"))
    phase: Mapped[str] = mapped_column(String(20), nullable=False, default="case_main")
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    source_key: Mapped[str | None] = mapped_column(String(255))
    source_order: Mapped[int | None] = mapped_column(Integer)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    # 步骤任意阶段失败是否继续（快照固化，防止运行时配置丢失；套件步同口径）
    continue_on_failure: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int | None] = mapped_column(Integer)
    actual_value: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(Text)


class ExecutionAssertion(Base, TimestampMixin):
    """步骤完成后执行的断言，按 execution_step_id 预建供 Agent 精确上报。"""

    __tablename__ = "execution_assertions"
    __table_args__ = (
        Index("idx_exec_assertions_step", "execution_step_id"),
        Index(
            "uq_exec_assertions_step_order",
            "execution_step_id",
            "assertion_order",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_step_id: Mapped[int] = mapped_column(ForeignKey("execution_steps.id"), nullable=False)
    assertion_order: Mapped[int] = mapped_column(Integer, nullable=False)
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
        CheckConstraint("not_applicable >= 0", name="ck_reports_not_applicable"),
        CheckConstraint("jsonb_typeof(exclusion_summary) = 'object'", name="ck_reports_exclusion_summary"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id"), nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    not_applicable: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default=text("0"))
    exclusion_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # CR-10：权威设计为 DECIMAL(5,2)，如 66.67
    success_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    duration: Mapped[int | None] = mapped_column(Integer)
    report_path: Mapped[str | None] = mapped_column(Text)
    # §2 报告三层统计：套件 / 步骤 / N/A（套件）
    suite_total: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    suite_passed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    suite_failed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    suite_error_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    suite_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    suite_success_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    step_total: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    step_passed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    step_failed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    step_error_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    step_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    step_success_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    not_applicable_suites: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    assertion_total: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    assertion_passed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    assertion_failed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    assertion_error_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    assertion_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    assertion_success_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), server_default=text("0"))


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
