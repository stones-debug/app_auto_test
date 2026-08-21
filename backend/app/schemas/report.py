from datetime import datetime
from typing import Any

from pydantic import BaseModel


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
    status: str
    duration: int | None
    actual_value: str | None
    error_message: str | None
    screenshot: str | None


class ReportAssertionOut(BaseModel):
    id: int
    assertion_type: str
    expected_value: str | None
    actual_value: str | None
    status: str
    error_message: str | None


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


class ReportSummaryOut(BaseModel):
    id: int | None
    total: int
    passed: int
    failed: int
    error_count: int
    skipped: int
    success_rate: float


class ReportDetailOut(BaseModel):
    execution: dict[str, Any]
    report: ReportSummaryOut
    cases: list[ReportCaseOut]
    logs: list[ReportLogOut]
    # Step 8：日志截断元数据（logs_truncated=true 时前端/HTML 展示“仅展示最后 N/M 条”）
    logs_total: int = 0
    logs_truncated: bool = False
