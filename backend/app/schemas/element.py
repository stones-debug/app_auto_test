from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.smart_locator import SmartLocatorConfig

# locator_type 总枚举（普通 9 种 + 智能定位 smart）
LOCATOR_TYPES = (
    "id|resource_id|xpath|accessibility_id|class_name|uiautomator|predicate|coordinate|custom|smart"
)
LOCATOR_TYPE_RE = f"^({LOCATOR_TYPES})$"
RESERVED_ELEMENT_PAGE_GROUP_NAMES = frozenset({"all", "全部", "未分组"})


def _normalize_element_page_group_name(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field}不能为空")
    if normalized.casefold() == "all" or normalized in RESERVED_ELEMENT_PAGE_GROUP_NAMES:
        raise ValueError("页面分组名称不能使用“all”“全部”或“未分组”")
    return normalized


def _validate_locator(locator_type: str | None, locator_value, locator_config) -> None:
    """普通定位必须非空 locator_value；smart 定位必须合法 locator_config。"""
    if locator_type == "smart":
        if locator_config is None:
            raise ValueError("smart 定位必须提供 locator_config")
        if locator_value is not None and str(locator_value).strip() != "":
            raise ValueError("smart 定位不允许提供非空 locator_value")
    else:
        if locator_config is not None:
            raise ValueError("普通定位不允许提供 locator_config")
        if locator_value is None or str(locator_value).strip() == "":
            raise ValueError("普通定位必须提供非空 locator_value")


class ModuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None
    sort_order: int = 0
    # 用例模块树 / 套件模块树；默认 case 保持旧调用方行为不变
    scope: str = Field(default="case", pattern="^(case|suite)$")


class ModuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: int | None = None
    sort_order: int | None = None


class ModulePositionUpdate(BaseModel):
    """拖拽移动：把模块挂到 parent_id 下、排在 before_id 之前。

    `parent_id=None` 表示根层级；`before_id=None` 表示追加到目标父级末尾。
    两个字段都不传等价于“移到根层级末尾”。
    """

    parent_id: int | None = None
    before_id: int | None = None


class ModuleOut(BaseModel):
    id: int
    project_id: int
    parent_id: int | None
    name: str
    sort_order: int
    scope: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ElementCreate(BaseModel):
    project_id: int | None = None
    name: str = Field(min_length=1, max_length=255)
    page_name: str | None = Field(default=None, max_length=255)
    platform: str = Field(default="both", pattern="^(android|ios|both)$")
    # 适用范围：自由文本；不填/空白 → 兜底 all（表示所有）
    scope: str = Field(default="all", max_length=100)
    locator_type: str = Field(pattern=LOCATOR_TYPE_RE)
    locator_value: str | None = None
    locator_config: SmartLocatorConfig | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _validate_locator(self):
        _validate_locator(self.locator_type, self.locator_value, self.locator_config)
        return self

    @field_validator("scope")
    @classmethod
    def normalize_scope(cls, value: str) -> str:
        normalized = value.strip()
        return normalized or "all"

    @field_validator("page_name")
    @classmethod
    def normalize_page_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("page_name")
    @classmethod
    def reject_reserved_page_name(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _normalize_element_page_group_name(value, field="页面名称")


class ElementUpdate(BaseModel):
    project_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    page_name: str | None = Field(default=None, max_length=255)
    platform: str | None = Field(default=None, pattern="^(android|ios|both)$")
    scope: str | None = Field(default=None, max_length=100)
    locator_type: str | None = Field(default=None, pattern=LOCATOR_TYPE_RE)
    # None = 不修改（仅支持设置，不支持清空语义）
    locator_value: str | None = Field(default=None)
    # None = 不修改（仅支持设置，不支持清空语义）
    locator_config: SmartLocatorConfig | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _validate_locator(self):
        if self.locator_type is None:
            # 未提供 locator_type：若只给了 locator_config 则要求显式声明类型，避免半改状态
            if self.locator_config is not None:
                raise ValueError("提供 locator_config 时必须同时显式声明 locator_type='smart'")
            # 普通更新（无 locator_type、无 config）：locator_value 为 None 表示不修改，合法
            if self.locator_value is not None and str(self.locator_value).strip() == "":
                raise ValueError("普通定位必须提供非空 locator_value")
            return self
        _validate_locator(self.locator_type, self.locator_value, self.locator_config)
        return self

    @field_validator("scope")
    @classmethod
    def normalize_scope(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or "all"

    @field_validator("page_name")
    @classmethod
    def normalize_page_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("page_name")
    @classmethod
    def reject_reserved_page_name(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _normalize_element_page_group_name(value, field="页面名称")


class ElementOut(BaseModel):
    id: int
    project_id: int
    project_name: str | None = None
    name: str
    page_name: str | None
    platform: str | None
    scope: str
    locator_type: str
    locator_value: str | None
    locator_config: dict | None = None
    description: str | None
    created_by: int | None = None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ElementUsage(BaseModel):
    case_id: int
    case_name: str
    step_orders: list[int] = Field(default_factory=list)


class ElementPageCount(BaseModel):
    page_name: str
    count: int
    group_id: int | None = None  # 自定义分组 id；元素聚合页为 None
    created_by: int | None = None  # 自定义分组创建者；聚合分组为 None
    parent_id: int | None = None  # 自定义分组父级；聚合分组为 None


class ElementGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    project_id: int | None = None
    parent_id: int | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_element_page_group_name(value, field="分组名称")


class ElementGroupUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_element_page_group_name(value, field="分组名称")


class ElementGroupOut(BaseModel):
    id: int
    name: str
    project_id: int | None = None
    parent_id: int | None = None
    created_by: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ElementPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ElementOut]
