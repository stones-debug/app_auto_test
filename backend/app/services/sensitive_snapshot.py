"""Mask sensitive execution snapshot leaves for user-facing projections."""

from copy import deepcopy
from typing import Any

REDACTED = "<redacted>"


def mask_execution_parameters(parameters: Any, sensitive_names: Any) -> Any:
    """Mask only exact names in the execution request's variables map."""
    if not isinstance(parameters, dict) or not isinstance(sensitive_names, list):
        return parameters
    variables = parameters.get("variables")
    if not isinstance(variables, dict):
        return parameters
    result = deepcopy(parameters)
    result["variables"] = {
        key: REDACTED if key in sensitive_names else value
        for key, value in variables.items()
    }
    return result


def _relative_path(path: object) -> list[str] | None:
    if not isinstance(path, str):
        return None
    parts = path.split(".")
    if parts and parts[0] in {"params", "parameters"}:
        parts = parts[1:]
    return parts or None


def mask_sensitive_parameters(parameters: Any, paths: Any) -> Any:
    """Mask only the leaves recorded by the resolver, preserving shape."""
    if not isinstance(parameters, (dict, list)) or not isinstance(paths, list):
        return parameters
    result = deepcopy(parameters)
    for path in paths:
        parts = _relative_path(path)
        if not parts:
            continue
        current: Any = result
        for part in parts[:-1]:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                current = current[index] if index < len(current) else None
            else:
                current = None
            if current is None:
                break
        else:
            leaf = parts[-1]
            if isinstance(current, dict) and leaf in current:
                current[leaf] = REDACTED
            elif isinstance(current, list) and leaf.isdigit():
                index = int(leaf)
                if index < len(current):
                    current[index] = REDACTED
    return result


def sensitive_result(paths: Any) -> bool:
    """Whether an assertion's expected value is sourced from a sensitive leaf."""
    return isinstance(paths, list) and any(
        isinstance(path, str)
        and path.split(".")[-1] in {"expected", "expected_value"}
        for path in paths
    )
