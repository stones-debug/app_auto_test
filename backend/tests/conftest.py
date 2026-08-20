import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models import Project, ProjectMember, RefreshToken, User


@pytest.fixture(autouse=True)
async def _cleanup_test_data():
    async with SessionLocal() as session:
        users = (await session.execute(select(User).where(User.username.like("pytest_%")))).scalars().all()
        for user in users:
            project_ids = (
                await session.execute(
                    select(Project.id).where(Project.owner_id == user.id)
                )
            ).scalars().all()
            if project_ids:
                await session.execute(
                    delete(ProjectMember).where(ProjectMember.project_id.in_(project_ids))
                )
                await session.execute(delete(Project).where(Project.id.in_(project_ids)))
            await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))
        await session.commit()
    yield
    from app.core.database import engine

    await engine.dispose()
