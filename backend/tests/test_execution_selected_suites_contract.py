"""APP 档案 profile_all 套件选择协议的无数据库契约测试。"""

import pytest
from pydantic import ValidationError

from app.schemas.execution import BatchExecutionCreate, ExecutionPreviewTarget
from app.services.execution_prepare import canonical_target, request_hash


def test_profile_all_excluded_suite_ids_are_a_sorted_set_and_target_ids_stay_empty():
    body = BatchExecutionCreate(
        target_scope="profile_all",
        excluded_suite_ids=[19, 4, 19, 7],
        app_profile_id=3,
        app_release_id=5,
        expected_profile_revision=2,
        expected_test_asset_revision=8,
    )
    assert body.suite_ids == []
    assert body.excluded_suite_ids == [4, 7, 19]

    target = ExecutionPreviewTarget(
        type="batch",
        target_scope="profile_all",
        excluded_suite_ids=[19, 4, 19, 7],
    )
    assert target.ids == []
    assert target.excluded_suite_ids == [4, 7, 19]


def test_explicit_targets_cannot_carry_profile_all_exclusions():
    with pytest.raises(ValidationError):
        BatchExecutionCreate(suite_ids=[11], excluded_suite_ids=[12])
    with pytest.raises(ValidationError):
        BatchExecutionCreate(target_scope="profile_all", parameters={"suite_ids": [11]})
    with pytest.raises(ValidationError):
        ExecutionPreviewTarget(type="batch", ids=[11], excluded_suite_ids=[12])


def test_prepare_canonical_target_captures_final_source_and_exclusions():
    target = canonical_target(
        target_type="batch",
        target_ids=[],
        target_scope="profile_all",
        context_suite_id=None,
        resolved_ids=[31, 12, 31],
        excluded_suite_ids=[9, 3, 9],
    )
    assert target == {
        "type": "batch",
        "ids": [31, 12],
        "target_scope": "profile_all",
        "context_suite_id": None,
        "excluded_suite_ids": [3, 9],
    }

    reordered = canonical_target(
        target_type="batch",
        target_ids=[],
        target_scope="profile_all",
        context_suite_id=None,
        resolved_ids=[31, 12, 31],
        excluded_suite_ids=[3, 9],
    )
    assert request_hash(target=target, parameters={"use_pre_steps": False}) == request_hash(
        target=reordered, parameters={"use_pre_steps": False}
    )
