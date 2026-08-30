"""WS 路由兼容导出；连接鉴权和消息落库实现位于 Repository 层。"""

import sys

from app.repositories import ws_routes as _routes

__all__ = [name for name in dir(_routes) if not name.startswith("__")]
for _name in __all__:
    setattr(sys.modules[__name__], _name, getattr(_routes, _name))
