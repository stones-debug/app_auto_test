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
    case_count: int = 0
    element_count: int = 0
    suite_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectMemberCreate(BaseModel):
    user_id: int
    role: str = Field(default="member", pattern="^(admin|member|viewer)$")


class ProjectMemberUpdate(BaseModel):
    role: str = Field(pattern="^(admin|member|viewer)$")


class ProjectMemberOut(BaseModel):
    membership_id: int | None = None  # owner 为虚拟行时为 None
    user_id: int
    username: str | None = None
    role: str

    model_config = {"from_attributes": True}


class UserCandidateOut(BaseModel):
    id: int
    username: str
    email: str | None = None


class PageResult(BaseModel):
    total: int
    page: int = 1
    page_size: int = 20
    items: list[ProjectOut]
