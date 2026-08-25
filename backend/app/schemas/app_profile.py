"""APP 档案相关请求/响应模型（方案 §4）。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_REASON_CODES = {"unsupported", "not_adapted", "deprecated", "environment_limit", "other"}


class ErrorDetail(BaseModel):
    """统一错误体（方案 §4.1）。"""

    code: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    field_errors: list[dict[str, Any]] = Field(default_factory=list)


# ---------- 档案 ----------


class AppProfileCreate(BaseModel):
    request_id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    description: str | None = None
    inherit_all: bool = True


class AppProfileUpdate(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    status: Literal["active", "disabled"] | None = None
    inherit_all: bool | None = None


class AppProfileDelete(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)


class AppProfileOut(BaseModel):
    id: int
    project_id: int
    name: str
    code: str
    description: str | None
    status: str
    inherit_all: bool
    revision: int
    release_count: int = 0
    skip_counts: dict[str, int] = Field(default_factory=dict)
    override_counts: dict[str, int] = Field(default_factory=dict)
    created_by: int | None
    updated_by: int | None
    updated_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class AppProfilePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AppProfileOut]


# ---------- 发布版本 ----------


class ReleaseCreate(BaseModel):
    request_id: str | None = None
    version: str = Field(min_length=1, max_length=64)
    build_number: str | None = Field(default=None, max_length=64)
    description: str | None = None


class ReleaseUpdate(BaseModel):
    request_id: str | None = None
    version: str | None = Field(default=None, min_length=1, max_length=64)
    build_number: str | None = Field(default=None, max_length=64)
    description: str | None = None
    status: Literal["active", "disabled"] | None = None


class ReleaseDelete(BaseModel):
    request_id: str | None = None


class ReleaseOut(BaseModel):
    id: int
    profile_id: int
    version: str
    build_number: str | None
    description: str | None
    status: str
    created_by: int | None
    updated_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class ReleasePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ReleaseOut]


# ---------- 批量跳过（方案 §4.5） ----------


class SkipReason(BaseModel):
    code: str = Field(pattern=r"^(unsupported|not_adapted|deprecated|environment_limit|other)$")
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _note_required_for_other(cls, v: str | None, info):
        code = info.data.get("code")
        if code == "other" and not (v or "").strip():
            raise ValueError("reason_code='other' 时必须填写备注")
        return v


class SkipTarget(BaseModel):
    type: Literal["suite", "case", "step", "assertion"]
    suite_id: int | None = None
    case_id: int | None = None
    node_key: str | None = None


class SkipBatchRequest(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)
    operation: Literal["skip", "restore"]
    reason: SkipReason | None = None
    targets: list[SkipTarget] = Field(min_length=1)


class SkipBatchResultItem(BaseModel):
    index: int
    status: Literal["changed", "unchanged", "invalid"]
    rule_id: int | None = None
    error: str | None = None


class SkipBatchResponse(BaseModel):
    request_id: str
    revision_before: int
    revision_after: int
    changed: int
    unchanged: int
    results: list[SkipBatchResultItem]


# ---------- 覆盖（方案 §4.6） ----------


class ElementOverrideUpsert(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)
    locator_type: str = Field(min_length=1, max_length=50)
    locator_value: str = Field(min_length=1)


class ElementOverrideDelete(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)


class VariableOverrideUpsert(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)
    value: str = Field(default="")
    description: str | None = Field(default=None, max_length=500)


class VariableOverrideDelete(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)


class NodeOverridePatch(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)
    patch: dict[str, Any] = Field(default_factory=dict)

    @field_validator("patch")
    @classmethod
    def _patch_not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("patch 不能为空")
        return v


class NodeOverrideDelete(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)


# ---------- 幂等响应 ----------


class IdempotentResponse(BaseModel):
    request_id: str
    revision_before: int
    revision_after: int
    data: dict[str, Any] = Field(default_factory=dict)


def ensure_reason_valid(code: str, note: str | None) -> None:
    """原因合法性（方案 §2.3：other 必须备注）。"""
    if code not in _REASON_CODES:
        raise ValueError(f"非法原因代码: {code}")
    if code == "other" and not (note or "").strip():
        raise ValueError("reason_code='other' 时必须填写备注")
