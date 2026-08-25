"""CR-21：滑动窗口限流（单进程内存版，按 bucket+客户端 IP 计数）。

生产多进程部署时应替换为 Redis 等共享存储；当前满足本地/单进程基线。
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.config import settings

# bucket → deque[monotonic 时间戳]
_buckets: dict[str, deque[float]] = defaultdict(deque)

# bucket 名 → (settings 字段名, 窗口秒数)
_BUCKET_LIMITS: dict[str, tuple[str, int]] = {
    "auth": ("rate_limit_auth_per_minute", 60),
    "upload": ("rate_limit_upload_per_minute", 60),
    "execution": ("rate_limit_execution_per_minute", 60),
    "bind": ("rate_limit_bind_per_minute", 60),
    "preview": ("rate_limit_preview_per_minute", 60),
}


def reset_rate_limits() -> None:
    """清空计数（测试用）。"""
    _buckets.clear()


def _check(bucket: str, key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    q = _buckets[f"{bucket}:{key}"]
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后再试",
        )
    q.append(now)


def rate_limit_check(bucket: str, key: str, limit: int, window_seconds: int = 60) -> None:
    """直接调用式限流（自定义 key，如绑定接口的 public_id 维度）。"""
    _check(bucket, key, limit, window_seconds)


def rate_limit(bucket: str):
    """FastAPI 依赖：按客户端 IP 限流。"""

    def _checker(request: Request) -> None:
        if bucket not in _BUCKET_LIMITS:
            raise ValueError(f"未定义的限流 bucket: {bucket}")
        field, window = _BUCKET_LIMITS[bucket]
        limit = getattr(settings, field)
        key = request.client.host if request.client else "unknown"
        _check(bucket, key, limit, window)

    return _checker
