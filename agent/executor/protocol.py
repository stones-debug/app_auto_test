"""Agent 侧 Protocol（Step 10：manifest 单一来源 + 类型门禁）。"""

from pathlib import Path

import yaml

_MANIFEST = Path(__file__).resolve().parent / "protocol_manifest.yaml"


def load_manifest() -> dict:
    with _MANIFEST.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def protocol_version() -> str:
    return str(load_manifest().get("protocol_version", "0.0.0"))


def verify_registry_matches_manifest() -> None:
    """启动断言：decorator 注册集合与 manifest 完全一致（多/少都拒绝）。"""
    from .actions import ACTION_REGISTRY
    from .assertions import ASSERTION_REGISTRY

    manifest = load_manifest()
    action_names = {a["name"] for a in manifest.get("actions", [])}
    assertion_names = {a["name"] for a in manifest.get("assertions", [])}

    registered_actions = set(ACTION_REGISTRY)
    registered_assertions = set(ASSERTION_REGISTRY)

    problems: list[str] = []
    missing_actions = action_names - registered_actions
    extra_actions = registered_actions - action_names
    missing_assertions = assertion_names - registered_assertions
    extra_assertions = registered_assertions - assertion_names
    if missing_actions:
        problems.append(f"manifest 声明但未注册的动作: {sorted(missing_actions)}")
    if extra_actions:
        problems.append(f"已注册但 manifest 未声明的动作: {sorted(extra_actions)}")
    if missing_assertions:
        problems.append(f"manifest 声明但未注册的断言: {sorted(missing_assertions)}")
    if extra_assertions:
        problems.append(f"已注册但 manifest 未声明的断言: {sorted(extra_assertions)}")
    if problems:
        raise RuntimeError("Action/Assertion Registry 与 protocol_manifest.yaml 不一致：" + "；".join(problems))
