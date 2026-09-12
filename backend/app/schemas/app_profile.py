"""APP 档案相关请求/响应模型（方案 §4）。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

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
    model_config = {"extra": "forbid"}

    type: Literal["suite", "case", "step", "assertion", "suite_step"]
    # suite/suite_step 使用 suite_id；case/step/assertion 使用 occurrence 身份。
    suite_id: int | None = None
    suite_case_id: int | None = None
    node_key: str | None = None

    @model_validator(mode="after")
    def _validate_identity(self) -> "SkipTarget":
        if self.type in ("suite", "suite_step"):
            if self.suite_id is None or self.suite_case_id is not None:
                raise ValueError("套件目标必须使用 suite_id，不能携带 suite_case_id")
        else:
            if self.suite_case_id is None or self.suite_id is not None:
                raise ValueError("用例及节点目标必须使用 suite_case_id，不能携带 suite_id")
        if self.type in ("suite", "case") and self.node_key is not None:
            raise ValueError("套件/用例目标不允许 node_key")
        if self.type in ("step", "assertion", "suite_step") and not self.node_key:
            raise ValueError("节点目标必须提供 node_key")
        return self


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


# ---------- occurrence 变量覆盖（方案 §10.20） ----------
class ProfileVariableReference(BaseModel):
    """同名变量在某个步骤/断言节点上的引用与覆盖。"""

    node_type: Literal["step", "assertion"]
    node_key: str
    order: int | None = None
    node_name: str
    inherited_value: str | None = None
    override_enabled: bool
    override_value: str


class ProfileCaseVariableItem(BaseModel):
    name: str
    reference_count: int
    # mixed=同名变量在不同节点存在不同覆盖值
    status: str
    inherited_value: str | None = None
    inherited_scope: str | None = None
    references: list[ProfileVariableReference] = Field(default_factory=list)


class ProfileSuiteCaseVariablesOut(BaseModel):
    profile_id: int
    profile_revision: int
    suite_case_id: int
    case_id: int
    case_name: str
    total: int
    variables: list[ProfileCaseVariableItem]


class ProfileVariableUpdateItem(BaseModel):
    model_config = {"extra": "forbid"}

    name: str
    # null 表示删除当前 occurrence 上的变量覆盖
    value: str | None = None


class ProfileVariableOverrideBatchRequest(BaseModel):
    request_id: str | None = None
    expected_revision: int = Field(ge=1)
    updates: list[ProfileVariableUpdateItem] = Field(min_length=1)


class MyVariableReference(BaseModel):
    suite_id: int | None = None
    suite_name: str | None = None
    suite_case_id: int | None = None
    case_id: int | None = None
    case_name: str | None = None
    node_type: Literal["action", "assertion"]
    node_key: str
    node_name: str
    action: str | None = None
    type: str | None = None
    phase: str | None = None
    order: int | None = None


class MyVariableItem(BaseModel):
    variable_id: int
    name: str
    scope: Literal["project", "suite", "case"]
    project_id: int | None = None
    suite_id: int | None = None
    suite_name: str | None = None
    case_id: int | None = None
    case_name: str | None = None
    public_value: str | None = None
    user_value: str | None = None
    display_value: str
    overridden: bool
    reference_count: int
    references: list[MyVariableReference] = Field(default_factory=list)
    is_sensitive: bool


class MyVariablesPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[MyVariableItem]


class MyVariableUpdateItem(BaseModel):
    model_config = {"extra": "forbid"}

    variable_id: int
    value: str | None = None


class MyVariableBatchRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: UUID
    updates: list[MyVariableUpdateItem] = Field(min_length=1)


# ---------- 工作台（方案 §4.4） ----------


class WorkspaceNode(BaseModel):
    node_type: str
    id: int | None = None
    # 用例节点的编排项身份（test_suite_cases.id），重复编排时用于区分展开与覆盖
    suite_case_id: int | None = None
    node_key: str | None = None
    element_id: int | None = None
    element_name: str | None = None
    name: str
    registry_key: str | None = None
    phase: str | None = None
    order: int | None = None
    effective_status: str
    status_source: str
    reason: dict[str, str] | None = None
    override_count: int = 0
    child_count: int = 0
    difference_count: int = 0
    has_children: bool = False
    updated_at: datetime | None = None


class WorkspacePage(BaseModel):
    profile_revision: int
    test_asset_revision: int
    total: int
    # 不受 keyword/status 筛选影响的可执行套件总数。前端用它维护跨分页的
    # “默认全选 + 补集取消”状态，不能用当前页 total 代替。
    execution_selectable_total: int = 0
    page: int
    page_size: int
    items: list[WorkspaceNode]


class DifferenceRow(BaseModel):
    target_type: str
    path: str
    reason_code: str | None = None
    reason_note: str | None = None
    source_type: str | None = None
    override: bool = False


class DifferencePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[DifferenceRow]


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
