from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

# ---------- CR-09/Step 10：协议参数模型来自 Registry 单一来源（generated） ----------
from app.schemas.generated_case_params import (  # noqa: F401  (ParamsBase 由生成模块提供)
    ASSERTION_PARAM_MODELS,
    KNOWN_ACTIONS,
    KNOWN_ASSERTIONS,
    STEP_PARAM_MODELS,
)


def _ensure_node_key(raw: str | None) -> str:
    """步骤/断言稳定标识：缺失时兜底生成 UUID；提供但非法（非 UUID 格式）直接拒绝（方案 §2.8）。"""
    if raw is None:
        return str(uuid4())
    if isinstance(raw, UUID):
        return str(raw)
    return str(UUID(raw))


class StepCreate(BaseModel):
    order: int = Field(ge=1)
    action: str
    phase: Literal["setup", "main", "teardown"] = "main"
    element_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    # 方案 §2.8：稳定标识，缺失/非法由后端兜底生成并回写响应
    key: str | None = None
    # Step 4：失败后继续为 Step 顶层字段（manifest controls.step，不进 params）
    continue_on_failure: bool = False

    @model_validator(mode="after")
    def _validate_action_params(self):
        if self.action not in KNOWN_ACTIONS:
            raise ValueError(f"未知动作: {self.action}")
        model = STEP_PARAM_MODELS[self.action]
        self.params = model(**self.params).model_dump(exclude_none=False)
        self.key = _ensure_node_key(self.key)
        return self


class AssertionCreate(BaseModel):
    order: int = Field(ge=1)
    type: str
    element_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    # 方案 §2.8：稳定标识
    key: str | None = None

    @model_validator(mode="after")
    def _validate_type_params(self):
        if self.type not in KNOWN_ASSERTIONS:
            raise ValueError(f"未知断言: {self.type}")
        model = ASSERTION_PARAM_MODELS[self.type]
        self.params = model(**self.params).model_dump(exclude_none=False)
        self.key = _ensure_node_key(self.key)
        return self


def _dump_steps(steps: list[StepCreate]) -> list[dict[str, Any]]:
    return [s.model_dump() for s in steps]


def _dump_assertions(assertions: list[AssertionCreate]) -> list[dict[str, Any]]:
    return [a.model_dump() for a in assertions]


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    module_id: int | None = None
    description: str | None = None
    status: str = Field(default="draft", pattern="^(draft|active|disabled)$")
    steps: list[StepCreate] = Field(default_factory=list)
    assertions: list[AssertionCreate] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _dump_nested(self):
        # 校验后转回 dict，供 ORM JSON 列直接存储
        self.steps = _dump_steps(self.steps)
        self.assertions = _dump_assertions(self.assertions)
        return self


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    module_id: int | None = None
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|active|disabled)$")
    steps: list[StepCreate] | None = None
    assertions: list[AssertionCreate] | None = None
    variables: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _dump_nested(self):
        if self.steps is not None:
            self.steps = _dump_steps(self.steps)
        if self.assertions is not None:
            self.assertions = _dump_assertions(self.assertions)
        return self


class CaseOut(BaseModel):
    id: int
    project_id: int
    module_id: int | None
    name: str
    description: str | None
    status: str
    steps: list[dict[str, Any]]
    assertions: list[dict[str, Any]]
    variables: dict[str, Any]
    created_by: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseListItem(BaseModel):
    id: int
    project_id: int
    module_id: int | None
    name: str
    description: str | None
    status: str
    module_name: str | None = None
    step_count: int = 0
    assertion_count: int = 0
    last_execution_status: str | None = None
    last_execution_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CasePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[CaseListItem]
