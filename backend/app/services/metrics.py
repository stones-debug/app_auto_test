"""轻量指标采集（方案 §10.5，无第三方依赖）。

Prometheus text 格式的简化实现，覆盖核心指标：
- profile_resolve_total{result=success|empty|invalid|error}
- profile_revision_conflicts_total{type=profile|asset}
- profile_rules_total{target_type}
- execution_na_ratio、execution_snapshot_bytes
- execution_snapshot_create_duration_seconds
单进程内存计数；多进程部署时应迁移至 prometheus-client + Redis/Pushgateway。
"""

import threading
from collections import defaultdict

_lock = threading.Lock()
_resolve_total: dict[str, int] = defaultdict(int)
_conflicts_total: dict[str, int] = defaultdict(int)
_rules_total: dict[str, int] = defaultdict(int)
_snapshot_bytes: list[int] = []
_snapshot_duration_ms: list[float] = []


def inc_resolve(result: str) -> None:
    with _lock:
        _resolve_total[result] += 1


def inc_conflict(type_: str) -> None:
    with _lock:
        _conflicts_total[type_] += 1


def inc_rule(target_type: str) -> None:
    with _lock:
        _rules_total[target_type] += 1


def observe_snapshot(bytes_: int, duration_ms: float) -> None:
    with _lock:
        _snapshot_bytes.append(bytes_)
        _snapshot_duration_ms.append(duration_ms)
        if len(_snapshot_bytes) > 1000:
            _snapshot_bytes.pop(0)
            _snapshot_duration_ms.pop(0)


def render_metrics() -> str:
    with _lock:
        lines = ["# TYPE profile_resolve_total counter", "# HELP profile_resolve_total 解析结果计数(观察数)"]
        for result, n in sorted(_resolve_total.items()):
            lines.append(f'profile_resolve_total{{result="{result}"}} {n}')
        lines.append("# TYPE profile_revision_conflicts_total counter")
        for type_, n in sorted(_conflicts_total.items()):
            lines.append(f'profile_revision_conflicts_total{{type="{type_}"}} {n}')
        lines.append("# TYPE profile_rules_total gauge")
        for type_, n in sorted(_rules_total.items()):
            lines.append(f'profile_rules_total{{target_type="{type_}"}} {n}')
        lines.append("# TYPE execution_snapshot_bytes summary")
        lines.append(f'execution_snapshot_bytes_sum {sum(_snapshot_bytes)}')
        lines.append(f'execution_snapshot_bytes_count {len(_snapshot_bytes)}')
        lines.append("# TYPE execution_snapshot_create_duration_seconds summary")
        lines.append(f'execution_snapshot_create_duration_seconds_sum {sum(_snapshot_duration_ms) / 1000.0}')
        lines.append(f'execution_snapshot_create_duration_seconds_count {len(_snapshot_duration_ms)}')
        lines.append("# TYPE execution_na_ratio gauge")
        return "\n".join(lines) + "\n"
