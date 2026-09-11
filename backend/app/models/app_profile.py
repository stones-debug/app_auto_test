from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class AppProfile(Base, TimestampMixin, SoftDeleteMixin):
    """项目内 APP 产品形态配置档案，不绑定物理设备（方案 §2.2）。"""

    __tablename__ = "app_profiles"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_app_profiles_status"),
        CheckConstraint("revision >= 1", name="ck_app_profiles_revision"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_-]{1,63}$'", name="ck_app_profiles_code"),
        Index(
            "uq_app_profiles_project_code_active",
            "project_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_app_profiles_project_name_active",
            "project_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_app_profiles_project_status",
            "project_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    inherit_all: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AppProfileRelease(Base, TimestampMixin, SoftDeleteMixin):
    """APP 档案下的发布版本；V1 只用于执行选择和留痕（方案 §2.2）。"""

    __tablename__ = "app_profile_releases"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_app_profile_releases_status"),
        Index(
            "uq_app_profile_releases_version_active",
            "profile_id",
            "version",
            text("COALESCE(build_number, '')"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_app_profile_releases_profile_status",
            "profile_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("app_profiles.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    build_number: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AppProfileSkipRule(Base, TimestampMixin, SoftDeleteMixin):
    """APP 档案对公共套件、用例、节点（步骤/断言）的稀疏排除规则（方案 §2.3）。"""

    __tablename__ = "app_profile_skip_rules"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('suite', 'case', 'step', 'assertion', 'suite_step')",
            name="ck_profile_skip_target_type",
        ),
        CheckConstraint(
            "reason_code IN ('unsupported', 'not_adapted', 'deprecated', 'environment_limit', 'other')",
            name="ck_profile_skip_reason",
        ),
        CheckConstraint(
            "reason_code <> 'other' OR length(btrim(COALESCE(reason_note, ''))) > 0",
            name="ck_profile_skip_reason_note",
        ),
        CheckConstraint(
            "(target_type = 'suite' AND suite_id IS NOT NULL AND case_id IS NULL AND node_key IS NULL)"
            " OR (target_type = 'case' AND suite_id IS NOT NULL"
            " AND case_id IS NOT NULL AND node_key IS NULL)"
            " OR (target_type = 'suite_step' AND suite_id IS NOT NULL"
            " AND case_id IS NULL AND node_key IS NOT NULL)"
            " OR (target_type IN ('step', 'assertion') AND suite_id IS NOT NULL"
            " AND case_id IS NOT NULL AND node_key IS NOT NULL)",
            name="ck_profile_skip_target_shape",
        ),
        Index(
            "uq_profile_skip_suite_active",
            "profile_id",
            "suite_id",
            unique=True,
            postgresql_where=text("target_type = 'suite' AND deleted_at IS NULL"),
        ),
        Index(
            "uq_profile_skip_case_active",
            "profile_id",
            "suite_id",
            "case_id",
            unique=True,
            postgresql_where=text("target_type = 'case' AND deleted_at IS NULL"),
        ),
        Index(
            "uq_profile_skip_node_active",
            "profile_id",
            "suite_id",
            "target_type",
            "case_id",
            "node_key",
            unique=True,
            postgresql_where=text(
                "target_type IN ('step', 'assertion') AND deleted_at IS NULL"
            ),
        ),
        Index(
            "uq_profile_skip_suite_step_active",
            "profile_id",
            "suite_id",
            "node_key",
            unique=True,
            postgresql_where=text("target_type = 'suite_step' AND deleted_at IS NULL"),
        ),
        Index(
            "idx_profile_skip_profile_type",
            "profile_id",
            "target_type",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_profile_skip_case", "case_id", postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("app_profiles.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    suite_id: Mapped[int | None] = mapped_column(ForeignKey("test_suites.id"))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("test_cases.id"))
    node_key: Mapped[UUID | None] = mapped_column(nullable=True)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AppProfileElementOverride(Base, TimestampMixin, SoftDeleteMixin):
    """APP 档案元素定位覆盖，执行快照中替换公共定位（方案 §2.4）。"""

    __tablename__ = "app_profile_element_overrides"
    __table_args__ = (
        # 定位模式一致性：smart 必须有 config 且无 value；普通必须无 config 且有 value
        CheckConstraint(
            "(locator_type = 'smart' AND locator_config IS NOT NULL AND locator_value IS NULL) "
            "OR (locator_type <> 'smart' AND locator_config IS NULL AND locator_value IS NOT NULL)",
            name="ck_profile_element_locator_mode",
        ),
        Index(
            "uq_profile_element_override_active",
            "profile_id",
            "element_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_profile_element_override_element",
            "element_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("app_profiles.id"), nullable=False)
    element_id: Mapped[int] = mapped_column(ForeignKey("test_elements.id"), nullable=False)
    locator_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # 普通定位必须非空 locator_value；smart 定位必须合法 locator_config，规则由 schema 层校验
    # none_as_null=True：Python None 落库为 SQL NULL（而非 JSON null），配合 ck_profile_element_locator_mode
    locator_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    locator_config: Mapped[MutableDict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AppProfileVariableOverride(Base, TimestampMixin, SoftDeleteMixin):
    """APP 档案级变量，优先级高于套件变量（方案 §2.4）。"""

    __tablename__ = "app_profile_variable_overrides"
    __table_args__ = (
        # 变量名需与 variables 表及用例引用 ${...} 保持一致：允许中文/任意非空字符，
        # 仅禁止空串（此前误限定 ASCII，导致覆盖中文变量“我的设备ID”等报错）。
        CheckConstraint("name <> ''", name="ck_profile_variable_name"),
        Index(
            "uq_profile_variable_override_active",
            "profile_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("app_profiles.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AppProfileSuiteCaseVariableOverride(Base, TimestampMixin, SoftDeleteMixin):
    """APP 档案对单个套件编排项的变量覆盖。

    ``suite_case_id`` 是 occurrence 身份；同一用例在不同套件、或同一套件
    的重复编排，均拥有互不共享的档案变量值。
    """

    __tablename__ = "app_profile_suite_case_variable_overrides"
    __table_args__ = (
        CheckConstraint("name <> ''", name="ck_profile_suite_case_variable_name"),
        Index(
            "uq_profile_suite_case_variable_override_active",
            "profile_id",
            "suite_case_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_profile_suite_case_variable_override_membership",
            "suite_case_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("app_profiles.id", ondelete="CASCADE"), nullable=False
    )
    suite_case_id: Mapped[int] = mapped_column(
        ForeignKey("test_suite_cases.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AppProfileNodeOverride(Base, TimestampMixin, SoftDeleteMixin):
    """步骤/断言可变字段的白名单补丁，不允许修改节点身份与顺序（方案 §2.4）。

    用例节点覆盖以 ``suite_case_id``（``test_suite_cases.id``）为身份，避免同一用例在同一
    套件中重复编排时共享覆盖配置；套件前后置步骤的 ``suite_case_id`` 始终为空。
    """

    __tablename__ = "app_profile_node_overrides"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('step', 'assertion', 'suite_step')", name="ck_profile_node_override_type"
        ),
        CheckConstraint(
            "jsonb_typeof(patch) = 'object' AND patch <> '{}'::jsonb",
            name="ck_profile_node_override_patch",
        ),
        CheckConstraint(
            "(target_type IN ('step', 'assertion') AND suite_id IS NOT NULL AND case_id IS NOT NULL"
            " AND suite_case_id IS NOT NULL)"
            " OR (target_type = 'suite_step' AND suite_id IS NOT NULL AND case_id IS NULL"
            " AND suite_case_id IS NULL)",
            name="ck_profile_node_override_shape",
        ),
        Index(
            "uq_profile_node_override_active",
            "profile_id",
            "suite_case_id",
            "target_type",
            "node_key",
            unique=True,
            postgresql_where=text(
                "target_type IN ('step', 'assertion') AND deleted_at IS NULL"
            ),
        ),
        Index(
            "uq_profile_node_override_suite_step_active",
            "profile_id",
            "suite_id",
            "node_key",
            unique=True,
            postgresql_where=text("target_type = 'suite_step' AND deleted_at IS NULL"),
        ),
        Index(
            "idx_profile_node_override_case",
            "case_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_profile_node_override_suite",
            "suite_id",
            postgresql_where=text("deleted_at IS NULL AND suite_id IS NOT NULL"),
        ),
        Index(
            "idx_profile_node_override_suite_case",
            "suite_case_id",
            postgresql_where=text("deleted_at IS NULL AND suite_case_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("app_profiles.id"), nullable=False)
    # 方案 §2.3：套件上下文，避免共享用例在不同套件中的覆盖互相污染；套件步骤覆盖时 case_id 为空
    suite_id: Mapped[int | None] = mapped_column(ForeignKey("test_suites.id"))
    # 编排项身份（test_suite_cases.id）：区分同一用例在同一套件的重复编排；套件步骤为空
    suite_case_id: Mapped[int | None] = mapped_column(ForeignKey("test_suite_cases.id"))
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("test_cases.id"))
    node_key: Mapped[UUID] = mapped_column(nullable=False)
    patch: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AppProfileAuditLog(Base):
    """每个配置命令一条不可变审计记录（方案 §2.5）。"""

    __tablename__ = "app_profile_audit_logs"
    __table_args__ = (
        CheckConstraint(
            "action IN ('profile_create', 'profile_update', 'profile_disable',"
            " 'release_create', 'release_update', 'release_disable',"
            " 'skip_batch', 'restore_batch', 'element_override_upsert',"
            " 'element_override_restore', 'variable_override_upsert',"
            " 'variable_override_restore', 'node_override_upsert', 'node_override_restore',"
            " 'node_override_batch', 'occurrence_variable_override_batch',"
            " 'user_variable_override_batch')",
            name="ck_profile_audit_action",
        ),
        CheckConstraint("jsonb_typeof(changes) = 'array'", name="ck_profile_audit_changes"),
        CheckConstraint("revision_after >= revision_before", name="ck_profile_audit_revisions"),
        UniqueConstraint("profile_id", "actor_id", "request_id", name="uq_profile_audit_request_actor"),
        Index("idx_profile_audit_profile_time", "profile_id", text("created_at DESC")),
        Index("idx_profile_audit_project_time", "project_id", text("created_at DESC")),
        Index("idx_profile_audit_actor_time", "actor_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("app_profiles.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    request_id: Mapped[str] = mapped_column(nullable=False)
    batch_id: Mapped[str | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_role: Mapped[str | None] = mapped_column(String(16))
    revision_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    response_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    client_ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class ExecutionExclusion(Base):
    """执行创建时固化的不适用内容；不依赖后续规则和资产状态（方案 §2.6）。"""

    __tablename__ = "execution_exclusions"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('suite', 'case', 'step', 'assertion', 'suite_step')",
            name="ck_execution_exclusion_target",
        ),
        CheckConstraint(
            "source_type IN ('direct', 'inherited', 'empty_after_filter')",
            name="ck_execution_exclusion_source",
        ),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_execution_exclusion_details"),
        Index("idx_execution_exclusions_execution", "execution_id", "target_type"),
        Index("idx_execution_exclusions_profile_time", "app_profile_id", text("created_at DESC")),
        Index(
            "idx_execution_exclusions_case",
            "case_id_snapshot",
            postgresql_where=text("case_id_snapshot IS NOT NULL"),
        ),
        Index("idx_execution_exclusions_created_brin", text("created_at"), postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), nullable=False)
    app_profile_id: Mapped[int | None] = mapped_column(ForeignKey("app_profiles.id"))
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    suite_id_snapshot: Mapped[int | None] = mapped_column(Integer)
    suite_name_snapshot: Mapped[str | None] = mapped_column(String(255))
    case_id_snapshot: Mapped[int | None] = mapped_column(Integer)
    # 同一套件中重复编排同一用例时，用例 occurrence 的顺序快照。
    occurrence_order: Mapped[int | None] = mapped_column(Integer)
    case_name_snapshot: Mapped[str | None] = mapped_column(String(255))
    node_key: Mapped[UUID | None] = mapped_column(nullable=True)
    node_name_snapshot: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_rule_id: Mapped[int | None] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_note: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
