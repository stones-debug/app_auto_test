import asyncio
from datetime import UTC, datetime
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models import Agent, Device

async def main():
    async with SessionLocal() as db:
        agent = (await db.execute(select(Agent).where(Agent.agent_id == "agent-smoke"))).scalar_one_or_none()
        if agent is None:
            agent = Agent(agent_key="sk-smoke", agent_id="agent-smoke", hostname="pc1", status="online", last_heartbeat=datetime.now(UTC))
            db.add(agent)
            await db.flush()
        else:
            agent.status = "online"
            agent.last_heartbeat = datetime.now(UTC)
        dev = (await db.execute(select(Device).where(Device.udid == "udid-smoke"))).scalar_one_or_none()
        if dev is None:
            dev = Device(agent_id=agent.id, name="M4模拟设备", platform="android", device_type="emulator", udid="udid-smoke", status="idle", capabilities={}, last_heartbeat=datetime.now(UTC))
            db.add(dev)
        else:
            dev.status = "idle"
            dev.locked_by_execution = None
        await db.commit()
        print(f"agent={agent.id} device={dev.id} status={dev.status}")

asyncio.run(main())
