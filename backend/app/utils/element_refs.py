"""Shared extraction of element references from executable JSON assets."""

from typing import Any

from app.schemas.generated_case_params import ELEMENT_PARAM_FIELDS

_ELEMENT_PARAM_KEYS = frozenset(
    field for fields in ELEMENT_PARAM_FIELDS.values() for field in fields
)


def collect_element_ids(*values: Any) -> set[int]:
    """Collect direct and parameter element references from nested asset JSON.

    Case JSON normally stores a flat flow, while older clients and profile patches
    may contain nested assertions/parameters.  Walking the complete JSON tree
    keeps deletion checks and asset validation on the same reference semantics.
    """

    element_ids: set[int] = set()

    def add(value: Any) -> None:
        if value is None or isinstance(value, bool):
            return
        try:
            element_ids.add(int(value))
        except (TypeError, ValueError):
            return

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            add(value.get("element_id"))
            node_name = str(value.get("action") or value.get("type") or "")
            params = value.get("params")
            if isinstance(params, dict):
                for field in ELEMENT_PARAM_FIELDS.get(node_name, ()):
                    add(params.get(field))
            for key, child in value.items():
                if key in _ELEMENT_PARAM_KEYS:
                    add(child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
    return element_ids
