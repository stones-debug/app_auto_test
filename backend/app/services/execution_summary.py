"""Execution terminal-state and report-summary calculations.

This module deliberately contains no ORM or I/O dependencies.  Database-facing
code in ``worker_service`` supplies immutable values and applies the returned
results to ORM rows.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

_STATUS_PRIORITY = ("error", "failed", "stopped", "skipped", "passed")
_STORED_EXECUTION_TERMINAL_STATUSES = {"error", "failed", "stopped"}
_EXCLUSION_KEYS = ("na_suites", "na_cases", "na_steps", "na_assertions", "na_suite_steps")


@dataclass(frozen=True)
class CaseStatusInput:
    """Immutable execution-case state used by :func:`merge_case_status`."""

    status: str
    started: bool = False
    step_statuses: tuple[str, ...] = ()
    assertion_statuses: tuple[str, ...] = ()
    error_message: str | None = None


@dataclass(frozen=True)
class CaseMergeResult:
    status: str
    error_message: str | None = None


@dataclass(frozen=True)
class Counts:
    total: int
    passed: int
    failed: int
    error_count: int
    skipped: int


@dataclass(frozen=True)
class ExclusionSummary:
    na_suites: int = 0
    na_cases: int = 0
    na_steps: int = 0
    na_assertions: int = 0
    na_suite_steps: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "na_suites": self.na_suites,
            "na_cases": self.na_cases,
            "na_steps": self.na_steps,
            "na_assertions": self.na_assertions,
            "na_suite_steps": self.na_suite_steps,
        }


def aggregate_statuses(statuses: Iterable[str]) -> str:
    """Aggregate statuses using ``error > failed > stopped > skipped > passed``.

    Empty input is ``skipped``.  Any status outside the known set fails closed
    to ``error`` instead of silently becoming a successful result.
    """
    remaining = set(statuses)
    for state in _STATUS_PRIORITY[:-1]:
        if state in remaining:
            return state
    if remaining == {"passed"}:
        return "passed"
    return "error" if remaining else "skipped"


def merge_execution_status(agent_status: str, stored_statuses: Iterable[str]) -> str:
    """Merge an Agent terminal status with higher-priority stored child states.

    Execution has no persisted ``skipped`` terminal state.  Skipped child
    nodes therefore do not override an Agent ``passed`` result, while stored
    error/failed/stopped nodes always do.  ``cancelled`` remains a top-level
    execution status but participates in priority as ``stopped``.
    """
    initial = "stopped" if agent_status == "cancelled" else agent_status
    candidates = [initial, *(
        status for status in stored_statuses if status in _STORED_EXECUTION_TERMINAL_STATUSES
    )]
    merged = aggregate_statuses(candidates)
    return "cancelled" if agent_status == "cancelled" and merged == "stopped" else merged


def merge_case_status(case: CaseStatusInput, terminal_status: str) -> CaseMergeResult:
    """Resolve one pending/running case without mutating database state."""
    if case.status not in ("pending", "running"):
        return CaseMergeResult(case.status, case.error_message)

    assertion_statuses = {
        "fail" if status in ("fail", "failed") else status
        for status in case.assertion_statuses
    }
    failed = "failed" in case.step_statuses or "fail" in assertion_statuses
    if failed:
        return CaseMergeResult("failed", case.error_message)
    if terminal_status == "passed":
        return CaseMergeResult("passed", case.error_message)

    started = (
        case.started
        or case.status == "running"
        or any(status != "pending" for status in case.step_statuses)
        or any(status != "pending" for status in assertion_statuses)
    )
    if not started:
        return CaseMergeResult("skipped", case.error_message)

    status = "stopped" if terminal_status in ("stopped", "cancelled") else "error"
    error_message = case.error_message or f"执行被中断（{terminal_status}）"
    return CaseMergeResult(status, error_message)


def compute_counts(statuses: Iterable[str]) -> Counts:
    """Count report statuses without treating unknown values as successful."""
    values = tuple(statuses)
    return Counts(
        total=len(values),
        passed=sum(status == "passed" for status in values),
        failed=sum(status == "failed" for status in values),
        error_count=sum(status == "error" for status in values),
        skipped=sum(status == "skipped" for status in values),
    )


def rate_percent(numerator: int, denominator: int) -> Decimal:
    """Return a two-decimal percentage; a zero denominator returns ``0``."""
    if denominator:
        return Decimal(str(round(numerator / denominator * 100, 2)))
    return Decimal("0")


def compute_exclusion_summary(target_types: Iterable[str]) -> ExclusionSummary:
    """Count execution exclusions by their target type."""
    counts = {key: 0 for key in _EXCLUSION_KEYS}
    for target_type in target_types:
        key = f"na_{target_type}s"
        if key in counts:
            counts[key] += 1
    return ExclusionSummary(**counts)
