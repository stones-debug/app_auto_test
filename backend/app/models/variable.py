from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class Variable(Base, TimestampMixin):
    __tablename__ = "variables"
    __table_args__ = (
        # CR-02：scope 与外键组合必须合法
        CheckConstraint(
            "(scope = 'global' AND project_id IS NULL AND suite_id IS NULL AND case_id IS NULL)"
            " OR (scope = 'project' AND project_id IS NOT NULL AND suite_id IS NULL AND case_id IS NULL)"
            " OR (scope = 'suite' AND project_id IS NOT NULL AND suite_id IS NOT NULL AND case_id IS NULL)"
            " OR (scope = 'case' AND project_id IS NOT NULL AND case_id IS NOT NULL AND suite_id IS NULL)",
            name="ck_variables_scope_fk",
        ),
        CheckConstraint("kind IN ('fixed','random_integer','random_choice')", name="ck_variables_kind"),
        # CR-02：作用域内名称唯一（并发创建冲突兜底）
        Index("uq_variables_global_name", "name", unique=True, postgresql_where=text("scope = 'global'")),
        Index("uq_variables_project_name", "project_id", "name", unique=True, postgresql_where=text("scope = 'project'")),
        Index("uq_variables_suite_name", "suite_id", "name", unique=True, postgresql_where=text("scope = 'suite'")),
        Index("uq_variables_case_name", "case_id", "name", unique=True, postgresql_where=text("scope = 'case'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)  # global / project / suite / case
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    suite_id: Mapped[int | None] = mapped_column(ForeignKey("test_suites.id"))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("test_cases.id"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="fixed", server_default=text("'fixed'"))
    spec: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # §5.2：token 哈希唯一（防重放/重复入库）
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        Index("idx_refresh_tokens_user", "user_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
