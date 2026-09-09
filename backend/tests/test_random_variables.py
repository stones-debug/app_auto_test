from types import SimpleNamespace

import pytest

from app.schemas.suite import VariableCreate, VariableUpdate
from app.services.random_variables import resolve_definition, resolve_rows, validate_definition


class FakeRandom:
    def __init__(self) -> None:
        self.calls = 0

    def randint(self, low: int, high: int) -> int:
        self.calls += 1
        return low

    def choice(self, values: list[str]) -> str:
        self.calls += 1
        return values[-1]


def test_random_integer_is_closed_and_stringified() -> None:
    rng = FakeRandom()
    assert resolve_definition("random_integer", "", {"min": -2, "max": 3}, rng=rng) == "-2"
    assert rng.calls == 1


def test_choice_validation_and_resolution() -> None:
    rng = FakeRandom()
    validate_definition("random_choice", "", {"items": ["a", "b"]})
    assert resolve_definition("random_choice", "", {"items": ["a", "b"]}, rng=rng) == "b"
    with pytest.raises(ValueError):
        validate_definition("random_choice", "", {"items": ["a", "a"]})


def test_shadowed_definition_does_not_consume_random_source() -> None:
    rng = FakeRandom()
    variable = SimpleNamespace(name="token", kind="random_integer", value="", spec={"min": 1, "max": 9})
    assert resolve_rows([variable], {}, ("project",), skip_names={"token"}, rng=rng) == {}
    assert rng.calls == 0


def test_resolution_cache_stabilizes_value_for_context() -> None:
    rng = FakeRandom()
    variable = SimpleNamespace(name="token", kind="random_choice", value="", spec={"items": ["a", "b"]})
    cache: dict[tuple[object, ...], str] = {}
    assert resolve_rows([variable], cache, ("suite", 1), rng=rng) == {"token": "b"}
    assert resolve_rows([variable], cache, ("suite", 1), rng=rng) == {"token": "b"}
    assert rng.calls == 1


def test_case_occurrences_have_independent_random_contexts() -> None:
    rng = FakeRandom()
    variable = SimpleNamespace(name="token", kind="random_integer", value="", spec={"min": 1, "max": 9})
    cache: dict[tuple[object, ...], str] = {}
    first = resolve_rows([variable], cache, ("case", 10, 20, 1), rng=rng)
    second = resolve_rows([variable], cache, ("case", 10, 20, 2), rng=rng)
    assert first == second == {"token": "1"}
    assert rng.calls == 2


def test_fixed_empty_string_is_a_valid_definition() -> None:
    assert VariableCreate(scope="global", name="empty", value="").value == ""
    assert resolve_definition("fixed", "") == ""


@pytest.mark.parametrize(
    "kind,spec",
    [
        ("random_integer", {"min": 2, "max": 1}),
        ("random_integer", {"min": True, "max": 2}),
        ("random_integer", {"min": 0, "max": 2**53}),
        ("random_integer", {"min": 0}),
        ("random_choice", {"items": []}),
        ("random_choice", {"items": ["x", "x"]}),
        ("random_choice", {"items": ["x" * 1001]}),
        ("random_choice", {"items": ["中" * 1000] * 100}),
    ],
)
def test_invalid_random_definition_boundaries(kind: str, spec: dict) -> None:
    with pytest.raises(ValueError):
        validate_definition(kind, "", spec)


def test_partial_update_models_expose_definition_fields() -> None:
    body = VariableUpdate(kind="random_integer", spec={"min": -1, "max": 1})
    assert body.model_fields_set == {"kind", "spec"}
    with pytest.raises(ValueError):
        VariableUpdate(kind=None)
