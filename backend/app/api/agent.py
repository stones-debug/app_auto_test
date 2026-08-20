from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import reports_dir, settings
from app.core.database import get_db
from app.models import Agent

router = APIRouter(tags=["Agent"])

_agent_key_header = APIKeyHeader(name="X-Agent-Key", auto_error=False)
_ALLOWED_EXTS = {".png", ".jpg", ".jpeg"}


async def _verify_agent_key(
    key: str | None = Depends(_agent_key_header),
    db: AsyncSession = Depends(get_db),
) -> Agent:
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 X-Agent-Key")
    agent = (await db.execute(select(Agent).where(Agent.agent_key == key))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent 认证失败")
    return agent


@router.post("/agent/upload")
async def upload_agent_file(
    file: UploadFile = File(...),
    execution_id: int = Form(...),
    file_type: str = Form("screenshot"),
    agent: Agent = Depends(_verify_agent_key),
):
    """Agent 经 HTTP 上传截图/附件（§10.7）。返回相对路径，由 Worker 更新 execution_steps.screenshot_path。"""
    if file_type != "screenshot":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持 screenshot")
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if file.filename else ""
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

    safe_name = (file.filename or "screenshot.png").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    target_dir = reports_dir() / f"execution_{execution_id}" / "screenshots"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    target.write_bytes(content)

    relative = f"execution_{execution_id}/screenshots/{safe_name}"
    return {"path": relative, "size": len(content), "execution_id": execution_id}
