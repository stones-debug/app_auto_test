from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.agent import router as agent_router
from app.api.agents import router as agents_router
from app.api.auth import router as auth_router
from app.api.cases import router as cases_router
from app.api.elements import router as elements_router
from app.api.executions import router as executions_router
from app.api.internal import router as internal_router
from app.api.projects import router as projects_router
from app.api.suites import router as suites_router
from app.api.variables import router as variables_router
from app.core.config import settings
from app.ws.routes import router as ws_router

app = FastAPI(
    title="APP 自动化测试平台",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
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
app.include_router(internal_router)
app.include_router(ws_router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info", reload=True)
