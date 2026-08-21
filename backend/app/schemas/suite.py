from datetime import datetime

from pydantic import BaseModel, Field, model_validator


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


class SuitePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SuiteOut]


class SuiteCaseOut(BaseModel):
    id: int  # test_suite_cases.id
    case_id: int
    case_name: str
    module_name: str | None = None
    sort_order: int


class SuiteAddCaseRequest(BaseModel):
    case_ids: list[int] | None = Field(default=None, min_length=1)
    case_id: int | None = None

    @model_validator(mode="after")
    def _normalize_case_ids(self):
        ids = self.case_ids or ([] if self.case_id is None else [self.case_id])
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise ValueError("case_ids 不能为空")
        self.case_ids = ids
        return self


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

    @model_validator(mode="after")
    def _validate_scope_fk(self):
        if self.scope == "project" and self.project_id is None:
            raise ValueError("project scope 必须提供 project_id")
        if self.scope == "suite" and self.suite_id is None:
            raise ValueError("suite scope 必须提供 suite_id")
        if self.scope == "case" and self.case_id is None:
            raise ValueError("case scope 必须提供 case_id")
        # 归属项目的解析与 global 外键清空由 API 层完成（CR-02）
        return self


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
