from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    module_id: int | None = None
    description: str | None = None
    status: str = Field(default="draft", pattern="^(draft|active|disabled)$")
    steps: list[dict[str, Any]] = Field(default_factory=list)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    module_id: int | None = None
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|active|disabled)$")
    steps: list[dict[str, Any]] | None = None
    assertions: list[dict[str, Any]] | None = None
    variables: dict[str, Any] | None = None


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
