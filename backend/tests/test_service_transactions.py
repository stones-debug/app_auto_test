"""Step 17：Service 事务所有权和失败回滚测试。"""

from unittest.mock import AsyncMock

import pytest

from app.models import Device, Execution, Project, User
from app.schemas.auth import RegisterRequest
from app.schemas.project import ProjectMemberCreate
from app.services import agent_service, auth_service, project_service


@pytest.mark.asyncio
async def test_register_rolls_back_when_refresh_token_creation_fails(monkeypatch):
    db = AsyncMock()
    user = User(id=1, username="new-user", password_hash="hash", status="active")
    monkeypatch.setattr(auth_service.auth_repo, "find_user_by_username_or_email", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_service.auth_repo, "create_user", AsyncMock(return_value=user))
    monkeypatch.setattr(
        auth_service.auth_repo,
        "create_refresh_token",
        AsyncMock(side_effect=RuntimeError("refresh token insert failed")),
    )

    with pytest.raises(RuntimeError, match="refresh token insert failed"):
        await auth_service.register_user(
            db,
            RegisterRequest(username="new-user", password="password123"),
        )

    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_member_write_failure_rolls_back(monkeypatch):
    db = AsyncMock()
    project = Project(id=7, owner_id=1, name="p", visibility="private")
    target = User(id=2, username="member", password_hash="hash", status="active")
    monkeypatch.setattr(project_service.projects_repo, "get_user", AsyncMock(return_value=target))
    monkeypatch.setattr(project_service.projects_repo, "get_member", AsyncMock(return_value=None))
    monkeypatch.setattr(
        project_service.projects_repo,
        "create_member",
        AsyncMock(side_effect=RuntimeError("member insert failed")),
    )

    with pytest.raises(RuntimeError, match="member insert failed"):
        await project_service.add_member(
            db, project, ProjectMemberCreate(user_id=2, role="member")
        )

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_release_failure_rolls_back_without_changing_execution(monkeypatch):
    db = AsyncMock()
    device = Device(id=3, agent_id=4, locked_by_execution=9, status="busy")
    execution = Execution(id=9, device_id=3, status="running", finalized_at=None)
    monkeypatch.setattr(agent_service.devices_repo, "get_locked_for_update", AsyncMock(return_value=device))
    monkeypatch.setattr(agent_service.devices_repo, "get_execution", AsyncMock(return_value=execution))
    monkeypatch.setattr(agent_service.devices_repo, "get_locked_execution", AsyncMock(return_value=execution))
    monkeypatch.setattr(
        agent_service.execution_service,
        "stop_execution",
        AsyncMock(side_effect=RuntimeError("stop transition failed")),
    )

    with pytest.raises(RuntimeError, match="stop transition failed"):
        await agent_service.release_device(db, device.id)

    assert execution.status == "running"
    assert device.locked_by_execution == 9
    db.rollback.assert_awaited_once()
