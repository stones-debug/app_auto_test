"""Variable definitions and deterministic-per-scope runtime resolution."""

from collections.abc import Mapping
from secrets import SystemRandom
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1
MAX_CHOICE_ITEMS = 100
MAX_CHOICE_ITEM_LENGTH = 1000
MAX_CHOICE_BYTES = 100_000
_rng = SystemRandom()


def validate_definition(kind: str, value: str | None, spec: Mapping[str, Any] | None) -> None:
    if kind not in {"fixed", "random_integer", "random_choice"}:
        raise ValueError("变量类型不支持")
    if kind == "fixed":
        if not isinstance(value, str):
            raise ValueError("固定变量值必须是字符串")
        if spec not in (None, {}):
            raise ValueError("固定变量不能设置 spec")
        return
    if value not in (None, ""):
        raise ValueError("随机变量的 value 必须为空")
    if not isinstance(spec, Mapping):
        raise ValueError("随机变量必须提供 spec")
    if kind == "random_integer":
        assert spec is not None
        keys = set(spec)
        if keys != {"min", "max"}:
            raise ValueError("随机整数 spec 必须包含 min 和 max")
        low, high = spec["min"], spec["max"]
        # bool is an int subclass but is not a valid bound.
        if isinstance(low, bool) or isinstance(high, bool) or not isinstance(low, int) or not isinstance(high, int):
            raise ValueError("随机整数边界必须是安全整数")
        if not (-MAX_SAFE_INTEGER <= low <= MAX_SAFE_INTEGER and -MAX_SAFE_INTEGER <= high <= MAX_SAFE_INTEGER):
            raise ValueError("随机整数边界超出安全范围")
        if low > high:
            raise ValueError("随机整数 min 不能大于 max")
        return
    assert spec is not None
    keys = set(spec)
    items = spec.get("items")
    if keys != {"items"} or not isinstance(items, list) or not 1 <= len(items) <= MAX_CHOICE_ITEMS:
        raise ValueError("随机列表必须包含 1 到 100 个候选项")
    if any(not isinstance(item, str) or not item or len(item) > MAX_CHOICE_ITEM_LENGTH for item in items):
        raise ValueError("随机列表候选项必须是非空且不超过 1000 字符的字符串")
    if len(set(items)) != len(items):
        raise ValueError("随机列表候选项不能重复")
    if sum(len(item.encode("utf-8")) for item in items) > MAX_CHOICE_BYTES:
        raise ValueError("随机列表配置过大")


def resolve_definition(kind: str, value: str | None = "", spec: Mapping[str, Any] | None = None, *, rng: Any | None = None) -> str:
    validate_definition(kind, value, spec)
    source: Any = rng or _rng
    if kind == "fixed":
        return str(value)
    if kind == "random_integer":
        assert spec is not None
        return str(source.randint(int(spec["min"]), int(spec["max"])))
    assert spec is not None
    return str(source.choice(list(spec["items"])))


def resolve_variable(variable: Any, cache: dict[tuple[Any, ...], str], context: tuple[Any, ...], *, rng: Any | None = None) -> str:
    """Resolve once per execution/context; fixed definitions are also normalized to strings."""
    key = (*context, variable.name)
    if key not in cache:
        cache[key] = resolve_definition(variable.kind or "fixed", variable.value, variable.spec, rng=rng)
    return cache[key]


def resolve_rows(
    rows: list[Any], cache: dict[tuple[Any, ...], str], context: tuple[Any, ...], *,
    skip_names: set[str] | frozenset[str] = frozenset(), rng: Any | None = None,
) -> dict[str, str]:
    """Resolve definitions not shadowed by a higher-priority layer."""
    return {
        row.name: resolve_variable(row, cache, context, rng=rng)
        for row in rows if row.name not in skip_names
    }
