"""Windows 方案 §3.4：Agent 安装包下载接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.api.deps import get_current_user
from app.models import User
from app.services.release_service import (
    ReleaseNotFound,
    issue_download_token,
    read_manifest,
    resolve_release_file,
    verify_download_token,
)

router = APIRouter(prefix="/agent/releases", tags=["Agent 发布"])


def _to_404(exc: ReleaseNotFound) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/latest")
async def get_latest_release(_user: User = Depends(get_current_user)):
    """最新版元数据（version/filename/sha256/size/published_at）。"""
    try:
        return read_manifest()
    except ReleaseNotFound as exc:
        raise _to_404(exc) from exc


@router.post("/latest/download-token")
async def issue_download_token_endpoint(_user: User = Depends(get_current_user)):
    """生成 5 分钟有效、限定文件名的下载 JWT。"""
    try:
        manifest = read_manifest()
    except ReleaseNotFound as exc:
        raise _to_404(exc) from exc
    return {"token": issue_download_token(manifest["filename"]), "filename": manifest["filename"]}


@router.get("/download/{filename}")
async def download_release(filename: str, token: str = Query(...)):
    """校验令牌与安全文件名后流式下载（FileResponse 支持 Range）。"""
    if not verify_download_token(token, filename):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="下载令牌无效或已过期")
    try:
        path = resolve_release_file(filename)
    except ReleaseNotFound as exc:
        raise _to_404(exc) from exc
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
        content_disposition_type="attachment",
    )
