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
    name: str
    page_name: str | None
    platform: str | None
    locator_type: str
    locator_value: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ElementUsage(BaseModel):
    case_id: int
    case_name: str


class ElementPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ElementOut]
