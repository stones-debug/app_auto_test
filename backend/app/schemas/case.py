from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

# ---------- CR-09/Step 10：协议参数模型来自 Registry 单一来源（generated） ----------
from app.schemas.generated_case_params import (  # noqa: F401  (ParamsBase 由生成模块提供)
    ASSERTION_PARAM_MODELS,
    ELEMENT_LABELS,
    KNOWN_ACTIONS,
    KNOWN_ASSERTIONS,
    STEP_NEEDS_ELEMENT,
    STEP_PARAM_MODELS,
)


def _ensure_node_key(raw: str | None) -> str:
    """步骤/断言稳定标识：缺失时兜底生成 UUID；提供但非法（非 UUID 格式）直接拒绝（方案 §2.8）。"""
    if raw is None:
        return str(uuid4())
    if isinstance(raw, UUID):
        return str(raw)
    return str(UUID(raw))


class ActionNodeCreate(BaseModel):
    """统一执行流中的动作节点。"""

    kind: Literal["action"] = "action"
    order: int = Field(ge=1)
    action: str
    phase: Literal["setup", "main", "teardown"] = "main"
    element_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    # 方案 §2.8：稳定标识，缺失/非法由后端兜底生成并回写响应
    key: str | None = None
    # 失败后继续为节点顶层字段（不进入 action params）。
    continue_on_failure: bool = False
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_action_params(self):
        if self.action not in KNOWN_ACTIONS:
            raise ValueError(f"未知动作: {self.action}")
        model = STEP_PARAM_MODELS[self.action]
        self.params = model(**self.params).model_dump(exclude_none=False)
        # Step 12：需要元素的动作必须提供 element_id（统一拒绝，覆盖所有 needs_element 动作）
        if self.action in STEP_NEEDS_ELEMENT and self.element_id is None:
            label = ELEMENT_LABELS.get(self.action, "元素")
            raise ValueError(f"动作 {self.action} 需要元素（{label}）")
        # Step 12：目标文字去除首尾空格后不能为空
        if self.action == "swipe_in_element_find_text_click":
            target_text = self.params.get("target_text")
            if not isinstance(target_text, str) or not target_text.strip():
                raise ValueError("目标文字不能为空")
        self.key = _ensure_node_key(self.key)
        return self


class AssertionNodeCreate(BaseModel):
    order: int = Field(ge=1)
    kind: Literal["assertion"] = "assertion"
    phase: Literal["setup", "main", "teardown"] = "main"
    type: str
    element_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    # 方案 §2.8：稳定标识
    key: str | None = None
    # 断言最大等待时间；0 表示只检查一次。
    max_wait_seconds: int = Field(default=10, ge=0, le=300)
    continue_on_failure: bool = False
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_type_params(self):
        if self.type not in KNOWN_ASSERTIONS:
            raise ValueError(f"未知断言: {self.type}")
        model = ASSERTION_PARAM_MODELS[self.type]
        self.params = model(**self.params).model_dump(exclude_none=False)
        self.key = _ensure_node_key(self.key)
        return self


FlowNode = Annotated[
    ActionNodeCreate | AssertionNodeCreate,
    Field(discriminator="kind"),
]

# 旧名称仅作为 Python 导入别名，新的 HTTP/JSON 结构不再嵌套断言。
StepCreate = ActionNodeCreate
AssertionCreate = AssertionNodeCreate


def _dump_nodes(nodes: list[FlowNode]) -> list[dict[str, Any]]:
    return [node.model_dump() for node in nodes]


def _flatten_legacy_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for step in steps:
        action = {key: value for key, value in step.items() if key != "assertions"}
        action["kind"] = "action"
        action["order"] = len(result) + 1
        result.append(action)
        for assertion in step.get("assertions") or []:
            node = dict(assertion)
            node["kind"] = "assertion"
            node["order"] = len(result) + 1
            node.setdefault("phase", action.get("phase") or "main")
            node.setdefault("max_wait_seconds", 10)
            result.append(node)
    counters: dict[str, int] = {}
    for node in result:
        phase = str(node.get("phase") or "main")
        counters[phase] = counters.get(phase, 0) + 1
        node["order"] = counters[phase]
    return result


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    module_id: int | None = None
    description: str | None = None
    status: str = Field(default="draft", pattern="^(draft|active|disabled)$")
    flow_nodes: list[FlowNode] = Field(default_factory=list)
    steps: list[dict[str, Any]] | None = None
    variables: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _dump_nodes(self):
        # 校验后转回 dict，供 ORM JSON 列直接存储
        object.__setattr__(self, "flow_nodes", _dump_nodes(self.flow_nodes))
        return self

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_steps(cls, values):
        if isinstance(values, dict) and not values.get("flow_nodes") and values.get("steps"):
            values = dict(values)
            values["flow_nodes"] = _flatten_legacy_steps(values["steps"])
        return values


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    module_id: int | None = None
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|active|disabled)$")
    flow_nodes: list[FlowNode] | None = None
    steps: list[dict[str, Any]] | None = None
    variables: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _dump_nodes(self):
        if self.flow_nodes is not None:
            object.__setattr__(self, "flow_nodes", _dump_nodes(self.flow_nodes))
        return self

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_steps(cls, values):
        if isinstance(values, dict) and "steps" in values and "flow_nodes" not in values:
            values = dict(values)
            values["flow_nodes"] = _flatten_legacy_steps(values["steps"] or [])
        return values


class CaseBatchDeleteRequest(BaseModel):
    project_id: int | None = None
    ids: list[int] = Field(default_factory=list)
    case_ids: list[int] | None = None

    @model_validator(mode="after")
    def _resolve_ids(self) -> "CaseBatchDeleteRequest":
        merged = [*self.ids, *(self.case_ids or [])]
        if not merged:
            raise ValueError("at least one case id is required")
        self.ids = list(dict.fromkeys(merged))
        self.case_ids = None
        return self


class CaseBatchDeleteResponse(BaseModel):
    deleted: int
    deleted_count: int
    ids: list[int]


class CaseOut(BaseModel):
    id: int
    project_id: int
    module_id: int | None
    name: str
    description: str | None
    status: str
    flow_nodes: list[dict[str, Any]]
    # 仅用于读取旧客户端缓存，服务端新建/更新不再接收 steps。
    steps: list[dict[str, Any]] | None = None
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
