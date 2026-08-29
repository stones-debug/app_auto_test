"""终态聚合纯函数（Step 7.1：Agent 与后端统一优先级口径）。

聚合优先级：error > failed > stopped > skipped > passed。
- 空集合返回 skipped（无子节点结果不视为通过）；
- 未知状态不允许静默视为 passed（fail-closed 归为 error）；
- cancelled 在聚合边界归一为 stopped（子节点终态无 cancelled，执行级才有）。
"""

from collections.abc import Iterable

# 执行级 cancelled 在子节点聚合中按 stopped 参与优先级比较
_NORMALIZE = {"cancelled": "stopped"}
_TERMINAL_PRIORITY = ("error", "failed", "stopped", "skipped", "passed")


def aggregate_statuses(statuses: Iterable[str]) -> str:
    """按统一优先级聚合子节点终态；结果与输入顺序无关。"""
    remaining: set[str] = set()
    for status in statuses:
        normalized = str(status).lower()
        remaining.add(_NORMALIZE.get(normalized, normalized))
    for state in _TERMINAL_PRIORITY[:4]:
        if state in remaining:
            return state
    if remaining == {"passed"}:
        return "passed"
    # 未知状态不能当作通过（Step 7.3：禁止未知状态直接返回 passed）
    return "error" if remaining else "skipped"
