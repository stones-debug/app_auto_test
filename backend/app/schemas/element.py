from datetime import datetime

from pydantic import BaseModel, Field


class ModuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None
    sort_order: int = 0


class ModuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: int | None = None
    sort_order: int | None = None


class ModuleOut(BaseModel):
    id: int
    project_id: int
    parent_id: int | None
    name: str
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ElementCreate(BaseModel):
    project_id: int | None = None
    name: str = Field(min_length=1, max_length=255)
    page_name: str | None = None
    platform: str = Field(default="both", pattern="^(android|ios|both)$")
    locator_type: str = Field(
        pattern="^(id|xpath|accessibility_id|class_name|uiautomator|predicate|coordinate|custom)$"
    )
    locator_value: str = Field(min_length=1)
    description: str | None = None


class ElementUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    page_name: str | None = None
    platform: str | None = Field(default=None, pattern="^(android|ios|both)$")
    locator_type: str | None = Field(
        default=None,
        pattern="^(id|xpath|accessibility_id|class_name|uiautomator|predicate|coordinate|custom)$",
    )
    locator_value: str | None = Field(default=None, min_length=1)
    description: str | None = None


class ElementOut(BaseModel):
    id: int
    project_id: int
    project_name: str | None = None
    name: str
    page_name: str | None
    platform: str | None
    locator_type: str
    locator_value: str
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


class ElementGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ElementGroupOut(BaseModel):
    id: int
    name: str
    created_by: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ElementPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ElementOut]
