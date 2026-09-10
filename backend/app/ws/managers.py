import logging
from typing import Any, Protocol

logger = logging.getLogger("app.ws")


def _is_socket_gone(exc: BaseException) -> bool:
    """判断发送失败是否表示连接已关闭/不可写。

    连接关闭后再 send 会抛 Starlette 的 ``RuntimeError``（close 已下发）或
    websockets 的 ``ConnectionClosed*``；这类才应当视为掉线。序列化错误等
    其它异常说明 socket 本身还活着，把它摘掉会让 Agent 被误判 offline，
    必须重连才能恢复。
    """
    if isinstance(exc, (ConnectionError, OSError, RuntimeError)):
        return True
    return exc.__class__.__name__.startswith("ConnectionClosed")


class BroadcastSocket(Protocol):
    """分组广播所需的最小能力；测试替身无需继承 FastAPI WebSocket。

    只声明位置参数 data：真实 WebSocket 的 mode 等参数带默认值，结构匹配时由
    实现侧自行提供，写进协议会迫使替身实现无关参数。
    """

    async def send_json(self, data: Any) -> None: ...


class AgentSocket(BroadcastSocket, Protocol):
    """Agent 单连接额外需要主动关闭旧连接的能力。"""

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class AgentConnectionManager:
    """维护 agent_id → WebSocket 映射（单进程 WS 网关，V1 约束）。"""

    def __init__(self) -> None:
        self._sockets: dict[int, AgentSocket] = {}

    async def connect(self, agent_id: int, ws: AgentSocket) -> None:
        """绑定 agent_id → socket。

        先替换映射再关闭旧连接：旧连接被 close 后其 finally 会调用
        disconnect(agent_id, old)，而此时 current is not old，按 CR-16 语义
        不会误删新绑定；反之（先 close 后绑定）会留下一段"映射指向已关闭 socket"的
        窗口。同一 socket 重复 connect 视为幂等，不再自我关闭。
        """
        old = self._sockets.get(agent_id)
        if old is ws:
            return
        self._sockets[agent_id] = ws
        if old is not None:
            try:
                await old.close(code=4001, reason="新连接替换旧连接")
            except Exception:
                pass

    async def disconnect(self, agent_id: int, ws: AgentSocket | None = None) -> bool:
        """CR-16：仅当当前映射仍为该连接时才删除；返回是否真的移除。"""
        current = self._sockets.get(agent_id)
        if current is None:
            return False
        if ws is not None and current is not ws:
            return False
        self._sockets.pop(agent_id, None)
        return True

    def is_online(self, agent_id: int) -> bool:
        return agent_id in self._sockets

    async def send(self, agent_id: int, message: dict) -> bool:
        ws = self._sockets.get(agent_id)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception as exc:
            if _is_socket_gone(exc):
                self._sockets.pop(agent_id, None)
            logger.warning("向 agent_id=%s 下发消息失败：%s", agent_id, exc)
            return False

    def reset(self) -> None:
        """清空连接注册表（进程关闭与测试隔离用）。"""
        self._sockets.clear()


agent_manager = AgentConnectionManager()


class ExecutionConnectionManager:
    """前端按 execution_id 分组广播（status / log / step_result / completed）。"""

    def __init__(self) -> None:
        self._groups: dict[int, set[BroadcastSocket]] = {}

    async def connect(self, execution_id: int, ws: BroadcastSocket) -> None:
        self._groups.setdefault(execution_id, set()).add(ws)

    async def disconnect(self, execution_id: int, ws: BroadcastSocket) -> None:
        group = self._groups.get(execution_id)
        if group is None:
            return
        group.discard(ws)
        if not group:
            self._groups.pop(execution_id, None)

    async def broadcast(self, execution_id: int, message: dict) -> None:
        group = self._groups.get(execution_id)
        if not group:
            return
        for ws in list(group):
            try:
                await ws.send_json(message)
            except Exception:
                group.discard(ws)
        # CR-24：发送失败移除最后一个 socket 后清理空组（原条件恒为 false）
        if not group:
            self._groups.pop(execution_id, None)

    def reset(self) -> None:
        """清空广播分组（进程关闭与测试隔离用）。"""
        self._groups.clear()


execution_manager = ExecutionConnectionManager()


class ProfileConfigConnectionManager:
    """前端按 project_id 分组的档案配置变更广播（方案 §4.9）。"""

    def __init__(self) -> None:
        self._groups: dict[int, set[BroadcastSocket]] = {}

    async def connect(self, project_id: int, ws: BroadcastSocket) -> None:
        self._groups.setdefault(project_id, set()).add(ws)

    async def disconnect(self, project_id: int, ws: BroadcastSocket) -> None:
        group = self._groups.get(project_id)
        if group is None:
            return
        group.discard(ws)
        if not group:
            self._groups.pop(project_id, None)

    async def broadcast(self, project_id: int, message: dict) -> None:
        group = self._groups.get(project_id)
        if not group:
            return
        for ws in list(group):
            try:
                await ws.send_json(message)
            except Exception:
                group.discard(ws)
        if not group:
            self._groups.pop(project_id, None)

    def reset(self) -> None:
        """清空广播分组（进程关闭与测试隔离用）。"""
        self._groups.clear()


profile_config_manager = ProfileConfigConnectionManager()
