"""Agent、设备和用户绑定用例。

本模块编排业务规则与事务，所有 SQL/ORM 操作均委托给具体 Repository。
"""

import logging
import secrets
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, api_error
from app.core.security import (
    hash_psk,
    parse_user_agent_key,
    verify_psk,
    verify_user_key,
)
from app.models import Agent, User, UserAgentKey
from app.repositories import access as access_repo
from app.repositories import agents as agents_repo
from app.repositories import auth as auth_repo
from app.repositories import devices as devices_repo
from app.repositories import executions as executions_repo
from app.schemas.agent import (
    AgentBindingOut,
    AgentCreate,
    AgentCreateResponse,
    AgentListItem,
    BindRequest,
    BindResponse,
    DefaultDeviceOut,
    DefaultDeviceUpdate,
    DeviceOut,
    DevicePage,
    DeviceReleaseResponse,
)
from app.services import execution_service
from app.utils.pagination import Pagination

logger = logging.getLogger("app.agent_service")

_BIND_KEY_LIMIT_PER_MINUTE = 5


async def verify_agent(key: str, agent_id: str, db: AsyncSession) -> Agent:
    if not key:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "AGENT_KEY_REQUIRED", "缺少 X-Agent-Key")
    agent = await agents_repo.get_by_install_id(db, agent_id) if agent_id else None
    if agent is not None and agent.deleted_at is None and verify_psk(key, agent.agent_key):
        return agent
    raise api_error(status.HTTP_401_UNAUTHORIZED, "AGENT_AUTH_INVALID", "Agent 认证失败")


async def load_user_key(db: AsyncSession, user_key: str) -> UserAgentKey | None:
    parsed = parse_user_agent_key(user_key)
    if parsed is None:
        return None
    public_id, secret = parsed
    row = await auth_repo.get_user_agent_key_by_public_id(db, public_id)
    if row is None or not verify_user_key(secret, row.key_hash):
        return None
    return row


async def bind_agent(db: AsyncSession, body: BindRequest) -> BindResponse:
    user_key = body.user_key.strip()
    parsed = parse_user_agent_key(user_key)
    if parsed is None:
        raise api_error(status.HTTP_400_BAD_REQUEST, "AGENT_USER_KEY_INVALID", "用户 Key 格式无效")
    key_row = await load_user_key(db, user_key)
    if key_row is None:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "AGENT_USER_KEY_INVALID", "用户 Key 无效")
    if not body.install_id or not body.install_id.strip():
        raise api_error(status.HTTP_400_BAD_REQUEST, "AGENT_INSTALL_ID_REQUIRED", "缺少 install_id")

    install_id = body.install_id.strip()
    agent = await agents_repo.get_by_install_id(db, install_id)
    machine_psk: str | None = None
    if body.machine_psk:
        if agent is None or not verify_psk(body.machine_psk, agent.agent_key):
            raise api_error(status.HTTP_401_UNAUTHORIZED, "AGENT_MACHINE_PSK_INVALID", "机器 PSK 无效")
        if agent.deleted_at is not None:
            machine_psk = f"sk-{secrets.token_hex(24)}"
            await agents_repo.reactivate(
                agent,
                agent_key=hash_psk(machine_psk),
                hostname=body.hostname,
                platform=body.platform,
                version=body.version,
            )
    else:
        if agent is not None:
            if agent.deleted_at is not None:
                raise api_error(
                    status.HTTP_409_CONFLICT,
                    "AGENT_REACTIVATION_REQUIRED",
                    "该安装实例已被注销，需要携带原机器 PSK 重新激活；如已丢失请联系平台管理员彻底重置",
                )
            raise api_error(
                status.HTTP_409_CONFLICT,
                "AGENT_ALREADY_EXISTS",
                "该 Agent 已存在，请携带 machine_psk 追加绑定",
            )
        machine_psk = f"sk-{secrets.token_hex(24)}"
        agent = await agents_repo.create(
            db,
            agent_id=install_id,
            agent_key=hash_psk(machine_psk),
            hostname=body.hostname,
            platform=body.platform,
            version=body.version,
        )

    existing = await agents_repo.get_binding(db, agent_id=agent.id, user_id=key_row.user_id)
    if existing is not None:
        raise api_error(status.HTTP_409_CONFLICT, "AGENT_USER_ALREADY_BOUND", "该用户已绑定此 Agent")
    revoke_credential = secrets.token_urlsafe(24)
    await agents_repo.create_binding(
        db,
        agent_id=agent.id,
        user_id=key_row.user_id,
        revoke_credential_hash=hash_psk(revoke_credential),
    )
    await db.commit()
    return BindResponse(
        agent_id=agent.agent_id,
        machine_psk=machine_psk,
        revoke_credential=revoke_credential,
        user_id=key_row.user_id,
    )


async def list_agent_bindings(
    db: AsyncSession, *, key: str, agent_id: str
) -> list[AgentBindingOut]:
    agent = await verify_agent(key, agent_id, db)
    rows = await agents_repo.list_bindings(db, agent.id)
    return [
        AgentBindingOut(id=binding.id, agent_id=agent.id, user_id=binding.user_id, username=username)
        for binding, username in rows
    ]


async def unbind_agent_binding(
    db: AsyncSession, *, key: str, agent_id: str, binding_id: int, revoke: str
) -> None:
    agent = await verify_agent(key, agent_id, db)
    if not revoke:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "AGENT_REVOKE_CREDENTIAL_REQUIRED", "缺少撤销凭据")
    binding = await agents_repo.get_binding_for_agent(
        db, binding_id=binding_id, agent_id=agent.id
    )
    if binding is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "AGENT_BINDING_NOT_FOUND", "绑定不存在")
    if not verify_psk(revoke, binding.revoke_credential_hash):
        raise api_error(status.HTTP_401_UNAUTHORIZED, "AGENT_REVOKE_CREDENTIAL_INVALID", "撤销凭据无效")
    await agents_repo.delete_binding(db, binding)
    await db.commit()


async def verify_execution_binding(
    db: AsyncSession, agent: Agent, execution_id: int, session_token: str | None
) -> None:
    execution = await executions_repo.get_by_id(db, execution_id)
    if execution is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "EXECUTION_NOT_FOUND", "执行不存在")
    device = await executions_repo.get_device_for_execution(db, execution)
    if device is None or device.agent_id != agent.id:
        raise api_error(status.HTTP_403_FORBIDDEN, "AGENT_EXECUTION_FORBIDDEN", "执行未分配给当前 Agent")
    if execution.session_token is not None and session_token != execution.session_token:
        raise api_error(status.HTTP_403_FORBIDDEN, "AGENT_SESSION_INVALID", "session_token 不匹配")
    if execution.status not in ("running", "stopping"):
        raise api_error(status.HTTP_409_CONFLICT, "EXECUTION_RESULT_NOT_ACCEPTED", "执行当前不可接收结果")


async def list_agents(db: AsyncSession, user: User) -> list[AgentListItem]:
    rows = await agents_repo.list_for_user(db, user_id=user.id, is_admin=user.is_admin)
    items = [AgentListItem.model_validate(agent) for agent, _count in rows]
    for item, (_agent, count) in zip(items, rows, strict=True):
        item.device_count = count
    return items


async def create_agent(db: AsyncSession, body: AgentCreate) -> AgentCreateResponse:
    psk = f"sk-{secrets.token_hex(24)}"
    agent = await agents_repo.create(
        db,
        agent_id=f"agent-{secrets.token_hex(4)}",
        agent_key=hash_psk(psk),
        hostname=body.hostname,
        platform=body.platform,
    )
    await db.commit()
    await agents_repo.refresh(db, agent)
    response = AgentCreateResponse.model_validate(agent)
    response.agent_key = psk
    return response


async def delete_agent(db: AsyncSession, agent_id: int) -> None:
    agent = await agents_repo.lock_agent(db, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.AGENT_NOT_FOUND, "Agent 不存在")
    devices = await agents_repo.lock_devices(db, agent_id)
    active_id = await agents_repo.find_active_execution_id(db, [device.id for device in devices])
    if active_id is not None:
        raise api_error(409, "AGENT_HAS_ACTIVE_EXECUTIONS", f"该 Agent 存在活动执行 #{active_id}，请先停止或等待完成后再注销")
    now = datetime.now(UTC)
    await agents_repo.mark_deleted(agent, now)
    for device in devices:
        if device.locked_by_execution is not None:
            logger.warning(
                "注销 Agent %s 时清理陈旧锁: device=%s locked_by_execution=%s",
                agent_id,
                device.id,
                device.locked_by_execution,
            )
    await agents_repo.clear_bindings(db, agent_id)
    await agents_repo.clear_device_preferences(db, [device.id for device in devices])
    await devices_repo.mark_devices_offline(devices, now)
    await db.commit()


async def unbind_my_agent(db: AsyncSession, user: User, agent_id: int) -> None:
    agent = await _require_agent_access(db, agent_id, user)
    binding = await agents_repo.get_binding(db, agent_id=agent.id, user_id=user.id)
    if binding is not None:
        await agents_repo.delete_binding(db, binding)
        await db.commit()


async def _require_agent_access(db: AsyncSession, agent_id: int, user: User) -> Agent:
    agent = await access_repo.get_agent(db, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.AGENT_NOT_FOUND, "Agent 不存在")
    if not user.is_admin and not await access_repo.is_user_bound_to_agent(db, agent_id, user.id):
        raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.AGENT_FORBIDDEN, "无权访问该 Agent")
    return agent


async def list_agent_devices(db: AsyncSession, user: User, agent_id: int) -> list[DeviceOut]:
    await _require_agent_access(db, agent_id, user)
    devices = await devices_repo.list_for_agent(db, agent_id)
    names = await devices_repo.list_with_agent_names(db, devices)
    items = []
    for device in devices:
        item = DeviceOut.model_validate(device)
        item.agent_name = names.get(device.agent_id)
        items.append(item)
    return items


async def list_devices(
    db: AsyncSession, user: User, *, platform: str, status_: str, pagination: Pagination
) -> DevicePage:
    agent_ids = None if user.is_admin else await access_repo.list_bound_agent_ids(db, user.id)
    total, devices = await devices_repo.list_page(
        db,
        agent_ids=agent_ids,
        platform=platform,
        status=status_,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    names = await devices_repo.list_with_agent_names(db, devices)
    items = []
    for device in devices:
        item = DeviceOut.model_validate(device)
        item.agent_name = names.get(device.agent_id)
        items.append(item)
    return DevicePage(total=total, page=pagination.page, page_size=pagination.page_size, items=items)


async def get_default_device(db: AsyncSession, user: User) -> DefaultDeviceOut:
    pref = await devices_repo.get_preference(db, user.id)
    if pref is None:
        return DefaultDeviceOut(device_id=None, reason="未设置默认设备")
    device = await devices_repo.get_by_id(db, pref.device_id)
    if device is None:
        await devices_repo.delete_preference(db, pref)
        await db.commit()
        return DefaultDeviceOut(device_id=None, reason="默认设备已不存在")
    names = await devices_repo.list_with_agent_names(db, [device])
    out = DeviceOut.model_validate(device)
    out.agent_name = names.get(device.agent_id)
    if not user.is_admin and not await access_repo.is_user_bound_to_agent(db, device.agent_id, user.id):
        return DefaultDeviceOut(device_id=device.id, device=out, available=False, reason="无权限")
    agent = await devices_repo.get_agent(db, device.agent_id)
    if agent is None or agent.status != "online":
        return DefaultDeviceOut(device_id=device.id, device=out, available=False, reason="Agent 离线")
    if device.status != "idle" or device.locked_by_execution is not None:
        return DefaultDeviceOut(device_id=device.id, device=out, available=False, reason="设备忙或已被占用")
    return DefaultDeviceOut(device_id=device.id, device=out, available=True, reason="")


async def set_default_device(
    db: AsyncSession, user: User, body: DefaultDeviceUpdate
) -> DefaultDeviceOut:
    pref = await devices_repo.get_preference(db, user.id)
    if body.device_id is None:
        if pref is not None:
            await devices_repo.delete_preference(db, pref)
            await db.commit()
        return DefaultDeviceOut(device_id=None, reason="已清除默认设备")
    device = await _require_device_access(db, body.device_id, user)
    if pref is None:
        await devices_repo.create_preference(db, user_id=user.id, device_id=device.id)
    else:
        await devices_repo.update_preference(pref, device_id=device.id)
    await db.commit()
    names = await devices_repo.list_with_agent_names(db, [device])
    out = DeviceOut.model_validate(device)
    out.agent_name = names.get(device.agent_id)
    return DefaultDeviceOut(device_id=device.id, device=out, reason="已设置默认设备")


async def get_device(db: AsyncSession, user: User, device_id: int) -> DeviceOut:
    device = await _require_device_access(db, device_id, user)
    names = await devices_repo.list_with_agent_names(db, [device])
    out = DeviceOut.model_validate(device)
    out.agent_name = names.get(device.agent_id)
    return out


async def _require_device_access(db: AsyncSession, device_id: int, user: User):
    device = await access_repo.get_device(db, device_id)
    if device is None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.DEVICE_NOT_FOUND, "设备不存在")
    agent = await access_repo.get_device_agent(db, device.agent_id)
    if agent is None or agent.deleted_at is not None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.DEVICE_NOT_FOUND, "设备不存在")
    if not user.is_admin and not await access_repo.is_user_bound_to_agent(db, device.agent_id, user.id):
        raise api_error(status.HTTP_403_FORBIDDEN, ErrorCode.DEVICE_FORBIDDEN, "无权使用该设备")
    return device


async def release_device(db: AsyncSession, device_id: int) -> DeviceReleaseResponse:
    device = await devices_repo.get_locked_for_update(db, device_id)
    if device is None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.DEVICE_NOT_FOUND, "设备不存在")
    now = datetime.now(UTC)
    execution = (
        await devices_repo.get_execution(db, device.locked_by_execution)
        if device.locked_by_execution is not None
        else None
    )

    async def set_available() -> DeviceReleaseResponse:
        agent = await devices_repo.get_agent(db, device.agent_id)
        await devices_repo.release_device(device, available=agent is not None and agent.deleted_at is None, now=now)
        await db.commit()
        await devices_repo.refresh(device, db)
        names = await devices_repo.list_with_agent_names(db, [device])
        out = DeviceOut.model_validate(device)
        out.agent_name = names.get(device.agent_id)
        return DeviceReleaseResponse(action="released", device=out)

    if execution is None:
        if device.locked_by_execution is not None:
            logger.warning(
                "强制释放发现孤儿锁: device=%s locked_by_execution=%s",
                device.id,
                device.locked_by_execution,
            )
        return await set_available()

    current = await devices_repo.get_locked_execution(db, execution.id)
    status_ = current.status if current is not None else execution.status
    if status_ == "queued":
        await devices_repo.cancel_queued_execution(db, execution.id, now)
        return await set_available()
    if status_ == "running":
        try:
            await execution_service.stop_execution(db, current or execution)
        except Exception:
            await db.rollback()
            raise
        await devices_repo.refresh(device, db)
        names = await devices_repo.list_with_agent_names(db, [device])
        out = DeviceOut.model_validate(device)
        out.agent_name = names.get(device.agent_id)
        return DeviceReleaseResponse(action="stop_requested", device=out, execution_id=execution.id, execution_status="stopping")
    if status_ == "stopping":
        await devices_repo.refresh(device, db)
        names = await devices_repo.list_with_agent_names(db, [device])
        out = DeviceOut.model_validate(device)
        out.agent_name = names.get(device.agent_id)
        return DeviceReleaseResponse(action="stop_requested", device=out, execution_id=execution.id, execution_status="stopping")
    if execution.finalized_at is None:
        await devices_repo.refresh(device, db)
        names = await devices_repo.list_with_agent_names(db, [device])
        out = DeviceOut.model_validate(device)
        out.agent_name = names.get(device.agent_id)
        return DeviceReleaseResponse(action="finalization_pending", device=out, execution_id=execution.id, execution_status=status_)
    return await set_available()


async def set_internal_execution_state(
    db: AsyncSession, execution_id: int, status_: str
) -> dict[str, int | str]:
    execution = await executions_repo.get_by_id(db, execution_id)
    if execution is None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.EXECUTION_NOT_FOUND, "执行记录不存在")
    await executions_repo.update_state(execution, status=status_, now=datetime.now(UTC))
    await db.commit()
    return {"execution_id": execution.id, "status": execution.status}


async def forward_to_agent(
    db: AsyncSession, agent_id: int, message: dict
) -> dict[str, int | str | bool]:
    agent = await agents_repo.get_by_id(db, agent_id)
    if agent is None:
        raise api_error(status.HTTP_404_NOT_FOUND, ErrorCode.AGENT_NOT_FOUND, "Agent 不存在")
    from app.ws.managers import agent_manager

    sent = await agent_manager.send(agent_id, message)
    if not sent:
        raise api_error(status.HTTP_409_CONFLICT, ErrorCode.AGENT_OFFLINE, "Agent 不在线")
    return {"sent": True, "agent_id": agent_id, "type": str(message.get("type", ""))}
