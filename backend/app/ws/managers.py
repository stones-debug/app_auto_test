from fastapi import WebSocket


class AgentConnectionManager:
    """维护 agent_id → WebSocket 映射（单进程 WS 网关，V1 约束）。"""

    def __init__(self) -> None:
        self._sockets: dict[int, WebSocket] = {}

    async def connect(self, agent_id: int, ws: WebSocket) -> None:
        old = self._sockets.get(agent_id)
        if old is not None:
            try:
                await old.close(code=4001, reason="新连接替换旧连接")
            except Exception:
                pass
        self._sockets[agent_id] = ws

    async def disconnect(self, agent_id: int, ws: WebSocket | None = None) -> bool:
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
        except Exception:
            self._sockets.pop(agent_id, None)
            return False


agent_manager = AgentConnectionManager()


class ExecutionConnectionManager:
    """前端按 execution_id 分组广播（status / log / step_result / completed）。"""

    def __init__(self) -> None:
        self._groups: dict[int, set[WebSocket]] = {}

    async def connect(self, execution_id: int, ws: WebSocket) -> None:
        self._groups.setdefault(execution_id, set()).add(ws)

    async def disconnect(self, execution_id: int, ws: WebSocket) -> None:
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


execution_manager = ExecutionConnectionManager()
