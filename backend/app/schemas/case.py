from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ---------- CR-09：按 action/type 严格校验嵌套参数 ----------


class ParamsBase(BaseModel):
    model_config = {"extra": "ignore"}


class LaunchAppParams(ParamsBase):
    package: str = ""
    activity: str | None = None
    no_reset: bool = True


class CloseAppParams(ParamsBase):
    package: str | None = None


class ClickParams(ParamsBase):
    wait_timeout: int | None = Field(default=None, ge=0)


class InputParams(ParamsBase):
    value: str = ""
    clear_first: bool = True


class SwipeParams(ParamsBase):
    direction: Literal["up", "down", "left", "right"] = "up"
    duration: int = Field(default=500, ge=0)


class SleepParams(ParamsBase):
    duration: float = Field(default=1, ge=0)


class ScreenshotParams(ParamsBase):
    filename: str | None = None


class GetTextParams(ParamsBase):
    variable_name: str | None = None


class GetAttributeParams(ParamsBase):
    attribute: str = ""
    variable_name: str | None = None


class TapCoordinateParams(ParamsBase):
    x: int
    y: int


STEP_PARAM_MODELS: dict[str, type[ParamsBase]] = {
    "launch_app": LaunchAppParams,
    "close_app": CloseAppParams,
    "click": ClickParams,
    "input": InputParams,
    "clear": ParamsBase,
    "swipe": SwipeParams,
    "scroll": ParamsBase,
    "back": ParamsBase,
    "sleep": SleepParams,
    "screenshot": ScreenshotParams,
    "get_text": GetTextParams,
    "get_attribute": GetAttributeParams,
    "tap_coordinate": TapCoordinateParams,
}

KNOWN_ACTIONS = frozenset(STEP_PARAM_MODELS)


class ElementExistsParams(ParamsBase):
    expected: Literal["exists", "not_exists"] = "exists"


class TextEqualsParams(ParamsBase):
    expected: str = ""
    trim: bool = False


class ExpectedTextParams(ParamsBase):
    expected: str = ""


class AttributeParams(ParamsBase):
    attribute: str = ""
    expected: str = ""


class RegexMatchParams(ParamsBase):
    pattern: str = ""


ASSERTION_PARAM_MODELS: dict[str, type[ParamsBase]] = {
    "element_exists": ElementExistsParams,
    "text_equals": TextEqualsParams,
    "text_contains": ExpectedTextParams,
    "text_not_contains": ExpectedTextParams,
    "attribute_equals": AttributeParams,
    "attribute_contains": AttributeParams,
    "value_equals": ExpectedTextParams,
    "regex_match": RegexMatchParams,
}

KNOWN_ASSERTIONS = frozenset(ASSERTION_PARAM_MODELS)


class StepCreate(BaseModel):
    order: int = Field(ge=1)
    action: str
    element_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    continue_on_failure: bool = False

    @model_validator(mode="after")
    def _validate_action_params(self):
        if self.action not in KNOWN_ACTIONS:
            raise ValueError(f"未知动作: {self.action}")
        model = STEP_PARAM_MODELS[self.action]
        self.params = model(**self.params).model_dump(exclude_none=False)
        return self


class AssertionCreate(BaseModel):
    order: int = Field(ge=1)
    type: str
    element_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None

    @model_validator(mode="after")
    def _validate_type_params(self):
        if self.type not in KNOWN_ASSERTIONS:
            raise ValueError(f"未知断言: {self.type}")
        model = ASSERTION_PARAM_MODELS[self.type]
        self.params = model(**self.params).model_dump(exclude_none=False)
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
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CasePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[CaseListItem]
