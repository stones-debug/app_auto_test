import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.execution import (
    BatchExecutionCreate,
    ExecutionCreate,
    ExecutionPreviewRequest,
    ExecutionPreviewTarget,
    ExecutionRetryRequest,
)


def _requests() -> list[tuple[type, dict]]:
    return [
        (ExecutionCreate, {}),
        (BatchExecutionCreate, {"suite_ids": [1]}),
        (ExecutionRetryRequest, {"device_id": 1}),
        (
            ExecutionPreviewRequest,
            {
                "project_id": 1,
                "target": ExecutionPreviewTarget(type="case", ids=[1]).model_dump(),
                "app_profile_id": 1,
                "app_release_id": 1,
            },
        ),
    ]


@pytest.mark.parametrize("model, base", _requests())
def test_execution_timeout_accepts_new_24_hour_ceiling(model: type, base: dict):
    value = model(timeout_seconds=86400, **base)
    assert value.timeout_seconds == 86400


@pytest.mark.parametrize("model, base", _requests())
@pytest.mark.parametrize("value", [59, 86401])
def test_execution_timeout_rejects_outside_configured_range(
    model: type, base: dict, value: int
):
    with pytest.raises(ValidationError):
        model(timeout_seconds=value, **base)


@pytest.mark.parametrize("model, base", _requests())
def test_execution_timeout_uses_runtime_configured_ceiling(
    monkeypatch: pytest.MonkeyPatch, model: type, base: dict
):
    monkeypatch.setattr(settings, "max_execution_timeout", 120)

    assert model(timeout_seconds=120, **base).timeout_seconds == 120
    with pytest.raises(ValidationError):
        model(timeout_seconds=121, **base)
