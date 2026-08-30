"""Worker 编排兼容层。

Worker 的队列、设备锁、快照和终态汇总数据库操作位于
``app.repositories.worker``；保留本模块的导出名称，避免运行宿主和既有调用方
在重构期间发生接口漂移。
"""

import sys

from app.repositories import worker as _worker

__all__ = [name for name in dir(_worker) if not name.startswith("__")]
for _name in __all__:
    setattr(sys.modules[__name__], _name, getattr(_worker, _name))
