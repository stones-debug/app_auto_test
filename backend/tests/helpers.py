"""测试共享工具。

Windows 方案 §3.3：执行创建必须指定 device_id，且非管理员用户必须绑定 Agent。
本模块提供"创建绑定到指定测试用户的 Agent+设备"的公共助手。
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_psk
from app.models import Agent, AgentUser, Device, User


async def create_bound_agent_device(
    username: str,
    *,
    agent_status: str = "online",
    device_status: str = "idle",
    stale: bool = False,
) -> tuple[int, int]:
    """创建 agent+device 并绑定到 username 用户，返回 (agent_id, device_id)。"""
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            raise AssertionError(f"用户 {username} 不存在，请先注册")
        agent = Agent(
            agent_key=f"sk-{uuid.uuid4().hex}",
            agent_id=f"pytest_agent_{uuid.uuid4().hex[:8]}",
            hostname="pytest-host",
            status=agent_status,
            last_heartbeat=datetime.now(UTC) - (timedelta(hours=1) if stale else timedelta(seconds=5)),
        )
        db.add(agent)
        await db.flush()
        device = Device(
            agent_id=agent.id,
            name="pytest设备",
            platform="android",
            udid=f"pytest-udid-{uuid.uuid4().hex[:8]}",
            status=device_status,
        )
        db.add(device)
        db.add(
            AgentUser(agent_id=agent.id, user_id=user.id, revoke_credential_hash=hash_psk("test-revoke"))
        )
        await db.commit()
        return agent.id, device.id
