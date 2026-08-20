from datetime import datetime

from pydantic import BaseModel, Field


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class SuiteOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str | None
    status: str
    created_by: int | None
    case_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SuiteCaseOut(BaseModel):
    id: int  # test_suite_cases.id
    case_id: int
    case_name: str
    module_name: str | None = None
    sort_order: int


class SuiteAddCaseRequest(BaseModel):
    case_id: int


class SuiteReorderRequest(BaseModel):
    order: list[int]  # case_id 列表（按新顺序）


class VariableCreate(BaseModel):
    scope: str = Field(pattern="^(global|project|suite|case)$")
    project_id: int | None = None
    suite_id: int | None = None
    case_id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    value: str = ""
    description: str | None = None


class VariableUpdate(BaseModel):
    value: str | None = None
    description: str | None = None


class VariableOut(BaseModel):
    id: int
    scope: str
    project_id: int | None
    suite_id: int | None
    case_id: int | None
    name: str
    value: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
