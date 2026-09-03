from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class TestModule(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "test_modules"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("test_modules.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class TestElement(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "test_elements"
    __table_args__ = (
        # 定位模式一致性：smart 必须有 config 且无 value；普通必须无 config 且有 value
        CheckConstraint(
            "(locator_type = 'smart' AND locator_config IS NOT NULL AND locator_value IS NULL) "
            "OR (locator_type <> 'smart' AND locator_config IS NULL AND locator_value IS NOT NULL)",
            name="ck_test_elements_locator_mode",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    page_name: Mapped[str | None] = mapped_column(String(255))
    platform: Mapped[str | None] = mapped_column(String(20), default="both")  # android / ios / both
    # 适用范围（自由文本，默认 all 表示所有；创建时未填由服务端兜底 all）
    scope: Mapped[str] = mapped_column(String(100), default="all", nullable=False)
    locator_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # id / resource_id / xpath / accessibility_id / ... / smart
    # 普通定位必须非空 locator_value；smart 定位必须合法 locator_config，规则由 schema 层校验
    # none_as_null=True：Python None 落库为 SQL NULL（而非 JSON null），配合 ck_test_elements_locator_mode
    locator_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    locator_config: Mapped[MutableDict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ElementGroup(Base, TimestampMixin):
    """元素库页面分组（支持项目归属、分层、空分组和先建页面后建元素）。"""

    __tablename__ = "element_page_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    # nullable 用于保留历史上未关联项目的全局分组；新建分组由项目上下文关联。
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("element_page_groups.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
