"""WS 处理器兼容导出；实际结果持久化位于 Repository 层。"""

import sys

from app.repositories import ws_handlers as _handlers

__all__ = [name for name in dir(_handlers) if not name.startswith("__")]
for _name in __all__:
    setattr(sys.modules[__name__], _name, getattr(_handlers, _name))
