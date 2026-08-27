from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ReportListItem(BaseModel):
    id: int
    execution_id: int
    total: int
    passed: int
    failed: int
    error_count: int
    skipped: int
    success_rate: float
    duration: int | None
    has_report: bool = False
    created_at: datetime
    # 执行维度
    project_id: int | None = None
    project_name: str | None = None
    device_name: str | None = None
    execution_status: str | None = None
    execution_type: str | None = None
    case_name: str | None = None
    suite_name: str | None = None
    finished_at: datetime | None = None
    # 方案 §7.3：档案/版本 + N/A 数量
    app_profile_name: str | None = None
    app_release_version: str | None = None
    not_applicable: int = 0

    model_config = {"from_attributes": True}


class ReportPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ReportListItem]


class ReportStepOut(BaseModel):
    id: int
    step_order: int
    action: str
    phase: str = "main"
    parameters: dict[str, Any]
    status: str
    duration: int | None
    actual_value: str | None
    error_message: str | None
    screenshot: str | None


class ReportAssertionOut(BaseModel):
    id: int
    assertion_order: int | None = None
    assertion_type: str
    expected_value: str | None
    actual_value: str | None
    status: str
    error_message: str | None
    # 快照携带的说明/参数（断言行本身不落库，从 assertions_snapshot 补）
    params: dict[str, Any] | None = None
    description: str | None = None


class ReportCaseOut(BaseModel):
    id: int
    case_id: int
    case_name: str
    module_name: str | None
    status: str
    duration: int | None
    error_message: str | None
    elements: dict[str, Any]
    steps: list[ReportStepOut]
    assertions: list[ReportAssertionOut]


class ReportLogOut(BaseModel):
    id: int
    level: str
    message: str
    source: str
    created_at: str | None


class ReportSuiteOut(BaseModel):
    id: int
    suite_id: int | None
    suite_name: str
    suite_order: int
    status: str
    duration: int | None
    error_message: str | None
    setup_steps: list[ReportStepOut]
    cases: list[ReportCaseOut]
    teardown_steps: list[ReportStepOut]


class ReportSummaryOut(BaseModel):
    id: int | None
    total: int
    passed: int
    failed: int
    error_count: int
    skipped: int
    success_rate: float
    not_applicable: int = 0
    exclusion_summary: dict[str, int] = Field(default_factory=dict)
    # 方案 §2：套件/步骤三层统计 + N/A 套件
    suite_total: int = 0
    suite_passed: int = 0
    suite_failed: int = 0
    suite_error_count: int = 0
    suite_skipped: int = 0
    suite_success_rate: float = 0
    step_total: int = 0
    step_passed: int = 0
    step_failed: int = 0
    step_error_count: int = 0
    step_skipped: int = 0
    step_success_rate: float = 0
    not_applicable_suites: int = 0


class ReportExclusionOut(BaseModel):
    target_type: str
    path: str
    reason_code: str
    reason_note: str | None
    source_type: str
    node_key: str | None = None


class ReportDetailOut(BaseModel):
    execution: dict[str, Any]
    report: ReportSummaryOut
    suites: list[ReportSuiteOut]
    cases: list[ReportCaseOut] = Field(default_factory=list)
    logs: list[ReportLogOut]
    # 方案 §7.2：不适用内容清单
    exclusions: list[ReportExclusionOut] = Field(default_factory=list)
    # Step 8：日志截断元数据（logs_truncated=true 时前端/HTML 展示“仅展示最后 N/M 条”）
    logs_total: int = 0
    logs_truncated: bool = False
