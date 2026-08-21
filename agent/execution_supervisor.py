"""执行运行时管理（CR-06 收敛性重构）。

每个 start_test 对应一个 ExecutionRuntime：取消事件、异步任务与驱动集中在一处，
避免 executions/cancel_events/drivers 多份字典漂移。清理顺序由 AgentApp 固定。
"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class ExecutionRuntime:
    execution_id: int
    session_token: str | None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    driver: object | None = None
