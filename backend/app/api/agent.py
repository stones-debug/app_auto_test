from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import reports_dir, settings
from app.core.database import get_db
from app.core.errors import api_error
from app.core.ratelimit import rate_limit, rate_limit_check
from app.core.security import parse_user_agent_key
from app.schemas.agent import AgentBindingOut, BindRequest, BindResponse
from app.services import agent_service
from app.services.screenshot_store import new_screenshot_filename

router = APIRouter(tags=["Agent"])

_ALLOWED_EXTS = {".png", ".jpg", ".jpeg"}
_BIND_KEY_LIMIT_PER_MINUTE = 5


@router.post("/agent/bind", response_model=BindResponse, status_code=status.HTTP_201_CREATED)
async def bind_agent(
    body: BindRequest,
    _rl: None = Depends(rate_limit("bind")),
    db: AsyncSession = Depends(get_db),
):
    parsed = parse_user_agent_key(body.user_key.strip())
    if parsed is not None:
        rate_limit_check("bind", f"pk:{parsed[0]}", _BIND_KEY_LIMIT_PER_MINUTE, 60)
    return await agent_service.bind_agent(db, body)


@router.get("/agent/bindings", response_model=list[AgentBindingOut])
async def list_agent_bindings(
    request: Request,
    agent_id: str,
    _rl: None = Depends(rate_limit("bind")),
    db: AsyncSession = Depends(get_db),
):
    return await agent_service.list_agent_bindings(
        db, key=request.headers.get("x-agent-key") or "", agent_id=agent_id
    )


@router.delete("/agent/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unbind_agent_binding(
    binding_id: int,
    request: Request,
    agent_id: str,
    _rl: None = Depends(rate_limit("bind")),
    db: AsyncSession = Depends(get_db),
):
    await agent_service.unbind_agent_binding(
        db,
        key=request.headers.get("x-agent-key") or "",
        agent_id=agent_id,
        binding_id=binding_id,
        revoke=request.headers.get("x-revoke-credential") or "",
    )


@router.post("/agent/upload")
async def upload_agent_file(
    request: Request,
    file: UploadFile = File(...),
    execution_id: int = Form(...),
    agent_id: str = Form(...),
    session_token: str | None = Form(None),
    file_type: str = Form("screenshot"),
    _rl: None = Depends(rate_limit("upload")),
    db: AsyncSession = Depends(get_db),
):
    """Agent 经 HTTP 上传截图/附件；DB 校验委托给 Agent Service。"""
    del file_type
    agent = await agent_service.verify_agent(
        request.headers.get("x-agent-key") or "", agent_id, db
    )
    await agent_service.verify_execution_binding(db, agent, execution_id, session_token)
    ext = (
        "." + (file.filename or "").rsplit(".", 1)[-1].lower()
        if file.filename and "." in file.filename
        else ""
    )
    if ext not in _ALLOWED_EXTS:
        raise api_error(status.HTTP_400_BAD_REQUEST, "AGENT_UPLOAD_TYPE_INVALID", f"不支持的扩展名: {ext or '无'}")
    content = await file.read()
    if not content:
        raise api_error(status.HTTP_400_BAD_REQUEST, "AGENT_UPLOAD_EMPTY", "文件为空")
    if len(content) > settings.max_screenshot_size:
        raise api_error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "AGENT_UPLOAD_TOO_LARGE",
            f"截图超过大小限制（{settings.max_screenshot_size // 1024 // 1024}MB）",
            {"max_bytes": settings.max_screenshot_size},
        )
    safe_name = new_screenshot_filename(file.filename)
    target_dir = reports_dir() / f"execution_{execution_id}" / "screenshots"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    target.write_bytes(content)
    relative = f"execution_{execution_id}/screenshots/{safe_name}"
    return {"path": relative, "size": len(content), "execution_id": execution_id}
