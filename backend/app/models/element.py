from sqlalchemy import ForeignKey, Integer, String, Text
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

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    page_name: Mapped[str | None] = mapped_column(String(255))
    platform: Mapped[str | None] = mapped_column(String(20), default="both")  # android / ios / both
    locator_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # id / resource_id / xpath / accessibility_id / ...
    locator_value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ElementGroup(Base, TimestampMixin):
    """元素库自定义页面分组（V3：分组可先于元素存在，空分组 count=0）。"""

    __tablename__ = "element_page_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
