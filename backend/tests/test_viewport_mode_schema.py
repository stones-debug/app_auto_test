import pytest
from pydantic import ValidationError

from app.schemas.case import ActionNodeCreate


def _node(params: dict):
    return ActionNodeCreate(
        order=1,
        action="swipe_in_element_find_text_click",
        element_id=1,
        params={"target_text": "目标", **params},
    )


def test_swipe_find_text_has_no_viewport_configuration_fields():
    node = _node({})
    assert "viewport_mode" not in node.params
    assert "viewport_element_id" not in node.params
    assert node.params["duration_ms"] == 300


@pytest.mark.parametrize("field", ["viewport_mode", "viewport_element_id"])
def test_removed_viewport_fields_are_rejected(field):
    with pytest.raises(ValidationError):
        _node({field: "parent" if field == "viewport_mode" else 9})


@pytest.mark.parametrize("duration", [0, -1, 10001])
def test_duration_ms_must_be_positive_and_bounded(duration):
    with pytest.raises(ValidationError):
        _node({"duration_ms": duration})
