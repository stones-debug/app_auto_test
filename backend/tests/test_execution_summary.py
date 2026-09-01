"""Table-driven tests for database-free execution summary rules."""

from decimal import Decimal
from itertools import permutations

import pytest

from app.services.execution_summary import (
    CaseStatusInput,
    aggregate_statuses,
    compute_counts,
    compute_exclusion_summary,
    merge_case_status,
    merge_execution_status,
    rate_percent,
)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((), "skipped"),
        (("passed",), "passed"),
        (("passed", "skipped"), "skipped"),
        (("stopped", "skipped", "passed"), "stopped"),
        (("failed", "stopped"), "failed"),
        (("error", "failed"), "error"),
        (("passed", "pending"), "error"),
    ],
)
def test_aggregate_statuses_priority_and_unknowns(statuses: tuple[str, ...], expected: str):
    assert aggregate_statuses(statuses) == expected


def test_aggregate_statuses_is_order_independent():
    for ordered in permutations(("passed", "skipped", "stopped", "failed", "error")):
        assert aggregate_statuses(ordered) == "error"


@pytest.mark.parametrize(
    ("agent_status", "stored_statuses", "expected"),
    [
        ("passed", (), "passed"),
        ("passed", ("skipped",), "passed"),
        ("failed", ("error",), "error"),
        ("stopped", ("failed",), "failed"),
        ("cancelled", (), "cancelled"),
    ],
)
def test_merge_execution_status(agent_status: str, stored_statuses: tuple[str, ...], expected: str):
    assert merge_execution_status(agent_status, stored_statuses) == expected


@pytest.mark.parametrize(
    ("case", "terminal_status", "expected_status", "expected_message"),
    [
        (
            CaseStatusInput(status="pending", step_statuses=("failed",)),
            "stopped",
            "failed",
            None,
        ),
        (
            CaseStatusInput(status="running", assertion_statuses=("failed",)),
            "passed",
            "failed",
            None,
        ),
        (CaseStatusInput(status="pending"), "passed", "passed", None),
        (CaseStatusInput(status="pending"), "stopped", "skipped", None),
        (
            CaseStatusInput(status="pending", started=True),
            "stopped",
            "stopped",
            "执行被中断（stopped）",
        ),
        (
            CaseStatusInput(status="running", error_message="已有错误"),
            "error",
            "error",
            "已有错误",
        ),
        (
            CaseStatusInput(status="running", step_statuses=("error",)),
            "passed",
            "error",
            None,
        ),
        (
            CaseStatusInput(status="running", assertion_statuses=("error",)),
            "passed",
            "error",
            None,
        ),
        (CaseStatusInput(status="failed"), "passed", "failed", None),
    ],
)
def test_merge_case_status(
    case: CaseStatusInput,
    terminal_status: str,
    expected_status: str,
    expected_message: str | None,
):
    result = merge_case_status(case, terminal_status)
    assert result.status == expected_status
    assert result.error_message == expected_message


def test_merge_case_status_normalizes_failed_assertion_to_fail():
    result = merge_case_status(
        CaseStatusInput(status="pending", assertion_statuses=("failed",)), "passed"
    )
    assert result.status == "failed"


def test_compute_counts_and_success_rate_exclude_skipped_and_na_from_denominator():
    counts = compute_counts(("passed", "skipped", "failed", "error", "not_applicable"))
    assert counts.total == 5
    assert counts.passed == 1
    assert counts.failed == 1
    assert counts.error_count == 1
    assert counts.skipped == 1
    assert rate_percent(counts.passed, counts.passed + counts.failed + counts.error_count) == Decimal("33.33")
    assert rate_percent(1, 1) == Decimal("100")


def test_rate_percent_zero_denominator_is_zero():
    assert rate_percent(0, 0) == Decimal("0")


def test_compute_exclusion_summary():
    summary = compute_exclusion_summary(
        ("suite", "case", "step", "assertion", "suite_step", "unknown")
    )
    assert summary.as_dict() == {
        "na_suites": 1,
        "na_cases": 1,
        "na_steps": 1,
        "na_assertions": 1,
        "na_suite_steps": 1,
    }
