from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    visibility: str = Field(default="private", pattern="^(private|public)$")


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    visibility: str | None = Field(default=None, pattern="^(private|public)$")


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str | None
    owner_id: int
    visibility: str
    status: str
    role: str | None = None  # 当前用户在项目中的角色
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectMemberCreate(BaseModel):
    user_id: int
    role: str = Field(default="member", pattern="^(admin|member|viewer)$")


class ProjectMemberOut(BaseModel):
    id: int
    project_id: int
    user_id: int
    role: str
    username: str | None = None

    model_config = {"from_attributes": True}


class PageResult(BaseModel):
    total: int
    items: list[ProjectOut]
