from typing import Any

from pydantic import BaseModel


class DashboardStats(BaseModel):
    project_count: int = 0
    period_execution_count: int = 0
    active_execution_count: int = 0
    success_rate: float = 0
    available_device_count: int = 0
    device_count: int = 0


class DashboardOverviewOut(BaseModel):
    range: str
    generated_at: str
    stats: DashboardStats
    status_counts: dict[str, int] = {}
    trend: list[dict[str, Any]] = []
    recent_executions: list[dict[str, Any]] = []
