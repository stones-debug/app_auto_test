from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.case import StepCreate


def _normalize_suite_steps(value):
    """兼容通用步骤编辑器提交的空 assertions 元数据。"""
    if value is None:
        return value
    normalized = []
    for step in value:
        if isinstance(step, dict) and "assertions" in step:
            if step["assertions"]:
                raise ValueError("套件前后置步骤不支持断言")
            step = {key: item for key, item in step.items() if key != "assertions"}
        normalized.append(step)
    return normalized


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    # 套件模块树（scope='suite'）的模块；为空表示未分组
    module_id: int | None = None
    # 方案 §2：套件前后置步骤（仅 Action，无断言）；复用 StepCreate 校验与 UUID 兜底
    setup_steps: list[StepCreate] = Field(default_factory=list)
    teardown_steps: list[StepCreate] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_step_payload(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        for field in ("setup_steps", "teardown_steps"):
            if field in values:
                values[field] = _normalize_suite_steps(values[field])
        return values


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    module_id: int | None = None
    setup_steps: list[StepCreate] | None = None
    teardown_steps: list[StepCreate] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_step_payload(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        for field in ("setup_steps", "teardown_steps"):
            if field in values:
                values[field] = _normalize_suite_steps(values[field])
        return values


class SuiteOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str | None
    status: str
    module_id: int | None = None
    module_name: str | None = None
    created_by: int | None
    case_count: int = 0
    setup_steps: list[dict] = Field(default_factory=list)
    teardown_steps: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SuitePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SuiteOut]


class SuiteCaseVariablePreview(BaseModel):
    """用例行内变量摘要（最多 2 项）。"""

    name: str
    display_value: str
    # occurrence=编排项覆盖 / 具体作用域名=继承 / random / undefined
    source: str
    # overridden / inherited / undefined / random
    status: str
    reference_count: int


class SuiteCaseOut(BaseModel):
    id: int  # test_suite_cases.id（编排项 / suite_case_id）
    case_id: int
    case_name: str
    module_name: str | None = None
    sort_order: int
    variable_count: int = 0
    variables_preview: list[SuiteCaseVariablePreview] = Field(default_factory=list)


class SuiteCaseVariableItem(BaseModel):
    name: str
    reference_count: int
    status: str
    display_value: str
    inherited_value: str | None = None
    inherited_scope: str | None = None
    override_enabled: bool
    override_value: str


class SuiteCaseVariablesOut(BaseModel):
    suite_id: int
    membership_id: int
    case_id: int
    case_name: str
    test_asset_revision: int
    total: int
    variables: list[SuiteCaseVariableItem]


class SuiteCaseVariableOverrideRequest(BaseModel):
    """增量覆盖：字符串=设置编排项覆盖，null=删除覆盖并恢复继承。"""

    updates: dict[str, str | None] = Field(default_factory=dict)


class SuiteAddCaseRequest(BaseModel):
    case_ids: list[int] | None = Field(default=None, min_length=1)
    case_id: int | None = None

    @model_validator(mode="after")
    def _normalize_case_ids(self):
        ids = self.case_ids or ([] if self.case_id is None else [self.case_id])
        if not ids:
            raise ValueError("case_ids 不能为空")
        self.case_ids = ids
        return self


class SuiteReorderRequest(BaseModel):
    membership_ids: list[int] = Field(min_length=1)  # test_suite_cases.id 列表（按新顺序）


class VariableCreate(BaseModel):
    scope: str = Field(pattern="^(global|project|suite|case)$")
    project_id: int | None = None
    suite_id: int | None = None
    case_id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    value: str = ""
    kind: str = Field(default="fixed", pattern="^(fixed|random_integer|random_choice)$")
    spec: dict | None = None
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

    @model_validator(mode="after")
    def _validate_definition(self):
        from app.services.random_variables import validate_definition
        validate_definition(self.kind, self.value, self.spec)
        return self


class VariableUpdate(BaseModel):
    value: str | None = None
    kind: str | None = Field(default=None, pattern="^(fixed|random_integer|random_choice)$")
    spec: dict | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _reject_null_kind(self):
        if "kind" in self.model_fields_set and self.kind is None:
            raise ValueError("kind 不能为 null")
        return self


class VariableOut(BaseModel):
    id: int
    scope: str
    project_id: int | None
    suite_id: int | None
    case_id: int | None
    name: str
    value: str
    kind: str
    spec: dict | None
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
