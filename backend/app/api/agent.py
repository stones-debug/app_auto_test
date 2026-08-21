from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import reports_dir, settings
from app.core.database import get_db
from app.core.ratelimit import rate_limit
from app.core.security import verify_psk
from app.models import Agent, Device, Execution
from app.services.screenshot_store import new_screenshot_filename

router = APIRouter(tags=["Agent"])

_ALLOWED_EXTS = {".png", ".jpg", ".jpeg"}


async def _verify_agent(key: str, agent_id: str, db: AsyncSession) -> Agent:
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 X-Agent-Key")
    if agent_id:
        agent = (
            await db.execute(select(Agent).where(Agent.agent_id == agent_id))
        ).scalar_one_or_none()
        if agent is not None and verify_psk(key, agent.agent_key):
            return agent
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent 认证失败")


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
