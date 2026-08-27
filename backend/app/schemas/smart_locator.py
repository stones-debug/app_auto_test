"""智能元素定位协议（协议 V2 风格，字段名与用户协议一致）。

安全约束：本结构本身不接受任何「原始 XPath / UiAutomator 表达式」字段——
attribute / operator 均为白名单 Literal，value 类型受控，从根本上阻止把任意
定位表达式塞入配置。所有规则由 Pydantic 校验层强制。
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 布尔属性：value 必须是 bool，operator 只能是 equals
_BOOL_ATTRIBUTES = {"clickable", "enabled", "selected", "displayed"}
# 非布尔属性：value 必须是 str（1..200），operator 可任选
_STR_ATTRIBUTES = {"text", "content_desc", "resource_id", "class_name", "package"}
_ATTRIBUTES = _BOOL_ATTRIBUTES | _STR_ATTRIBUTES

_OPERATORS = {"equals", "contains", "starts_with", "ends_with", "regex"}

# path 段 axis：ancestor/parent 需 depth（1..5），其余不得设 depth
_DEPTH_AXES = {"ancestor", "parent"}


class SmartCondition(BaseModel):
    attribute: Literal["text", "content_desc", "resource_id", "class_name", "package",
                       "clickable", "enabled", "selected", "displayed"]
    operator: Literal["equals", "contains", "starts_with", "ends_with", "regex"]
    value: str | bool

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value, info):
        attribute = info.data.get("attribute")
        if attribute in _BOOL_ATTRIBUTES:
            if not isinstance(value, bool):
                raise ValueError("布尔属性 value 必须为 bool")
            return value
        # 非布尔属性
        if not isinstance(value, str):
            raise ValueError("非布尔属性 value 必须为 str")
        if len(value) < 1:
            raise ValueError("value 长度至少为 1")
        if len(value) > 200:
            raise ValueError("value 长度不能超过 200")
        return value

    @model_validator(mode="after")
    def _validate_operator(self):
        if self.attribute in _BOOL_ATTRIBUTES and self.operator != "equals":
            raise ValueError("布尔属性 operator 只能是 equals")
        if self.operator == "regex":
            try:
                re.compile(self.value if isinstance(self.value, str) else str(self.value))
            except re.error as exc:
                raise ValueError(f"无效正则: {exc}") from exc
        return self


class SmartPathSegment(BaseModel):
    axis: Literal["parent", "ancestor", "child", "descendant",
                  "following_sibling", "preceding_sibling"]
    depth: int | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_depth(self):
        if self.axis in _DEPTH_AXES:
            if self.depth is None:
                raise ValueError("ancestor/parent 轴必须显式指定 depth (1..5)")
            if not (1 <= self.depth <= 5):
                raise ValueError("ancestor depth 必须在 1..5 之间")
        else:
            if self.depth is not None:
                raise ValueError("非 ancestor/parent 轴不允许设置 depth")
        return self


class SmartAlternative(BaseModel):
    anchor: list[SmartCondition] | None = Field(default=None, min_length=1, max_length=20)
    path: list[SmartPathSegment] | None = Field(default=None, min_length=1, max_length=3)
    target: list[SmartCondition] = Field(min_length=1, max_length=20)  # 必填，同为 AND


class SmartSearchConfig(BaseModel):
    scroll: bool = True
    direction: Literal["up", "down"] = "up"
    max_swipes: int = Field(default=8, ge=1, le=20)
    duration_ms: int = Field(default=500, ge=100, le=2000)
    settle_ms: int = Field(default=300, ge=0, le=2000)


class SmartSelection(BaseModel):
    policy: Literal["unique", "index"] = "unique"
    index: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_index(self):
        if self.policy == "unique":
            if self.index is not None:
                raise ValueError("policy='unique' 时不允许设置 index")
        else:  # policy == "index"
            # index 只能用户显式配置，不允许默认退化为 first
            if self.index is None:
                raise ValueError("policy='index' 时必须显式指定 index (>=1)")
        return self


class SmartLocatorConfig(BaseModel):
    version: Literal[1] = 1
    alternatives: list[SmartAlternative] = Field(min_length=1, max_length=10)
    search: SmartSearchConfig = Field(default_factory=SmartSearchConfig)
    selection: SmartSelection = Field(default_factory=SmartSelection)
