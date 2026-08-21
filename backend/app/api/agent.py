import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import reports_dir, settings
from app.core.database import get_db
from app.core.ratelimit import rate_limit, rate_limit_check
from app.core.security import hash_psk, parse_user_agent_key, verify_psk, verify_user_key
from app.models import Agent, AgentUser, Device, Execution, User, UserAgentKey
from app.schemas.agent import AgentBindingOut, BindRequest, BindResponse
from app.services.screenshot_store import new_screenshot_filename

router = APIRouter(tags=["Agent"])

_ALLOWED_EXTS = {".png", ".jpg", ".jpeg"}

# Windows 方案 §3.2：同一 public_id 每分钟最多绑定尝试次数（Key 维度限流）
_BIND_KEY_LIMIT_PER_MINUTE = 5


async def _verify_agent(key: str, agent_id: str, db: AsyncSession) -> Agent:
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 X-Agent-Key")
    if agent_id:
        agent = (
            await db.execute(select(Agent).where(Agent.agent_id == agent_id))
        ).scalar_one_or_none()
        if (
            agent is not None
            and agent.deleted_at is None  # Step 6：软注销 Agent 拒绝认证
            and verify_psk(key, agent.agent_key)
        ):
            return agent
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent 认证失败")


async def _load_user_key(db: AsyncSession, user_key: str) -> UserAgentKey | None:
    """解析并校验用户 Key，返回对应记录；非法/无效返回 None。"""
    parsed = parse_user_agent_key(user_key)
    if parsed is None:
        return None
    public_id, secret = parsed
    row = (
        await db.execute(select(UserAgentKey).where(UserAgentKey.public_id == public_id))
    ).scalar_one_or_none()
    if row is None or not verify_user_key(secret, row.key_hash):
        return None
    return row


@router.post("/agent/bind", response_model=BindResponse, status_code=status.HTTP_201_CREATED)
async def bind_agent(
    body: BindRequest,
    _rl: None = Depends(rate_limit("bind")),  # Windows 方案 §3.2：绑定接口按 IP 限流
    db: AsyncSession = Depends(get_db),
):
    """Windows 方案 §3.2：Agent 绑定用户。

    - 首次绑定（machine_psk 为空）：提交用户 Key + install_id，创建 Agent、机器 PSK 与 agent_users；
    - 追加绑定：同时提交机器 PSK 与另一个用户 Key，只新增关联。
    - 机器 PSK 与撤销凭据只返回一次；原始用户 Key 不写日志、不落库。
    """
    user_key = body.user_key.strip()
    public_id = parse_user_agent_key(user_key)
    if public_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户 Key 格式无效")
    # Key 维度限流（IP + public_id 双维度，§3.2）
    rate_limit_check("bind", f"pk:{public_id[0]}", _BIND_KEY_LIMIT_PER_MINUTE, 60)

    key_row = await _load_user_key(db, user_key)
    if key_row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户 Key 无效")
    if not body.install_id or not body.install_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 install_id")

    install_id = body.install_id.strip()
    agent = (
        await db.execute(select(Agent).where(Agent.agent_id == install_id))
    ).scalar_one_or_none()

    machine_psk: str | None = None
    if body.machine_psk:
        # 追加绑定 / 软注销实例重新激活：机器 PSK 认证
        if agent is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="机器 PSK 无效")
        if not verify_psk(body.machine_psk, agent.agent_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="机器 PSK 无效")
        if agent.deleted_at is not None:
            # Step 6：软注销安装实例重新激活——必须携带仍匹配的旧 machine PSK，
            # 旋转 PSK、清空 deleted_at、更新 hostname/platform/version
            machine_psk = f"sk-{secrets.token_hex(24)}"
            agent.agent_key = hash_psk(machine_psk)
            agent.deleted_at = None
            agent.status = "offline"
            if body.hostname:
                agent.hostname = body.hostname
            if body.platform:
                agent.platform = body.platform
            if body.version:
                agent.version = body.version
    else:
        # 首次绑定：创建 Agent 与机器 PSK
        if agent is not None:
            if agent.deleted_at is not None:
                # Step 6：软注销实例无旧 PSK 时拒绝仅凭 install_id 激活，提示管理员彻底重置
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该安装实例已被注销，需要携带原机器 PSK 重新激活；如已丢失请联系平台管理员彻底重置",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该 Agent 已存在，请携带 machine_psk 追加绑定",
            )
        machine_psk = f"sk-{secrets.token_hex(24)}"
        agent = Agent(
            agent_id=install_id,
            agent_key=hash_psk(machine_psk),
            hostname=body.hostname,
            platform=body.platform,
            version=body.version,
            status="offline",
        )
        db.add(agent)
        await db.flush()

    existing = (
        await db.execute(
            select(AgentUser).where(
                AgentUser.agent_id == agent.id,
                AgentUser.user_id == key_row.user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该用户已绑定此 Agent")

    revoke_credential = secrets.token_urlsafe(24)
    db.add(
        AgentUser(
            agent_id=agent.id,
            user_id=key_row.user_id,
            revoke_credential_hash=hash_psk(revoke_credential),
        )
    )
    await db.commit()
    return BindResponse(
        agent_id=agent.agent_id,
        machine_psk=machine_psk,
        revoke_credential=revoke_credential,
        user_id=key_row.user_id,
    )


@router.get("/agent/bindings", response_model=list[AgentBindingOut])
async def list_agent_bindings(
    request: Request,
    agent_id: str,
    _rl: None = Depends(rate_limit("bind")),
    db: AsyncSession = Depends(get_db),
):
    """Windows 方案 §3.2：机器 PSK 认证，返回本机已绑定用户名。"""
    agent = await _verify_agent(request.headers.get("x-agent-key") or "", agent_id, db)
    rows = (
        await db.execute(
            select(AgentUser, User.username)
            .join(User, AgentUser.user_id == User.id)
            .where(AgentUser.agent_id == agent.id)
            .order_by(AgentUser.id)
        )
    ).all()
    return [
        AgentBindingOut(id=au.id, agent_id=agent.id, user_id=au.user_id, username=username)
        for au, username in rows
    ]


@router.delete("/agent/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unbind_agent_binding(
    binding_id: int,
    request: Request,
    agent_id: str,
    _rl: None = Depends(rate_limit("bind")),
    db: AsyncSession = Depends(get_db),
):
    """Windows 方案 §3.2：机器 PSK + 对应撤销凭据解绑。"""
    agent = await _verify_agent(request.headers.get("x-agent-key") or "", agent_id, db)
    revoke = request.headers.get("x-revoke-credential") or ""
    if not revoke:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少撤销凭据")
    binding = (
        await db.execute(
            select(AgentUser).where(
                AgentUser.id == binding_id,
                AgentUser.agent_id == agent.id,
            )
        )
    ).scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="绑定不存在")
    if not verify_psk(revoke, binding.revoke_credential_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="撤销凭据无效")
    await db.delete(binding)
    await db.commit()


async def _verify_execution_binding(
    db: AsyncSession, agent: Agent, execution_id: int, session_token: str | None
) -> None:
    """CR-05：上传必须绑定到分配给当前 Agent 的执行，并校验 session_token。"""
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="执行不存在")
    device = await db.get(Device, execution.device_id) if execution.device_id else None
    if device is None or device.agent_id != agent.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="执行未分配给当前 Agent")
    if execution.session_token is not None and session_token != execution.session_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="session_token 不匹配")
    if execution.status not in ("running", "stopping"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="执行当前不可接收结果")


@router.post("/agent/upload")
async def upload_agent_file(
    request: Request,
    file: UploadFile = File(...),
    execution_id: int = Form(...),
    agent_id: str = Form(...),
    session_token: str | None = Form(None),
    file_type: str = Form("screenshot"),
    _rl: None = Depends(rate_limit("upload")),  # CR-21：上传接口限流
    db: AsyncSession = Depends(get_db),
):
    """Agent 经 HTTP 上传截图/附件（§10.7）。返回相对路径，由 Worker 更新 execution_steps.screenshot_path。"""
    agent = await _verify_agent(request.headers.get("x-agent-key") or "", agent_id, db)
    await _verify_execution_binding(db, agent, execution_id, session_token)
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"不支持的扩展名: {ext or '无'}")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件为空")
    if len(content) > settings.max_screenshot_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"截图超过大小限制（{settings.max_screenshot_size // 1024 // 1024}MB）",
        )

    safe_name = new_screenshot_filename(file.filename)
    target_dir = reports_dir() / f"execution_{execution_id}" / "screenshots"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    target.write_bytes(content)

    relative = f"execution_{execution_id}/screenshots/{safe_name}"
    return {"path": relative, "size": len(content), "execution_id": execution_id}
