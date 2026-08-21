from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.agent import router as agent_router
from app.api.agents import router as agents_router
from app.api.auth import router as auth_router
from app.api.cases import router as cases_router
from app.api.elements import router as elements_router
from app.api.executions import router as executions_router
from app.api.internal import router as internal_router
from app.api.me import router as me_router
from app.api.projects import router as projects_router
from app.api.reports import router as reports_router
from app.api.suites import router as suites_router
from app.api.variables import router as variables_router
from app.core.config import BASE_DIR, settings, validate_security_baseline
from app.ws.routes import router as ws_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # CR-21：生产环境启动前校验安全基线（默认密钥/弱配置直接拒绝启动）
    validate_security_baseline()
    yield


app = FastAPI(
    title="APP 自动化测试平台",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(elements_router, prefix="/api")
app.include_router(cases_router, prefix="/api")
app.include_router(suites_router, prefix="/api")
app.include_router(variables_router, prefix="/api")
app.include_router(executions_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(me_router, prefix="/api")
app.include_router(reports_router, prefix="/api")
app.include_router(internal_router)
app.include_router(ws_router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


# ---------- 前端静态托管（本地部署：单端口 8001 提供页面 + API + WS） ----------
_FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # /api、/ws 由路由处理，未匹配的返回 404 而非回退 index.html
        if full_path.startswith(("api", "ws")):
            raise HTTPException(status_code=404)
        index = _FRONTEND_DIST / "index.html"
        if not index.exists():
            raise HTTPException(status_code=404)
        return FileResponse(index)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info", reload=True)
