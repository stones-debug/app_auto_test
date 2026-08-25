from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

RUN_OPTION_KEYS = ("use_pre_steps", "use_post_steps", "attach_to_current_app")


def _validate_run_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    for key in RUN_OPTION_KEYS:
        if key in parameters and not isinstance(parameters[key], bool):
            raise ValueError(f"{key} 必须是布尔值")
    return parameters


class ExecutionCreate(BaseModel):
    device_id: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, ge=60, le=7200)
    # 方案 §4.8：执行创建必填档案/版本与双 revision（required 语义）
    app_profile_id: int | None = None
    app_release_id: int | None = None
    expected_profile_revision: int | None = Field(default=None, ge=1)
    expected_test_asset_revision: int | None = Field(default=None, ge=1)

    _run_parameters = field_validator("parameters")(_validate_run_parameters)


class BatchExecutionCreate(BaseModel):
    suite_ids: list[int] = Field(min_length=1)
    device_id: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, ge=60, le=7200)
    app_profile_id: int | None = None
    app_release_id: int | None = None
    expected_profile_revision: int | None = Field(default=None, ge=1)
    expected_test_asset_revision: int | None = Field(default=None, ge=1)

    _run_parameters = field_validator("parameters")(_validate_run_parameters)


class ExecutionRetryRequest(BaseModel):
    device_id: int
    timeout_seconds: int | None = Field(default=None, ge=60, le=7200)


# ---------- 执行预检（方案 §4.7） ----------


class ExecutionPreviewTarget(BaseModel):
    type: str = Field(pattern="^(case|suite|batch)$")
    ids: list[int] = Field(min_length=1)


class ExecutionPreviewRequest(BaseModel):
    project_id: int
    target: ExecutionPreviewTarget
    app_profile_id: int
    app_release_id: int | None = None
    device_id: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, ge=60, le=7200)

    _run_parameters = field_validator("parameters")(_validate_run_parameters)


class ExecutionPreviewResponse(BaseModel):
    profile_revision: int
    test_asset_revision: int
    profile: dict[str, Any]
    release: dict[str, Any]
    counts: dict[str, int]
    exclusion_preview: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


class ExecutionCaseOut(BaseModel):
    id: int
    case_id: int
    case_name: str
    module_name: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    duration: int | None
    error_message: str | None
    steps: list["ExecutionStepOut"] = Field(default_factory=list)
    assertions: list["ExecutionAssertionOut"] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ExecutionStepOut(BaseModel):
    id: int
    step_order: int
    action: str
    phase: str = "main"
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: str
    duration: int | None
    actual_value: str | None
    error_message: str | None
    artifact_id: int | None = None

    model_config = {"from_attributes": True}


class ExecutionAssertionOut(BaseModel):
    id: int
    assertion_type: str
    expected_value: str | None
    actual_value: str | None
    status: str
    error_message: str | None

    model_config = {"from_attributes": True}


class ExecutionOut(BaseModel):
    id: int
    project_id: int
    type: str
    suite_id: int | None
    case_id: int | None
    device_id: int | None
    status: str
    parameters: dict[str, Any]
    timeout_seconds: int
    started_at: datetime | None
    finished_at: datetime | None
    stop_requested_at: datetime | None
    finalized_at: datetime | None
    duration: int | None
    created_by: int | None
    retry_of: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ExecutionDetail(ExecutionOut):
    project_name: str | None = None
    device_name: str | None = None
    created_by_name: str | None = None
    cases: list[ExecutionCaseOut] = Field(default_factory=list)


class ExecutionListItem(BaseModel):
    id: int
    project_id: int
    type: str
    suite_id: int | None
    case_id: int | None
    device_id: int | None
    status: str
    timeout_seconds: int
    started_at: datetime | None
    finished_at: datetime | None
    stop_requested_at: datetime | None
    finalized_at: datetime | None
    duration: int | None
    retry_of: int | None
    created_at: datetime
    case_name: str | None = None
    suite_name: str | None = None
    project_name: str | None = None
    device_name: str | None = None
    created_by_name: str | None = None

    model_config = {"from_attributes": True}


class ExecutionPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ExecutionListItem]


class ExecutionLogOut(BaseModel):
    id: int
    execution_id: int
    level: str
    message: str
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ExecutionLogPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ExecutionLogOut]
