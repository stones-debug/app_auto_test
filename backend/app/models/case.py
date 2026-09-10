from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class TestCase(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "test_cases"
    __table_args__ = (
        # 用例模块筛选是列表页高频查询；部分索引跳过软删行
        Index(
            "idx_test_cases_module",
            "project_id",
            "module_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    module_id: Mapped[int | None] = mapped_column(ForeignKey("test_modules.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft / active / disabled
    # 统一执行流：动作和断言按同一个 order 顺序保存。
    flow_nodes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 旧列保留到一次性迁移完成；新代码只读写 flow_nodes。
    steps: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    variables: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class TestSuite(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "test_suites"
    __table_args__ = (
        # §2 套件前后置仅 Action Registry 步骤；稳定 UUID key + order + action + params + continue_on_failure
        CheckConstraint(
            "jsonb_typeof(setup_steps) = 'array'", name="ck_test_suites_setup_steps"
        ),
        CheckConstraint(
            "jsonb_typeof(teardown_steps) = 'array'", name="ck_test_suites_teardown_steps"
        ),
        # 套件模块树：module_id 只能指向 scope='suite' 的模块，跨表约束由服务层保证
        Index(
            "idx_test_suites_module",
            "project_id",
            "module_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    module_id: Mapped[int | None] = mapped_column(ForeignKey("test_modules.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    setup_steps: Mapped[list] = mapped_column(JSONB, default=list)
    teardown_steps: Mapped[list] = mapped_column(JSONB, default=list)


class TestSuiteCase(Base, TimestampMixin):
    __tablename__ = "test_suite_cases"
    __table_args__ = (
        Index("idx_suite_cases_order_stable", "suite_id", "sort_order", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    suite_id: Mapped[int] = mapped_column(ForeignKey("test_suites.id"), nullable=False)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
